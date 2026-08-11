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
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from lxml import etree

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from docx.text.run import Run as DocxRun


# ── YAML / filename helpers ───────────────────────────────────────────────────

ALLOWED_CATEGORIES = [
    # General (cross-cutting modifiers)
    "Governance", "Regulation", "Survey", "Discussion", "Supply chain",
    "National Interest", "Strategic Autonomy",
    # HTG — High-Tech Geopolitics
    "HTG", "AI", "Quantum", "Space", "Energy", "Semiconductors",
    "Geopolitics", "Emerging Technologies", "Rare Earths",
    "Internet Governance", "Information Warfare",
    # Advanced Biology
    "Advanced Biology", "Synthetic Biology", "Genomics", "Public Health",
    "Biosecurity", "Bioeconomy",
    # Geostrategy
    "Geostrategy", "China", "Pakistan", "Diplomacy", "Partnerships", "PLA",
    "Maritime Security", "Indo-Pacific", "Japan", "United States", "US Congress", "West Asia",
    # Advanced Military Technologies
    "Advanced Military Technologies", "Defence Innovation",
    "Autonomous Weapons Systems", "Cybersecurity", "Military R&D", "Defence",
    # Strategic Studies
    "Strategic Studies", "National Security Strategy",
    "Conflict And Deterrence", "Security Architecture", "Nuclear",
    # Geospatial
    "Geospatial", "Geospatial Infrastructure", "Mapping And Intelligence",
    "Remote Sensing Applications", "Data for Policy",
    # Economic Policy
    "Economic Policy", "Macroeconomic Policy", "Jobs",
    "Trade and Industrial Policy", "Public Finance", "Digital Economy",
    "Geoeconomics", "Economic Freedom", "Economic Reasoning",
]


def _yaml_quote(s: str) -> str:
    """Wrap a string as a YAML double-quoted scalar (handles colons, special chars)."""
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def make_filename(title: str, date: str) -> str:
    """Return canonical YYYYMMDD-kebab-slug filename stem from title and date."""
    date_part = ""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y%m%d", "%d%m%Y"):
        try:
            date_part = datetime.strptime(date.strip(), fmt).strftime("%Y%m%d")
            break
        except (ValueError, AttributeError):
            pass
    if not date_part:
        date_part = datetime.today().strftime("%Y%m%d")

    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug.strip())
    slug = re.sub(r'-+', '-', slug).strip('-')
    if len(slug) > 45:
        slug = slug[:45].rsplit('-', 1)[0]
    return f"{date_part}-{slug or 'document'}"


# ── YAML frontmatter ──────────────────────────────────────────────────────────

def build_frontmatter(meta: dict, pdf_filename: str) -> str:
    """Build the YAML frontmatter block from metadata form fields."""
    # Strip whitespace from all string values to prevent tab/space corruption in YAML
    meta = {k: v.strip() if isinstance(v, str) else v for k, v in meta.items()}
    authors = [a.strip() for a in meta.get("authors", "").split(",") if a.strip()]
    raw_cats = [c.strip() for c in meta.get("categories", "").split(",") if c.strip()]
    invalid = [c for c in raw_cats if c not in ALLOWED_CATEGORIES]
    if invalid:
        print(f"WARNING: unrecognised categories removed: {invalid}", file=sys.stderr)
    categories = [c for c in raw_cats if c in ALLOWED_CATEGORIES]

    lines = ["---"]
    lines.append(f'title: {_yaml_quote(meta["title"])}')
    if meta.get("subtitle"):
        lines.append(f'subtitle: {_yaml_quote(meta["subtitle"])}')
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
    if meta.get("header_title"):
        lines.append(f'header_title: {_yaml_quote(meta["header_title"])}')
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
        # Skip runs with a "Hyperlink" character style — Google Docs emits these
        # as a plain-run duplicate immediately after the <w:hyperlink> element,
        # which would otherwise produce the same URL text twice.
        rPr = r_elem.find(qn("w:rPr"))
        if rPr is not None:
            rStyle = rPr.find(qn("w:rStyle"))
            if rStyle is not None:
                val = (rStyle.get(qn("w:val")) or "").lower()
                if "hyperlink" in val:
                    return

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

    Duplicate-URL prevention: Google Docs often emits a <w:hyperlink> element
    followed immediately by a plain <w:r> run whose text IS the same URL.
    _linkify_bare_urls would then turn that run into a second [Link](url).
    We track every URL (and URL-text) rendered inside a hyperlink and skip any
    subsequent plain run whose stripped text matches one.
    """
    parts: list[str] = []
    # URLs already rendered as [text](url) — plain runs matching these are skipped.
    rendered_urls: set[str] = set()

    def _collect_runs(container) -> str:
        """Concatenate all w:t text inside a container element."""
        return "".join(
            t.text
            for r in container.findall(".//" + qn("w:r"))
            for t in r.findall(qn("w:t"))
            if t.text
        )

    def _handle_hyperlink(elem) -> None:
        r_id = elem.get(qn("r:id"))
        url = rels.get(r_id, "") if r_id else ""
        link_text = _collect_runs(elem).strip()
        if url:
            # Use "Link" when text is empty or is itself a URL
            # (Google Docs auto-hyperlinks typed URLs so text == href)
            display = link_text if (link_text and not link_text.startswith("http")) else "Link"
            parts.append(f"[{display}]({url})")
            rendered_urls.add(url)          # track the href
            rendered_urls.add(link_text)    # track the display text (may also be a URL)
        elif link_text:
            parts.append(link_text)

    for child in p_elem:
        tag = child.tag

        if tag == qn("w:r"):
            # Skip runs with Hyperlink character style — Google Docs adds these
            # as a plain duplicate immediately after the <w:hyperlink> element.
            rPr = child.find(qn("w:rPr"))
            if rPr is not None:
                rStyle = rPr.find(qn("w:rStyle"))
                if rStyle is not None:
                    val = (rStyle.get(qn("w:val")) or "").lower()
                    if "hyperlink" in val:
                        continue
            run_text = "".join(t.text for t in child.findall(qn("w:t")) if t.text)
            # Skip plain runs whose text is a URL we already rendered as a link
            if run_text.strip() in rendered_urls:
                continue
            if run_text:
                parts.append(run_text)

        elif tag == qn("w:hyperlink"):
            _handle_hyperlink(child)

        elif tag == qn("w:ins"):
            # Tracked-change insertion — extract its children normally
            for sub in child:
                if sub.tag == qn("w:r"):
                    run_text = "".join(t.text for t in sub.findall(qn("w:t")) if t.text)
                    if run_text.strip() in rendered_urls:
                        continue
                    if run_text:
                        parts.append(run_text)
                elif sub.tag == qn("w:hyperlink"):
                    _handle_hyperlink(sub)

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
    para_elem_id: int   # id() of the paragraph _p element
    alt_text: str = field(default="")  # from <wp:docPr descr="..."> or title


def _extract_images(doc: Document, img_prefix: str = "img") -> list[ImageRef]:
    """
    Walk all paragraphs and extract embedded images.
    Returns list of ImageRef in document order.
    Keyed by id(para._p) so lookups work regardless of paragraph enumeration order.
    """
    images: list[ImageRef] = []
    img_counter = 0

    for para in doc.paragraphs:
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
            # Extract alt text / description from <wp:docPr descr="...">
            doc_pr = drawing.find(".//" + qn("wp:docPr"))
            alt_text = ""
            if doc_pr is not None:
                alt_text = (
                    doc_pr.get("descr") or doc_pr.get("title") or ""
                ).strip()
            images.append(
                ImageRef(
                    index=img_counter,
                    filename=filename,
                    blob=rel.target_part.blob,
                    para_elem_id=id(para._p),
                    alt_text=alt_text,
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
    # Never treat a heading style as a list (some heading styles carry numbering)
    if "heading" in style_name.lower():
        return None
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

    # Don't misidentify figure/table/box captions as section headings
    if re.match(r'^(figure|fig\.?|table|tbl\.?|box|chart)\s*[\d:]', text, re.IGNORECASE):
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

    # Build a mapping: id(para._p) → list of ImageRef
    para_to_images: dict[int, list[ImageRef]] = {}
    for img in image_refs:
        para_to_images.setdefault(img.para_elem_id, []).append(img)

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

    # 4. Convert body items (paragraphs and tables) in document order
    raw_lines: list[str] = []
    seen_heading = False
    body_items = list(_iter_body_items(doc))
    item_count = len(body_items)
    item_idx = 0

    while item_idx < item_count:
        item_type, item = body_items[item_idx]
        item_idx += 1

        # ── Table ────────────────────────────────────────────────────────────
        if item_type == "table":
            caption = None
            # A paragraph immediately following the table starting with
            # "Table N" / "Table N:" is treated as its caption.
            if item_idx < item_count:
                next_type, next_item = body_items[item_idx]
                if next_type == "para":
                    next_text = next_item.text.strip()
                    if re.match(r'^(table|tbl\.?)\s*[\d:]', next_text, re.IGNORECASE):
                        caption = next_text
                        item_idx += 1  # consume the caption paragraph
            table_lines = _table_to_qmd(item, caption)
            if table_lines:
                raw_lines.append("")
                raw_lines.extend(table_lines)
                raw_lines.append("")
            continue

        # ── Paragraph ────────────────────────────────────────────────────────
        para = item

        # Emit images attached to this paragraph
        img_list = para_to_images.get(id(para._p), [])
        for img_i, img in enumerate(img_list):
            raw_lines.append("")
            alt = img.alt_text
            # For the last image in this paragraph, peek at the next body item:
            # if it's a "Caption"-styled paragraph or starts with "Figure N",
            # use it as the image caption and consume it from the stream.
            if img_i == len(img_list) - 1 and not alt and item_idx < item_count:
                nxt_type, nxt_item = body_items[item_idx]
                if nxt_type == "para":
                    nxt_style = (nxt_item.style.name if nxt_item.style else "")
                    nxt_text = nxt_item.text.strip()
                    if "caption" in nxt_style.lower() or re.match(
                        r'^[Ff]ig(?:ure)?s?\.?\s*[\d:]', nxt_text
                    ):
                        alt = nxt_text
                        item_idx += 1  # consume the caption paragraph
            raw_lines.append(f"![{alt}](images/{img.filename}){{width=100%}}")
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


# ── Body-item iterator ────────────────────────────────────────────────────────

def _iter_body_items(doc: Document):
    """
    Yield ('para', Paragraph) or ('table', Table) for each body-level element
    in document order, skipping all other XML nodes.
    """
    for child in doc.element.body:
        if child.tag == qn("w:p"):
            yield "para", DocxParagraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield "table", DocxTable(child, doc)


def _table_to_qmd(table, caption: Optional[str] = None) -> list[str]:
    """
    Convert a python-docx Table to GFM markdown table lines.
    Merged cells are de-duplicated so content doesn't repeat.
    Returns the table lines, optionally followed by a blank line and
    ': caption text' for use as a Quarto/pandoc table caption.
    """
    rows = table.rows
    if not rows:
        return []

    cell_texts: list[list[str]] = []
    for row in rows:
        seen_cells: set[int] = set()
        row_texts: list[str] = []
        for cell in row.cells:
            cell_id = id(cell._tc)
            if cell_id in seen_cells:
                continue
            seen_cells.add(cell_id)
            text = " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
            text = text.replace("|", "\\|")
            row_texts.append(text)
        cell_texts.append(row_texts)

    col_count = max((len(r) for r in cell_texts), default=0)
    if col_count == 0:
        return []

    for row in cell_texts:
        while len(row) < col_count:
            row.append("")

    lines: list[str] = []
    lines.append("| " + " | ".join(cell_texts[0]) + " |")
    lines.append("|" + "|".join(["---"] * col_count) + "|")
    for row in cell_texts[1:]:
        lines.append("| " + " | ".join(row) + " |")

    if caption:
        lines.append("")
        lines.append(f": {caption}")

    return lines


# ── Blog conversion ───────────────────────────────────────────────────────────

def build_blog_frontmatter(meta: dict, slug: str) -> str:
    """Build minimal YAML frontmatter for a blog post."""
    authors = [a.strip() for a in meta.get("authors", "").split(",") if a.strip()]
    raw_cats = [c.strip() for c in meta.get("categories", "").split(",") if c.strip()]
    invalid = [c for c in raw_cats if c not in ALLOWED_CATEGORIES]
    if invalid:
        print(f"WARNING: unrecognised categories removed: {invalid}", file=sys.stderr)
    categories = [c for c in raw_cats if c in ALLOWED_CATEGORIES]

    lines = ["---"]
    lines.append(f'title: {_yaml_quote(meta["title"])}')
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
        para_to_images.setdefault(img.para_elem_id, []).append(img)

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
    body_items = list(_iter_body_items(doc))
    item_count = len(body_items)
    item_idx = 0

    while item_idx < item_count:
        item_type, item = body_items[item_idx]
        item_idx += 1

        # ── Table ────────────────────────────────────────────────────────────
        if item_type == "table":
            caption = None
            if item_idx < item_count:
                next_type, next_item = body_items[item_idx]
                if next_type == "para":
                    next_text = next_item.text.strip()
                    if re.match(r'^(table|tbl\.?)\s*[\d:]', next_text, re.IGNORECASE):
                        caption = next_text
                        item_idx += 1
            table_lines = _table_to_qmd(item, caption)
            if table_lines:
                raw_lines.append("")
                raw_lines.extend(table_lines)
                raw_lines.append("")
            continue

        # ── Paragraph ────────────────────────────────────────────────────────
        para = item

        for img in para_to_images.get(id(para._p), []):
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
