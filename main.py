"""
FastAPI application for the Takshashila Google Docs → QMD/PDF Converter.

Endpoints:
  GET  /           → serves static/index.html
  POST /api/convert → fetches Google Doc, converts, optionally renders, returns ZIP
  GET  /api/health  → liveness check
"""

import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from docx import Document

from gdocs import fetch_docx
from converter import convert
from renderer import render_and_zip, QuartoNotFoundError, RenderError

STATIC_DIR = Path(__file__).parent.parent / "static"

app = FastAPI(title="Takshashila QMD Converter", docs_url=None, redoc_url=None)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/convert")
async def api_convert(
    google_doc_url: str = Form(...),
    title: str = Form(...),
    subtitle: str = Form(""),
    authors: str = Form(...),
    date: str = Form(...),
    tldr: str = Form(""),
    categories: str = Form(""),
    doctype: str = Form(""),
    docversion: str = Form(""),
    pdf_filename: str = Form(...),
    render_pdf: bool = Form(True),
):
    # ── 1. Fetch DOCX ──────────────────────────────────────────────────────
    try:
        docx_path = fetch_docx(google_doc_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ── 2. Parse DOCX ──────────────────────────────────────────────────────
    try:
        doc = Document(str(docx_path))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse the downloaded document: {exc}",
        )
    finally:
        docx_path.unlink(missing_ok=True)

    # ── 3. Extract images into a temp dir ──────────────────────────────────
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

        # ── 4. Convert DOCX → QMD ─────────────────────────────────────────
        try:
            qmd_content = convert(doc, meta, pdf_filename, images_dir)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Conversion error: {exc}",
            )

        # ── 5. Render PDF + zip ───────────────────────────────────────────
        try:
            zip_bytes = render_and_zip(
                qmd_content=qmd_content,
                images_dir=images_dir,
                pdf_filename=pdf_filename,
                render_pdf=render_pdf,
            )
        except QuartoNotFoundError:
            # Quarto not installed — return QMD-only zip with a warning header
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
                    "X-Quarto-Warning": "Quarto not installed; PDF skipped.",
                },
            )
        except RenderError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"PDF render failed: {exc}",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error during rendering: {exc}",
            )

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_filename}.zip"',
        },
    )


# ── Serve the static frontend ──────────────────────────────────────────────────
# Mount AFTER API routes so /api/* isn't caught by the static handler.
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
