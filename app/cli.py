#!/usr/bin/env python3
"""
Standalone CLI for the Takshashila DOCX -> QMD converter.
Used by the GitHub Actions convert workflow.

Usage (paper):
    python app/cli.py --url "https://docs.google.com/..." --title "My Paper" \
        --authors "Jane Doe" --date 2026-03-30 --pdf-filename MyPaper-30032026 \
        --render-pdf --output /tmp/output.zip

Usage (blog):
    python app/cli.py --url "https://docs.google.com/..." --title "My Blog Post" \
        --authors "Jane Doe" --date 2026-03-30 --mode blog --slug my-blog-post \
        --output /tmp/output.zip
"""

import argparse
import sys
import tempfile
from pathlib import Path

# Ensure app/ is on the Python path so sibling modules resolve correctly
sys.path.insert(0, str(Path(__file__).parent))

from docx import Document  # noqa: E402

from converter import convert, convert_blog  # noqa: E402
from gdocs import fetch_docx  # noqa: E402
from renderer import render_and_zip, zip_blog  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a Google Doc to a Quarto Markdown (.qmd) + PDF ZIP"
    )
    parser.add_argument("--url", required=True, help="Google Docs share URL")
    parser.add_argument("--mode", default="paper", choices=["paper", "blog"],
                        help="Conversion mode: paper (QMD+PDF) or blog (QMD only)")
    parser.add_argument("--title", required=True, help="Document title")
    parser.add_argument("--subtitle", default="", help="Document subtitle (paper only)")
    parser.add_argument("--authors", default="", help="Authors (comma-separated)")
    parser.add_argument("--date", default="", help="Publication date (YYYY-MM-DD)")
    parser.add_argument("--tldr", default="", help="TL;DR summary (paper only)")
    parser.add_argument("--categories", default="", help="Categories (comma-separated)")
    parser.add_argument("--doctype", default="", help="Document type string (paper only)")
    parser.add_argument("--docversion", default="", help="Version string (paper only)")
    parser.add_argument(
        "--pdf-filename",
        default="document",
        dest="pdf_filename",
        help="Output filename stem for paper mode (no extension, no spaces)",
    )
    parser.add_argument(
        "--slug",
        default="",
        help="URL slug for blog mode (no extension, no spaces)",
    )
    parser.add_argument(
        "--render-pdf",
        action="store_true",
        dest="render_pdf",
        help="Render PDF with Typst (paper mode only)",
    )
    parser.add_argument(
        "--output",
        default="output.zip",
        help="Path to write the output ZIP file",
    )
    args = parser.parse_args()

    # ── Fetch DOCX ────────────────────────────────────────────────────────────
    print(f"[1/3] Fetching Google Doc: {args.url}")
    docx_path = fetch_docx(args.url)
    docx_bytes = docx_path.read_bytes()
    doc = Document(docx_path)

    # ── Convert ───────────────────────────────────────────────────────────────
    print(f"[2/3] Converting to QMD (mode={args.mode})...")
    with tempfile.TemporaryDirectory() as tmp:
        images_dir = Path(tmp) / "images"
        images_dir.mkdir()

        if args.mode == "blog":
            slug = args.slug or args.pdf_filename or "post"
            meta = {
                "title": args.title,
                "authors": args.authors,
                "date": args.date,
                "categories": args.categories,
            }
            qmd_content = convert_blog(doc, meta, slug, images_dir, docx_bytes=docx_bytes)

            print("[3/3] Packaging (blog, QMD only)...")
            zip_bytes = zip_blog(qmd_content, images_dir, slug)

        else:
            meta = {
                "title": args.title,
                "subtitle": args.subtitle,
                "authors": args.authors,
                "date": args.date,
                "tldr": args.tldr,
                "categories": args.categories,
                "doctype": args.doctype,
                "docversion": args.docversion,
            }
            qmd_content = convert(doc, meta, args.pdf_filename, images_dir, docx_bytes=docx_bytes)

            render_label = "with PDF render" if args.render_pdf else "QMD only"
            print(f"[3/3] Packaging ({render_label})...")
            zip_bytes = render_and_zip(
                qmd_content=qmd_content,
                images_dir=images_dir,
                pdf_filename=args.pdf_filename,
                render_pdf=args.render_pdf,
            )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(zip_bytes)
    print(f"Done! Output: {output_path} ({len(zip_bytes):,} bytes)")


if __name__ == "__main__":
    main()
