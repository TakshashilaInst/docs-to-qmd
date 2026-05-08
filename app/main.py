"""
FastAPI application for the Takshashila Google Docs → QMD/PDF Converter.

Endpoints:
  GET  /           → serves static/index.html
  POST /api/convert → fetches Google Doc, converts, optionally renders, returns ZIP
  GET  /api/health  → liveness check
"""

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from docx import Document

import tempfile

from gdocs import fetch_docx
from converter import convert, convert_blog
from renderer import render_and_zip, zip_blog, TypstNotFoundError, RenderError

STATIC_DIR = Path(__file__).parent.parent / "static"

app = FastAPI(title="Takshashila QMD Converter", docs_url=None, redoc_url=None)

# Allow the GitHub Pages frontend (and local dev) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/convert")
async def api_convert(
    google_doc_url: str = Form(...),
    mode: str = Form("paper"),          # "paper" or "blog"
    title: str = Form(...),
    subtitle: str = Form(""),
    authors: str = Form(...),
    date: str = Form(...),
    tldr: str = Form(""),
    categories: str = Form(""),
    doctype: str = Form(""),
    docversion: str = Form(""),
    pdf_filename: str = Form(""),       # required for paper
    slug: str = Form(""),               # required for blog
    render_pdf: bool = Form(True),
):
    # ── 1. Fetch DOCX ──────────────────────────────────────────────────────
    try:
        docx_path = fetch_docx(google_doc_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ── 2. Parse DOCX ──────────────────────────────────────────────────────
    try:
        docx_bytes = docx_path.read_bytes()
        doc = Document(str(docx_path))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse the downloaded document: {exc}",
        )
    finally:
        docx_path.unlink(missing_ok=True)

    # ── 3. Blog mode ───────────────────────────────────────────────────────
    if mode == "blog":
        if not slug:
            raise HTTPException(status_code=400, detail="slug is required for blog mode.")
        with tempfile.TemporaryDirectory() as img_tmp:
            images_dir = Path(img_tmp) / "images"
            images_dir.mkdir()
            meta = {
                "title": title,
                "authors": authors,
                "date": date,
                "categories": categories,
            }
            try:
                qmd_content = convert_blog(doc, meta, slug, images_dir, docx_bytes=docx_bytes)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Conversion error: {exc}")

            try:
                zip_bytes = zip_blog(qmd_content, images_dir, slug)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Packaging error: {exc}")

        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
        )

    # ── 4. Paper mode: Convert DOCX → QMD ─────────────────────────────────
    if not pdf_filename:
        raise HTTPException(status_code=400, detail="pdf_filename is required for paper mode.")

    with tempfile.TemporaryDirectory() as img_tmp:
        images_dir = Path(img_tmp) / "images"
        images_dir.mkdir()

        meta = {
            "title": title,
            "subtitle": subtitle,
            "authors": authors,
            "date": date,
            "tldr": tldr,
            "categories": categories,
            "doctype": doctype,
            "docversion": docversion,
        }

        try:
            qmd_content = convert(doc, meta, pdf_filename, images_dir, docx_bytes=docx_bytes)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Conversion error: {exc}")

        # ── 5. Render PDF + zip ───────────────────────────────────────────
        try:
            zip_bytes = render_and_zip(
                qmd_content=qmd_content,
                images_dir=images_dir,
                pdf_filename=pdf_filename,
                render_pdf=render_pdf,
            )
        except TypstNotFoundError:
            # Typst not installed — return QMD-only zip with a warning header
            zip_bytes = render_and_zip(
                qmd_content=qmd_content,
                images_dir=images_dir,
                pdf_filename=pdf_filename,
                render_pdf=False,
            )
            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{pdf_filename}.zip"',
                    "X-Typst-Warning": "Typst not installed; PDF skipped.",
                },
            )
        except RenderError as exc:
            raise HTTPException(status_code=500, detail=f"PDF render failed: {exc}")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_filename}.zip"',
        },
    )


# Serve the static frontend (local dev only — production uses GitHub Pages).
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
