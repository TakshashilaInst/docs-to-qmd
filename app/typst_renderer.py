"""
Convert a QMD file (YAML frontmatter + Markdown body) to a Typst source file
for compilation with the Takshashila template.

The generated .typ file imports takshashila.typ from the same directory and
uses it as a show-rule template.
"""

import re
import yaml


def qmd_to_typst(qmd_content: str) -> str:
    """
    Convert QMD content to a Typst source file string.
    """
    meta, body = _parse_frontmatter(qmd_content)
    typst_body = _convert_body(body)
    return _build_typ_file(meta, typst_body)


# ── Frontmatter parsing ────────────────────────────────────────────────────────

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    yaml_str = content[3:end].strip()
    body = content[end + 4:].lstrip("\n")
    try:
        meta = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, body


# ── Body conversion ────────────────────────────────────────────────────────────

def _convert_body(body: str) -> str:
    lines = body.split("\n")

    # Collect footnote definitions (may be multi-line with 4-space indent)
    footnotes: dict[str, str] = {}
    footnote_line_ids: set[int] = set()
    i = 0
    while i < len(lines):
        m = re.match(r'^\[\^(\d+)\]:\s*(.*)', lines[i])
        if m:
            fn_id = m.group(1)
            fn_text = m.group(2)
            footnote_line_ids.add(i)
            j = i + 1
            while j < len(lines) and lines[j].startswith('    '):
                fn_text += ' ' + lines[j].strip()
                footnote_line_ids.add(j)
                j += 1
            footnotes[fn_id] = fn_text
        i += 1

    result: list[str] = []
    in_aside = False
    aside_lines: list[str] = []
    skip_until_close_div = 0  # depth counter for ::: blocks to skip

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip footnote definition lines
        if i in footnote_line_ids:
            i += 1
            continue

        # ── Quarto-only divs to skip (download button, content-visible, etc.) ──
        if re.match(r'^:::\s*\{\.content-visible', line) or re.match(r'^:::\s*\{\.aside\.aside-btn\}', line):
            skip_until_close_div += 1
            i += 1
            continue
        if skip_until_close_div > 0:
            if line.strip() == ':::':
                skip_until_close_div -= 1
            i += 1
            continue

        # ── Aside blocks ──────────────────────────────────────────────────────
        if re.match(r'^:::\s*\{\.aside\}', line):
            in_aside = True
            aside_lines = []
            i += 1
            continue
        if line.strip() == ':::' and in_aside:
            in_aside = False
            aside_body = '\n'.join(
                _convert_inline(l, footnotes) for l in aside_lines if l.strip()
            )
            result.append(f'#aside[{aside_body}]')
            i += 1
            continue
        if in_aside:
            aside_lines.append(line)
            i += 1
            continue

        # Skip other remaining ::: fence lines
        if re.match(r'^:::', line):
            i += 1
            continue

        # ── Headings ──────────────────────────────────────────────────────────
        m = re.match(r'^(#{1,4})\s+(.*)', line)
        if m:
            level = len(m.group(1))
            text = _convert_inline(m.group(2), footnotes)
            result.append('=' * level + ' ' + text)
            i += 1
            continue

        # ── Images ────────────────────────────────────────────────────────────
        m = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)(?:\{[^}]*\})?', line)
        if m:
            alt = m.group(1)
            path = m.group(2)
            # fit: "contain" preserves aspect ratio — never crops or distorts.
            # width: 100% fills the text column; height is calculated automatically.
            img = f'image("{path}", width: 100%, fit: "contain")'
            if alt:
                result.append(f'#figure({img}, caption: [{_escape(alt)}])')
            else:
                result.append(f'#figure({img})')
            i += 1
            continue

        # ── Bullet list ───────────────────────────────────────────────────────
        m = re.match(r'^(\s*)[-*]\s+(.*)', line)
        if m:
            indent = (len(m.group(1)) // 2) * '  '
            text = _convert_inline(m.group(2), footnotes)
            result.append(f'{indent}- {text}')
            i += 1
            continue

        # ── Numbered list ─────────────────────────────────────────────────────
        m = re.match(r'^(\s*)\d+\.\s+(.*)', line)
        if m:
            indent = (len(m.group(1)) // 2) * '  '
            text = _convert_inline(m.group(2), footnotes)
            result.append(f'{indent}+ {text}')
            i += 1
            continue

        # ── Empty line ────────────────────────────────────────────────────────
        if line.strip() == '':
            result.append('')
            i += 1
            continue

        # ── Regular paragraph line ────────────────────────────────────────────
        result.append(_convert_inline(line, footnotes))
        i += 1

    return '\n'.join(result)


def _escape(text: str) -> str:
    """Escape Typst special characters in literal text."""
    text = text.replace('\\', '\\\\')
    text = text.replace('#', '\\#')
    text = text.replace('<', '\\<')
    text = text.replace('@', '\\@')
    return text


def _convert_inline(text: str, footnotes: dict[str, str] | None = None) -> str:
    """Convert inline markdown formatting to Typst markup."""
    if footnotes is None:
        footnotes = {}

    # Inline footnote markers → #footnote[...]
    def replace_fn(m: re.Match) -> str:
        n = m.group(1)
        content = footnotes.get(n, '')
        content = _convert_inline(content, {})  # avoid recursion on nested markers
        return f'#footnote[{content}]'
    text = re.sub(r'\[\^(\d+)\]', replace_fn, text)

    # Bold+italic: ***text***
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'*_\1_*', text)
    # Bold: **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    # Italic: *text* (not inside ** — already consumed above)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'_\1_', text)

    # Hyperlinks: [text](url)
    def replace_link(m: re.Match) -> str:
        link_text = m.group(1)
        url = m.group(2)
        # Escape double-quotes in URL
        url = url.replace('"', '%22')
        # Escape $ in link text so Typst doesn't treat it as math mode
        link_text = link_text.replace('$', r'\$')
        return f'#link("{url}")[{link_text}]'
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', replace_link, text)

    # Escape stray # characters not part of a Typst command we just inserted
    # Strategy: split on existing #... sequences, escape only plain-text segments
    text = _escape_hashes(text)

    # Escape bare $ in plain text (math mode trigger in Typst).
    # The (?<!\\) lookbehind skips already-escaped \$ from link text above.
    text = re.sub(r'(?<!\\)\$', r'\\$', text)

    return text


def _escape_hashes(text: str) -> str:
    """
    Escape '#' characters that are NOT already part of a Typst command
    (i.e., not preceded by being part of #link, #footnote, etc. we inserted).
    We do this by finding '#' not preceded by a word char or backslash and
    not followed by a known Typst function name.
    """
    # Match '#' that isn't already a Typst command we inserted
    typst_fns = r'(?:link|footnote|figure|image|aside|v|h|text|set|show|let|par)'
    return re.sub(
        r'#(?!' + typst_fns + r'[(\[])',
        r'\\#',
        text,
    )


# ── .typ file assembly ─────────────────────────────────────────────────────────

def _q(s: str) -> str:
    """Wrap a Python string as a Typst string literal."""
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _c(s: str) -> str:
    """Wrap a Python string as Typst content (bracket notation), escaped."""
    return '[' + _escape(str(s)) + ']'


def _build_typ_file(meta: dict, typst_body: str) -> str:
    title = meta.get('title', '') or ''
    subtitle = meta.get('subtitle', '') or ''
    date = str(meta.get('date', '') or '')
    tldr = meta.get('tldr', '') or ''
    doctype = meta.get('doctype', '') or ''
    docversion = meta.get('docversion', '') or ''

    # authors: converter.py uses 'author' as a list
    raw = meta.get('author', meta.get('authors', []))
    if isinstance(raw, str):
        authors = [a.strip() for a in raw.split(',') if a.strip()]
    elif isinstance(raw, list):
        authors = [
            a.get('name', str(a)) if isinstance(a, dict) else str(a)
            for a in raw
        ]
    else:
        authors = []

    authors_typst = '(' + ', '.join(f'"{a}"' for a in authors)
    # Typst needs trailing comma for single-element arrays
    authors_typst += (',' if len(authors) == 1 else '') + ')'

    lines = [
        '#import "takshashila.typ": *',
        '',
        '#show: takshashila-doc.with(',
        f'  title: {_c(title)},',
        f'  subtitle: {_c(subtitle)},',
        f'  authors: {authors_typst},',
        f'  date: {_q(date)},',
        f'  tldr: {_c(tldr)},',
        f'  doctype: {_q(doctype)},',
        f'  docversion: {_q(docversion)},',
        ')',
        '',
        typst_body,
    ]

    return '\n'.join(lines)
