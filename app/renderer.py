"""
Render a QMD file to PDF using Typst, then package everything into a ZIP.

Directory layout inside the temp working directory:
  {work_dir}/
    {stem}.qmd
    {stem}.typ         (generated Typst source)
    takshashila.typ    (Takshashila Typst template)
    images/
      img_1.png ...
    assets/
      main-logo-dark.png
      {stem}.pdf       (compiled PDF)

The returned ZIP mirrors this layout (QMD + images; PDF in assets/ if rendered).
"""

import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from typst_renderer import qmd_to_typst

TEMPLATE_DIR = Path(__file__).parent / "typst_template"


class TypstNotFoundError(RuntimeError):
    pass


class RenderError(RuntimeError):
    pass


def _find_typst() -> str:
    typst = shutil.which("typst")
    if not typst:
        raise TypstNotFoundError(
            "Typst CLI not found. Make sure it is installed and on PATH."
        )
    return typst


def render_and_zip(
    qmd_content: str,
    images_dir: Path,
    pdf_filename: str,
    render_pdf: bool = True,
) -> bytes:
    """
    Write QMD + images to a temp directory, optionally compile a PDF with
    Typst, and return a ZIP of the output as bytes.

    Args:
        qmd_content:  Full text of the .qmd file.
        images_dir:   Directory containing extracted images.
        pdf_filename: Stem for output files (e.g. "EU-Rearm-India-09032026").
        render_pdf:   If False, skip Typst rendering (QMD-only output).

    Returns:
        Raw bytes of the ZIP archive.
    """
    with tempfile.TemporaryDirectory() as tmp_str:
        work = Path(tmp_str)

        # Write QMD
        qmd_path = work / f"{pdf_filename}.qmd"
        qmd_path.write_text(qmd_content, encoding="utf-8")

        # Copy images
        out_images = work / "images"
        out_images.mkdir(exist_ok=True)
        if images_dir.exists():
            for img in images_dir.iterdir():
                shutil.copy(img, out_images / img.name)

        # Assets dir (logo + output PDF)
        assets = work / "assets"
        assets.mkdir(exist_ok=True)

        logo_src = TEMPLATE_DIR / "assets" / "main-logo-dark.png"
        if logo_src.exists():
            shutil.copy(logo_src, assets / "main-logo-dark.png")

        pdf_path: Path | None = None

        if render_pdf:
            typst = _find_typst()

            # Generate Typst source
            typ_content = qmd_to_typst(qmd_content)
            typ_path = work / f"{pdf_filename}.typ"
            typ_path.write_text(typ_content, encoding="utf-8")

            # Copy template file alongside the .typ source
            tpl_src = TEMPLATE_DIR / "takshashila.typ"
            if tpl_src.exists():
                shutil.copy(tpl_src, work / "takshashila.typ")

            # Compile PDF
            out_pdf = work / f"{pdf_filename}.pdf"
            has_logo = (assets / "main-logo-dark.png").exists()
            typst_inputs = ["--input", "has-logo=true"] if has_logo else []
            try:
                result = subprocess.run(
                    [typst, "compile", *typst_inputs, str(typ_path), str(out_pdf)],
                    cwd=str(work),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired as exc:
                raise RenderError("Typst compile timed out after 2 minutes.") from exc

            if result.returncode != 0:
                stderr = result.stderr or result.stdout or "(no output)"
                raise RenderError(
                    f"Typst compile failed (exit {result.returncode}):\n{stderr[-3000:]}"
                )

            if out_pdf.exists():
                dest = assets / f"{pdf_filename}.pdf"
                shutil.move(str(out_pdf), str(dest))
                pdf_path = dest

        # Build ZIP in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # QMD
            zf.write(qmd_path, arcname=f"{pdf_filename}.qmd")

            # Assets: logo + PDF (if rendered)
            for f in assets.iterdir():
                zf.write(f, arcname=f"assets/{f.name}")

            # Images
            for img in out_images.iterdir():
                zf.write(img, arcname=f"images/{img.name}")

        buf.seek(0)
        return buf.read()


def zip_blog(qmd_content: str, images_dir: Path, slug: str) -> bytes:
    """
    Package a blog QMD + its images into a ZIP (no PDF, no template files).

    Args:
        qmd_content: Full text of the .qmd file.
        images_dir:  Directory containing extracted images.
        slug:        Filename stem (e.g. "my-blog-post").

    Returns:
        Raw bytes of the ZIP archive.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{slug}.qmd", qmd_content.encode("utf-8"))
        if images_dir.exists():
            for img in images_dir.iterdir():
                zf.write(img, arcname=f"images/{img.name}")
    buf.seek(0)
    return buf.read()
