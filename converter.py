"""
Convert a DOCX (fetched from Google Docs) to Quarto Markdown (.qmd).

Handles:
- YAML frontmatter from metadata form fields
- Heading styles (Heading 1–4)
- Bold, italic, bold+italic inline formatting
- Hyperlinks
- Bullet and numbered lists
- Embedded images → images/img_N.png with captions
- Word footnotes → [^N] format
- [^N] pass-through (already in QMD format)
- [aside] / [/aside] plain-text tags → :::{.aside} blocks
- Pass-through of existing Quarto syntax (:::, ![, etc.)
"""

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from lxml import etree

from docx import Document
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT


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
    """Convert all runs in a paragraph to inline markdown."""
    parts = []
    for run in para.runs:
        parts.append(_format_run(run, para))
    return "".join(parts)


# ── Footnote extraction ────────────────────────────────────────────────────────

def _extract_footnotes(doc: Document) -> dict[int, str]:
    """
    Extract Word footnote text keyed by footnote ID.
    Returns {footnote_id: markdown_text}.
    """
    footnotes: dict[int, str] = {}
    try:
        fn_part = doc.part.footnotes_part
        if fn_part is None:
            return footnotes
        fn_elem = fn_part._element
    except AttributeError:
        return footnotes

    for fn in fn_elem.findall(qn("w:footnote")):
        fn_id_str = fn.get(qn("w:id"))
        if fn_id_str is None:
            continue
        fn_id = int(fn_id_str)
        if fn_id < 1:  # skip separator/continuation footnotes (ids 0, -1)
            continue
        # Collect text from all paragraphs in the footnote
        text_parts = []
        for p in fn.findall(qn("w:p")):
            run_texts = []
            for r in p.findall(".//" + qn("w:r")):
                for t in r.findall(qn("w:t")):
                    run_texts.append(t.text or "")
            text_parts.append("".join(run_texts).strip())
        footnotes[fn_id] = " ".join(t for t in text_parts if t)
    return footnotes


# ── Image extraction ───────────────────────────────────────────────────────────

@dataclass
class ImageRef:
    index: int
    filename: str  # e.g. "img_1.png"
    blob: bytes
    para_index: int  # paragraph index where the image appears


def _extract_images(doc: Document) -> list[ImageRef]:
    """
    Walk all paragraphs and extract embedded images.
    Returns list of ImageRef in document order.
    """
    images: list[ImageRef] = []
    img_counter = 0
    body = doc.element.body

    for para_idx, para in enumerate(doc.paragraphs):
        # Look for <w:drawing> elements in this paragraph's XML
        drawings = para._p.findall(".//" + qn("w:drawing"))
        for drawing in drawings:
            # Find the blipFill relationship id
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
            filename = f"img_{img_counter}{ext}"
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
PASSTHROUGH_PREFIXES = (":::", "[^", "![", "---", "<!-- ")


def _extract_literal_heading(text: str) -> Optional[tuple[str, str]]:
    """
    If text is a literal markdown heading like '# Foo' or '## Bar',
    return (prefix, rest_of_text). Otherwise None.
    Handles cases where the '#' might have been wrapped in bold by the converter.
    """
    # Strip leading/trailing bold markers that may wrap the whole heading
    stripped = text.strip().lstrip("*").rstrip("*").strip()
    m = re.match(r"^(#{1,4})\s+(.+)$", stripped)
    if m:
        return m.group(1), m.group(2).strip()
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
    # Check numPr for outline-level lists
    pPr = para._p.find(qn("w:pPr"))
    if pPr is not None:
        numPr = pPr.find(qn("w:numPr"))
        if numPr is not None:
            numId = numPr.find(qn("w:numId"))
            ilvl = numPr.find(qn("w:ilvl"))
            if numId is not None and numId.get(qn("w:val")) not in ("0", None):
                # Try to determine bullet vs numbered from the numbering definition
                # Default to bullet if we can't tell
                return "- "
    return None


def _footnote_ref_ids_in_para(para) -> list[int]:
    """Return the Word footnote IDs referenced in this paragraph (in order)."""
    ids = []
    for fn_ref in para._p.findall(".//" + qn("w:footnoteReference")):
        val = fn_ref.get(qn("w:id"))
        if val is not None:
            ids.append(int(val))
    return ids


# ── Main conversion ───────────────────────────────────────────────────────────

def convert(doc: Document, meta: dict, pdf_filename: str, images_dir: Path) -> str:
    """
    Convert a python-docx Document to QMD string.
    Extracted images are saved into images_dir.
    Returns the full QMD content as a string.
    """
    # 1. Extract footnotes and images up-front
    word_footnotes = _extract_footnotes(doc)
    image_refs = _extract_images(doc)

    # Save images to disk
    for img in image_refs:
        dest = images_dir / img.filename
        dest.write_bytes(img.blob)

    # Build a mapping: para_index → list of ImageRef
    para_to_images: dict[int, list[ImageRef]] = {}
    for img in image_refs:
        para_to_images.setdefault(img.para_index, []).append(img)

    # 2. Build a running footnote counter for Word-style footnotes
    #    (pass-through [^N] in text already carries its own numbering)
    fn_map: dict[int, int] = {}  # word_fn_id → sequential [^N] number
    fn_counter = [0]

    def get_fn_num(word_id: int) -> int:
        if word_id not in fn_map:
            fn_counter[0] += 1
            fn_map[word_id] = fn_counter[0]
        return fn_map[word_id]

    # 3. Build a set of metadata strings to skip at the top of the document
    #    (Google Docs exports title/subtitle/author as plain "normal" paragraphs)
    authors_list = [a.strip() for a in meta.get("authors", "").split(",") if a.strip()]
    skip_exact = {meta.get("title", "").strip(), meta.get("subtitle", "").strip()}
    skip_exact.update(authors_list)
    skip_exact.discard("")

    # 3. Convert paragraphs to raw markdown lines (before aside processing)
    raw_lines: list[str] = []
    # Track whether we've passed the first heading (after which we stop skipping metadata)
    seen_heading = False

    for para_idx, para in enumerate(doc.paragraphs):
        # Emit any images that appear in this paragraph first
        for img in para_to_images.get(para_idx, []):
            raw_lines.append(f"![](images/{img.filename}){{width=100%}}")
            raw_lines.append("")

        style_name = para.style.name if para.style else "Normal"
        raw_text = para.text  # plain text for pass-through detection
        stripped = raw_text.strip()

        if not stripped:
            raw_lines.append("")
            continue

        # --- Skip title/author/subtitle paragraphs (already in YAML frontmatter) ---
        # Only skip before the first heading to avoid skipping legitimately repeated text
        if style_name in SKIP_STYLES and not seen_heading:
            continue
        if stripped in skip_exact and not seen_heading:
            continue

        # --- Pass-through Quarto syntax ---
        if _is_passthrough(stripped):
            raw_lines.append(stripped)
            continue

        # --- Headings via Word/Google Docs heading styles ---
        heading_prefix = HEADING_MAP.get(style_name)
        if heading_prefix:
            seen_heading = True
            inline = _para_to_inline_text(para)
            raw_lines.append(f"{heading_prefix} {inline.strip()}")
            raw_lines.append("")
            continue

        # --- Headings written as literal markdown (e.g. "# Section 1" in body text) ---
        literal_heading = _extract_literal_heading(stripped)
        if literal_heading:
            seen_heading = True
            prefix, heading_text = literal_heading
            raw_lines.append(f"{prefix} {heading_text}")
            raw_lines.append("")
            continue

        # --- Lists ---
        list_marker = _get_list_marker(para)

        # --- Build inline markdown for this paragraph ---
        inline = _para_to_inline_text(para)

        # --- Inject Word footnote references ---
        word_fn_ids = _footnote_ref_ids_in_para(para)
        # Replace the first occurrence of each footnote separator character.
        # Word puts a special unicode char (U+0002) or just places the ref;
        # since python-docx strips ref chars from .text, we append inline markers.
        for wid in word_fn_ids:
            n = get_fn_num(wid)
            inline = inline + f"[^{n}]"

        line = inline.strip()
        if list_marker:
            line = list_marker + line
        raw_lines.append(line)

    # 4. Process [aside] / [/aside] blocks
    processed_lines = _process_asides(raw_lines)

    # 5. Append footnote definitions (Word-footnote derived)
    footnote_defs: list[str] = []
    if fn_map:
        footnote_defs.append("")
        for word_id, n in sorted(fn_map.items(), key=lambda x: x[1]):
            fn_text = word_footnotes.get(word_id, "")
            footnote_defs.append(f"[^{n}]: {fn_text}")

    # 6. Assemble
    frontmatter = build_frontmatter(meta, pdf_filename)
    body = "\n".join(processed_lines)
    fn_block = "\n".join(footnote_defs)

    parts = [frontmatter, "", "<!-- Replace everything below with your text. -->", "", body]
    if fn_block.strip():
        parts.append(fn_block)

    return "\n".join(parts)


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
            # Single-line aside: [aside] content [/aside]
            content = re.sub(r"\[aside\]", "", line, flags=re.IGNORECASE)
            content = re.sub(r"\[/aside\]", "", content, flags=re.IGNORECASE).strip()
            result.append(":::{.aside}")
            if content:
                result.append(content)
            result.append(":::")
            result.append("")
            continue

        if "[aside]" in lower:
            # Start of aside block
            inside_aside = True
            prefix = re.sub(r"\[aside\].*", "", line, flags=re.IGNORECASE).strip()
            suffix = re.sub(r".*\[aside\]", "", line, flags=re.IGNORECASE).strip()
            result.append(":::{.aside}")
            if suffix:
                result.append(suffix)
            continue

        if "[/aside]" in lower:
            # End of aside block
            suffix = re.sub(r"\[/aside\].*", "", line, flags=re.IGNORECASE).strip()
            if suffix:
                result.append(suffix)
            result.append(":::")
            result.append("")
            inside_aside = False
            continue

        result.append(line)

    if inside_aside:
        # Unclosed aside — close it at end
        result.append(":::")
        result.append("")

    return result
