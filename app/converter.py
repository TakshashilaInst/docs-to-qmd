"""
Convert a DOCX (fetched from Google Docs) to Quarto Markdown (.qmd).

Handles:
- YAML frontmatter from metadata form fields
- Heading styles (Heading 1–4) + heuristic detection of bold short paragraphs
- Bold, italic, bold+italic inline formatting
- Hyperlinks
- Bullet and numbered lists
- Embedded images → images/img_N.png at {width=100%}
- Word footnotes → [^N] placed inline at the exact reference position
- [^N] pass-through (already in QMD format)
- [aside] / [/aside] plain-text tags → :::{.aside} blocks
- Pass-through of existing Quarto syntax (:::, ![, etc.)
"""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from lxml import etree

from docx import Document
from docx.oxml.ns import qn
from docx.text.run import Run as DocxRun


# ── YAML frontmatter ──────────────────────────────────────────────────────────

def build_frontmatter(meta: dict, pdf_filename: str) -> str:
    """Build the YAML frontmatter block from metadata form fields."""
    authors = [a.strip() for a in meta.get("authors", "").split(",") if a.strip()]
    categories = [c.strip() for c in meta.get("categories", "").split(",") if c.strip()]

    lines = ["---"]
    lines.append(f'title: {meta["title"]}')
    if meta.get("subtitle"):
        lines.append(f'subtitle: {meta["subtitle"]}')
    if authors:
        lines.append("author:")
        for a in authors:
            lines.append(f"  - {a}")
    if meta.get("date"):
        lines.append(f'date: "{meta["date"]}"')
    if meta.get("tldr"):
        lines.append(f'tldr: "{meta["tldr"]}"')
    if categories:
        lines.append("categories:")
        for c in categories:
            lines.append(f"  - {c}")
    if meta.get("doctype"):
        lines.append(f"doctype: {meta['doctype']}")
    if meta.get("docversion"):
        lines.append(f"docversion: {meta['docversion']}")
    lines.append("---")

    # Download button div (HTML-only)
    lines.append("")
    lines.append('::: {.content-visible unless-format="pdf"}')
    lines.append("::: {.aside .aside-btn}")
    lines.append(
        f'[Download Document](assets/{pdf_filename}.pdf){{.primary-btn target="_blank"}}'
    )
    lines.append(":::")
    lines.append(":::")

    return "\n".join(lines)


# ── Inline text formatting ─────────────────────────────────────────────────────

def _get_hyperlink_url(run, para) -> Optional[str]:
    """Return the hyperlink URL for a run that is inside a <w:hyperlink>, or None."""
    parent = run._r.getparent()
    if parent is None:
        return None
    if parent.tag == qn("w:hyperlink"):
        r_id = parent.get(qn("r:id"))
        if r_id:
            try:
                return para.part.rels[r_id].target_ref
            except (KeyError, AttributeError):
                pass
    return None


def _format_run(run, para) -> str:
    """Convert a single Run to its markdown representation."""
    text = run.text
    if not text:
        return ""

    url = _get_hyperlink_url(run, para)

    bold = run.bold
    italic = run.italic

    if bold and italic:
        text = f"***{text}***"
    elif bold:
        text = f"**{text}**"
    elif italic:
        text = f"*{text}*"

    if url:
        text = f"[{text}]({url})"

    return text


def _para_to_inline_text(para) -> str:
    """Convert all runs in a paragraph to inline markdown (no footnotes)."""
    parts = []
    for run in para.runs:
        parts.append(_format_run(run, para))
    return "".join(parts)


def _para_to_inline_with_fn(para, get_fn_num) -> str:
    """
    Build inline markdown for a paragraph, placing [^N] footnote markers
    at the EXACT position where they appear in the XML (not appended at end).
    Walks the paragraph XML directly to interleave runs and footnote refs.
    """
    parts = []

    def _handle_run_elem(r_elem):
        # Footnote reference run — no visible text, just a marker
        fn_ref = r_elem.find(qn("w:footnoteReference"))
        if fn_ref is not None:
            wid_str = fn_ref.get(qn("w:id"))
            if wid_str:
                try:
                    wid = int(wid_str)
                    if wid >= 1:
                        parts.append(f"[^{get_fn_num(wid)}]")
                except ValueError:
                    pass
            return
        # If run contains a line break (Shift+Enter in Google Docs), walk
        # children directly so the break is preserved as a paragraph separator.
        if r_elem.find(qn("w:br")) is not None:
            run    = DocxRun(r_elem, para)
            url    = _get_hyperlink_url(run, para)
            bold   = run.bold
            italic = run.italic
            for child in r_elem:
                if child.tag == qn("w:t"):
                    t = child.text or ""
                    if t:
                        if bold and italic: t = f"***{t}***"
                        elif bold:          t = f"**{t}**"
                        elif italic:        t = f"*{t}*"
                        if url:             t = f"[{t}]({url})"
                        parts.append(t)
                elif child.tag == qn("w:br"):
                    if child.get(qn("w:type"), "") != "page":
                        parts.append("\n")
        else:
            # Regular run — wrap in a python-docx Run object to reuse _format_run
            run = DocxRun(r_elem, para)
            parts.append(_format_run(run, para))

    for child in para._p:
        tag = child.tag
        if tag == qn("w:r"):
            _handle_run_elem(child)
        elif tag == qn("w:hyperlink"):
            r_id = child.get(qn("r:id"))
            url = None
            if r_id:
                try:
                    url = para.part.rels[r_id].target_ref
                except (KeyError, AttributeError):
                    pass
            # Collect text from all runs inside the hyperlink
            link_text = ""
            for r_elem in child.findall(qn("w:r")):
                for t in r_elem.findall(qn("w:t")):
                    if t.text:
                        link_text += t.text
            if url:
                # Use "Link" when text is empty or is itself a URL
                display = link_text if (link_text and not link_text.startswith("http")) else "Link"
                parts.append(f"[{display}]({url})")
            elif link_text:
                parts.append(link_text)
        elif tag == qn("w:ins"):
            # Tracked-change insertions — include their runs
            for r_elem in child.findall(qn("w:r")):
                _handle_run_elem(r_elem)

    return "".join(parts)


# ── Footnote extraction ────────────────────────────────────────────────────────

_BARE_URL_RE = re.compile(r'(?<![(\[<"])(https?://[^\s<>"\)\]]+)')


def _linkify_bare_urls(text: str) -> str:
    """Convert bare http(s) URLs to [Link](url) markdown links."""
    return _BARE_URL_RE.sub(r'[Link](\1)', text)


def _fn_para_to_markdown(p_elem, rels: dict[str, str]) -> str:
    """
    Convert a footnote paragraph element to markdown text, preserving hyperlinks.
    Handles w:r (plain runs), w:hyperlink (linked text), and w:ins (tracked inserts).
    """
    parts: list[str] = []

    def _collect_runs(container) -> str:
        """Concatenate all w:t text inside a container element."""
        return "".join(
            t.text
            for r in container.findall(".//" + qn("w:r"))
            for t in r.findall(qn("w:t"))
            if t.text
        )

    for child in p_elem:
        tag = child.tag

        if tag == qn("w:r"):
            for t in child.findall(qn("w:t")):
                if t.text:
                    parts.append(t.text)

        elif tag == qn("w:hyperlink"):
            r_id = child.get(qn("r:id"))
            url = rels.get(r_id, "") if r_id else ""
            link_text = _collect_runs(child).strip()
            if url:
                # Use "Link" when text is empty or is itself a URL
                # (Google Docs auto-hyperlinks typed URLs so text == href)
                display = link_text if (link_text and not link_text.startswith("http")) else "Link"
                parts.append(f"[{display}]({url})")
            elif link_text:
                parts.append(link_text)

        elif tag == qn("w:ins"):
            # Tracked-change insertion — extract its children normally
            for sub in child:
                if sub.tag == qn("w:r"):
                    for t in sub.findall(qn("w:t")):
                        if t.text:
                            parts.append(t.text)
                elif sub.tag == qn("w:hyperlink"):
                    r_id = sub.get(qn("r:id"))
                    url = rels.get(r_id, "") if r_id else ""
                    link_text = _collect_runs(sub).strip()
                    if url:
                        display = link_text if (link_text and not link_text.startswith("http")) else "Link"
                        parts.append(f"[{display}]({url})")
                    elif link_text:
                        parts.append(link_text)

    return "".join(parts).strip()


def _extract_footnotes_from_bytes(docx_bytes: bytes) -> dict[int, str]:
    """
    Extract Word footnote text by reading word/footnotes.xml directly
    from the DOCX zip.  Bypasses python-docx relationship lookup entirely.
    Returns {footnote_id: markdown_text} with hyperlinks rendered as [text](url).
    """
    footnotes: dict[int, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
            if "word/footnotes.xml" not in z.namelist():
                return footnotes

            # Load footnote-part relationships so hyperlink URLs can be resolved
            fn_rels: dict[str, str] = {}
            rels_path = "word/_rels/footnotes.xml.rels"
            if rels_path in z.namelist():
                with z.open(rels_path) as rf:
                    rels_root = etree.parse(rf).getroot()
                    for rel in rels_root:
                        r_id = rel.get("Id")
                        target = rel.get("Target")
                        if r_id and target:
                            fn_rels[r_id] = target

            with z.open("word/footnotes.xml") as f:
                fn_elem = etree.parse(f).getroot()
    except Exception:
        return footnotes

    for fn in fn_elem.findall(qn("w:footnote")):
        fn_id_str = fn.get(qn("w:id"))
        if fn_id_str is None:
            continue
        try:
            fn_id = int(fn_id_str)
        except ValueError:
            continue
        if fn_id < 1:  # skip separator/continuation footnotes (ids -1, 0)
            continue
        text_parts = []
        for p in fn.findall(qn("w:p")):
            para_text = _fn_para_to_markdown(p, fn_rels)
            if para_text:
                text_parts.append(para_text)
        footnotes[fn_id] = _linkify_bare_urls(" ".join(text_parts))
    return footnotes


def _extract_footnotes(doc: Document) -> dict[int, str]:
    """Fallback footnote extraction via python-docx (used when raw bytes unavailable)."""
    footnotes: dict[int, str] = {}
    fn_part = None
    try:
        fn_part = doc.part.footnotes_part
    except Exception:
        pass
    if fn_part is None:
        try:
            for rel in doc.part.rels.values():
                if hasattr(rel, "reltype") and "footnote" in rel.reltype.lower():
                    fn_part = rel.target_part
                    break
        except Exception:
            pass
    if fn_part is None:
        return footnotes

    fn_elem = fn_part._element

    # Build relationship map for URL resolution
    fn_rels: dict[str, str] = {}
    try:
        for r_id, rel in fn_part.rels.items():
            if hasattr(rel, "target_ref"):
                fn_rels[r_id] = rel.target_ref
    except Exception:
        pass

    for fn in fn_elem.findall(qn("w:footnote")):
        fn_id_str = fn.get(qn("w:id"))
        if fn_id_str is None:
            continue
        try:
            fn_id = int(fn_id_str)
        except ValueError:
            continue
        if fn_id < 1:
            continue
        text_parts = []
        for p in fn.findall(qn("w:p")):
            para_text = _fn_para_to_markdown(p, fn_rels)
            if para_text:
                text_parts.append(para_text)
        footnotes[fn_id] = _linkify_bare_urls(" ".join(text_parts))
    return footnotes


# ── Image extraction ───────────────────────────────────────────────────────────

def _image_prefix(pdf_filename: str) -> str:
    """
    Derive a short image prefix from the pdf_filename.
    Strips a trailing date pattern and lowercases the result.
      'GAGEChina-30032026'      → 'gagechina'
      'EU-Rearm-India-09032026' → 'eu_rearm_india'
    """
    stem = re.sub(r"[-_]\d{6,8}$", "", pdf_filename)
    return stem.lower().replace("-", "_").replace(" ", "_")


@dataclass
class ImageRef:
    index: int
    filename: str       # e.g. "gagechina_1.png"
    blob: bytes
    para_index: int     # paragraph index where the image appears


def _extract_images(doc: Document, img_prefix: str = "img") -> list[ImageRef]:
    """
    Walk all paragraphs and extract embedded images.
    Returns list of ImageRef in document order.
    """
    images: list[ImageRef] = []
    img_counter = 0

    for para_idx, para in enumerate(doc.paragraphs):
        drawings = para._p.findall(".//" + qn("w:drawing"))
        for drawing in drawings:
            blip = drawing.find(".//" + qn("a:blip"))
            if blip is None:
                continue
            r_embed = blip.get(qn("r:embed"))
            if not r_embed:
                continue
            try:
                rel = para.part.rels[r_embed]
            except KeyError:
                continue
            if "image" not in rel.reltype:
                continue
            img_counter += 1
            ext = Path(rel.target_ref).suffix or ".png"
            filename = f"{img_prefix}_{img_counter}{ext}"
            images.append(
                ImageRef(
                    index=img_counter,
                    filename=filename,
                    blob=rel.target_part.blob,
                    para_index=para_idx,
                )
            )
    return images


# ── Paragraph-level processing ────────────────────────────────────────────────

HEADING_MAP = {
    "Heading 1": "#",
    "Heading 2": "##",
    "Heading 3": "###",
    "Heading 4": "####",
    # Google Docs sometimes exports with these names
    "heading 1": "#",
    "heading 2": "##",
    "heading 3": "###",
    "heading 4": "####",
}

# Paragraph styles that are title/author metadata — skip them (already in YAML)
SKIP_STYLES = {"Title", "Subtitle", "Author", "title", "subtitle", "author"}

# Quarto/Markdown syntax that should be passed through verbatim
PASSTHROUGH_PREFIXES = (":::", "[^", "---", "<!-- ")


def _strip_emphasis(text: str) -> str:
    """Remove bold/italic markdown markers (**/**/*) from a string."""
    return re.sub(r"\*+", "", text).strip()


def _extract_literal_heading(text: str) -> Optional[tuple[str, str]]:
    """
    If text is a literal markdown heading like '# Foo' or '## Bar',
    return (prefix, clean_heading_text). Otherwise None.
    Strips bold/italic markers from the heading text.
    """
    stripped = text.strip().lstrip("*").rstrip("*").strip()
    m = re.match(r"^(#{1,4})\s+(.+)$", stripped)
    if m:
        heading_text = _strip_emphasis(m.group(2))
        return m.group(1), heading_text
    return None


def _is_passthrough(text: str) -> bool:
    return any(text.startswith(p) for p in PASSTHROUGH_PREFIXES)


def _get_list_marker(para) -> Optional[str]:
    """Return '- ' for bullet lists or '1. ' for numbered lists, else None."""
    style_name = para.style.name if para.style else ""
    if "List Bullet" in style_name:
        return "- "
    if "List Number" in style_name:
        return "1. "
    pPr = para._p.find(qn("w:pPr"))
    if pPr is not None:
        numPr = pPr.find(qn("w:numPr"))
        if numPr is not None:
            numId = numPr.find(qn("w:numId"))
            if numId is not None and numId.get(qn("w:val")) not in ("0", None):
                return "- "
    return None


def _is_implicit_heading(para) -> bool:
    """
    Heuristically detect paragraphs that look like headings but use 'Normal'
    style in Google Docs (e.g. short bold lines used as section titles).

    Criteria:
    - Not already a recognised heading or skip style
    - Short text (≤ 10 words)
    - Every run that contains text is bold
    - Not a list item
    """
    style_name = para.style.name if para.style else "Normal"
    if style_name in HEADING_MAP or style_name in SKIP_STYLES:
        return False

    text = para.text.strip()
    if not text:
        return False

    # Must be short
    if len(text.split()) > 10:
        return False

    # Every non-empty run must be bold
    runs_with_text = [r for r in para.runs if r.text.strip()]
    if not runs_with_text:
        return False
    if not all(r.bold for r in runs_with_text):
        return False

    # Must not be a list item
    if _get_list_marker(para):
        return False

    return True


# ── Main conversion ───────────────────────────────────────────────────────────

def convert(
    doc: Document,
    meta: dict,
    pdf_filename: str,
    images_dir: Path,
    docx_bytes: Optional[bytes] = None,
) -> str:
    """
    Convert a python-docx Document to QMD string.
    Extracted images are saved into images_dir.
    Pass docx_bytes (raw DOCX file bytes) for reliable footnote extraction.
    Returns the full QMD content as a string.
    """
    # 1. Extract footnotes and images up-front
    if docx_bytes is not None:
        word_footnotes = _extract_footnotes_from_bytes(docx_bytes)
    else:
        word_footnotes = _extract_footnotes(doc)

    img_prefix = _image_prefix(pdf_filename)
    image_refs = _extract_images(doc, img_prefix)

    # Save images to disk
    for img in image_refs:
        dest = images_dir / img.filename
        dest.write_bytes(img.blob)

    # Build a mapping: para_index → list of ImageRef
    para_to_images: dict[int, list[ImageRef]] = {}
    for img in image_refs:
        para_to_images.setdefault(img.para_index, []).append(img)

    # 2. Footnote counter — shared state accessed via closure
    fn_map: dict[int, int] = {}   # word_fn_id → sequential [^N] number
    fn_counter = [0]

    def get_fn_num(word_id: int) -> int:
        if word_id not in fn_map:
            fn_counter[0] += 1
            fn_map[word_id] = fn_counter[0]
        return fn_map[word_id]

    # 3. Build a set of metadata strings to skip at the top of the document
    authors_list = [a.strip() for a in meta.get("authors", "").split(",") if a.strip()]
    skip_exact = {meta.get("title", "").strip(), meta.get("subtitle", "").strip()}
    skip_exact.update(authors_list)
    skip_exact.discard("")

    # 4. Convert paragraphs
    raw_lines: list[str] = []
    seen_heading = False

    for para_idx, para in enumerate(doc.paragraphs):

        # ── Emit images attached to this paragraph ──────────────────────────
        for img in para_to_images.get(para_idx, []):
            raw_lines.append("")
            raw_lines.append(f"![](images/{img.filename}){{width=100%}}")
            raw_lines.append("")

        style_name = para.style.name if para.style else "Normal"
        raw_text = para.text
        stripped = raw_text.strip()

        if not stripped:
            raw_lines.append("")
            continue

        # ── Skip title/author/subtitle (already in YAML frontmatter) ────────
        if style_name in SKIP_STYLES and not seen_heading:
            continue
        if stripped in skip_exact and not seen_heading:
            continue

        # ── Pass-through Quarto syntax ───────────────────────────────────────
        if _is_passthrough(stripped):
            raw_lines.append(stripped)
            continue

        # ── Pass-through image markdown — ensure {width=100%} ────────────────
        if stripped.startswith("!["):
            if "{width" not in stripped and "{}" not in stripped:
                # Strip any existing size attr and add standard one
                stripped = re.sub(r"\{[^}]*\}\s*$", "", stripped).rstrip()
                stripped = stripped + "{width=100%}"
            raw_lines.append(stripped)
            continue

        # ── Headings via Word/Google Docs heading styles ─────────────────────
        heading_prefix = HEADING_MAP.get(style_name)
        if heading_prefix:
            seen_heading = True
            inline = _para_to_inline_text(para)
            clean_heading = _strip_emphasis(inline)
            raw_lines.append(f"{heading_prefix} {clean_heading}")
            raw_lines.append("")
            continue

        # ── Headings written as literal markdown (e.g. "# Section 1") ────────
        literal_heading = _extract_literal_heading(stripped)
        if literal_heading:
            seen_heading = True
            prefix, heading_text = literal_heading
            raw_lines.append(f"{prefix} {heading_text}")
            raw_lines.append("")
            continue

        # ── Heuristic heading: short all-bold Normal paragraph ───────────────
        if _is_implicit_heading(para):
            seen_heading = True
            clean_heading = _strip_emphasis(_para_to_inline_text(para))
            raw_lines.append(f"## {clean_heading}")
            raw_lines.append("")
            continue

        # ── Lists ────────────────────────────────────────────────────────────
        list_marker = _get_list_marker(para)

        # ── Build inline markdown with footnote markers in correct positions ──
        inline = _para_to_inline_with_fn(para, get_fn_num)

        # Split on \n emitted by soft line-breaks (Shift+Enter in Google Docs)
        # so each visual line becomes its own markdown paragraph.
        segments = [s.strip() for s in inline.split("\n") if s.strip()]
        if not segments:
            raw_lines.append("")
        elif list_marker:
            for s in segments:
                raw_lines.append(list_marker + s)
        else:
            for s in segments:
                raw_lines.append(s)
                raw_lines.append("")

    # 5. Process [aside] / [/aside] blocks, then normalise blank lines
    processed_lines = _normalize_blank_lines(_process_asides(raw_lines))

    # 6. Append footnote definitions
    footnote_defs: list[str] = []
    if fn_map:
        footnote_defs.append("")
        for word_id, n in sorted(fn_map.items(), key=lambda x: x[1]):
            fn_text = word_footnotes.get(word_id, "")
            footnote_defs.append(f"[^{n}]: {fn_text}")

    # 7. Assemble
    frontmatter = build_frontmatter(meta, pdf_filename)
    body = "\n".join(processed_lines)
    fn_block = "\n".join(footnote_defs)

    parts = [frontmatter, "", body]
    if fn_block.strip():
        parts.append(fn_block)

    return "\n".join(parts)


# ── Blank-line normalisation ─────────────────────────────────────────────────

def _normalize_blank_lines(lines: list[str]) -> list[str]:
    """Collapse runs of 3+ consecutive blank lines down to a single blank line."""
    result: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 1:
                result.append(line)
        else:
            blank_run = 0
            result.append(line)
    return result


# ── Aside processing ──────────────────────────────────────────────────────────

def _process_asides(lines: list[str]) -> list[str]:
    """
    Scan lines for [aside] ... [/aside] markers and wrap them in
    :::{.aside} ... ::: blocks.

    Handles:
    - [aside] on its own line
    - [aside] at the start of a line (rest of line is inside the aside)
    - [/aside] on its own line
    - [/aside] at the end of a line
    - Already-correct :::{.aside} syntax is left untouched
    """
    result: list[str] = []
    inside_aside = False

    for line in lines:
        lower = line.lower()

        if "[aside]" in lower and "[/aside]" in lower:
            content = re.sub(r"\[aside\]", "", line, flags=re.IGNORECASE)
            content = re.sub(r"\[/aside\]", "", content, flags=re.IGNORECASE).strip()
            result.append("")
            result.append(":::{.aside}")
            if content:
                result.append(content)
            result.append(":::")
            result.append("")
            continue

        if "[aside]" in lower:
            inside_aside = True
            suffix = re.sub(r".*\[aside\]", "", line, flags=re.IGNORECASE).strip()
            result.append("")
            result.append(":::{.aside}")
            if suffix:
                result.append(suffix)
            continue

        if "[/aside]" in lower:
            suffix = re.sub(r"\[/aside\].*", "", line, flags=re.IGNORECASE).strip()
            if suffix:
                result.append(suffix)
            result.append(":::")
            result.append("")
            inside_aside = False
            continue

        result.append(line)

    if inside_aside:
        result.append(":::")
        result.append("")

    return result


# ── Blog conversion ───────────────────────────────────────────────────────────

def build_blog_frontmatter(meta: dict, slug: str) -> str:
    """Build minimal YAML frontmatter for a blog post."""
    authors = [a.strip() for a in meta.get("authors", "").split(",") if a.strip()]
    categories = [c.strip() for c in meta.get("categories", "").split(",") if c.strip()]

    lines = ["---"]
    lines.append(f'title: {meta["title"]}')
    if authors:
        lines.append("author:")
        for a in authors:
            lines.append(f"  - {a}")
    if meta.get("date"):
        lines.append(f'date: "{meta["date"]}"')
    if categories:
        lines.append("categories:")
        for c in categories:
            lines.append(f"  - {c}")
    lines.append("---")
    return "\n".join(lines)


def convert_blog(
    doc: Document,
    meta: dict,
    slug: str,
    images_dir: Path,
    docx_bytes: Optional[bytes] = None,
) -> str:
    """
    Convert a python-docx Document to a blog QMD string (no PDF template,
    no download button, no asides).  Extracted images saved into images_dir.
    """
    if docx_bytes is not None:
        word_footnotes = _extract_footnotes_from_bytes(docx_bytes)
    else:
        word_footnotes = _extract_footnotes(doc)

    img_prefix = slug
    image_refs = _extract_images(doc, img_prefix)
    for img in image_refs:
        (images_dir / img.filename).write_bytes(img.blob)

    para_to_images: dict[int, list[ImageRef]] = {}
    for img in image_refs:
        para_to_images.setdefault(img.para_index, []).append(img)

    fn_map: dict[int, int] = {}
    fn_counter = [0]

    def get_fn_num(word_id: int) -> int:
        if word_id not in fn_map:
            fn_counter[0] += 1
            fn_map[word_id] = fn_counter[0]
        return fn_map[word_id]

    authors_list = [a.strip() for a in meta.get("authors", "").split(",") if a.strip()]
    skip_exact = {meta.get("title", "").strip()}
    skip_exact.update(authors_list)
    skip_exact.discard("")

    raw_lines: list[str] = []
    seen_heading = False

    for para_idx, para in enumerate(doc.paragraphs):
        for img in para_to_images.get(para_idx, []):
            raw_lines.append("")
            raw_lines.append(f"![](images/{img.filename}){{width=100%}}")
            raw_lines.append("")

        style_name = para.style.name if para.style else "Normal"
        text = para.text.strip()

        if not text:
            raw_lines.append("")
            continue

        if text in skip_exact and not seen_heading:
            continue

        if _is_passthrough(text):
            raw_lines.append(text)
            continue

        if _is_implicit_heading(para):
            seen_heading = True
            raw_lines.append(f"## {text}")
            continue

        if style_name in HEADING_MAP:
            seen_heading = True
            hdr = _extract_literal_heading(text)
            if hdr:
                lvl_prefix, heading_text = hdr
                raw_lines.append(f"{lvl_prefix} {heading_text}")
            else:
                prefix = HEADING_MAP[style_name]
                inline = _para_to_inline_with_fn(para, get_fn_num)
                raw_lines.append(f"{prefix} {_strip_emphasis(inline)}")
            continue

        if style_name in SKIP_STYLES:
            continue

        list_marker = _get_list_marker(para)
        if list_marker:
            inline = _para_to_inline_with_fn(para, get_fn_num)
            raw_lines.append(f"{list_marker}{inline}")
            continue

        inline = _para_to_inline_with_fn(para, get_fn_num)
        segments = [s.strip() for s in inline.split("\n") if s.strip()]
        if not segments:
            raw_lines.append("")
        else:
            for s in segments:
                raw_lines.append(s)
                raw_lines.append("")

    # Footnote block
    fn_block_lines: list[str] = []
    if fn_map:
        fn_block_lines.append("")
        for word_id, seq_num in sorted(fn_map.items(), key=lambda x: x[1]):
            fn_text = word_footnotes.get(word_id, "")
            fn_block_lines.append(f"[^{seq_num}]: {fn_text}")

    processed = _normalize_blank_lines(_process_asides(raw_lines))
    body = "\n".join(processed).strip()
    fn_block = "\n".join(fn_block_lines)

    frontmatter = build_blog_frontmatter(meta, slug)
    parts = [frontmatter, "", body]
    if fn_block.strip():
        parts.append(fn_block)

    return "\n".join(parts)
