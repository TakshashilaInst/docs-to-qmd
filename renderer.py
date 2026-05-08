"""
Render a QMD file to PDF using Quarto CLI, then package everything into a ZIP.

Directory layout inside the temp working directory:
  {work_dir}/
    {stem}.qmd
    _metadata.yml          (Takshashila standard)
    _variables.yml         (Takshashila standard)
    images/
      img_1.png ...
    assets/
      {stem}.pdf           (output — moved here after render)

The returned ZIP mirrors this layout.
"""

import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

# Path to the bundled Takshashila template files (inside the Docker image / repo)
TEMPLATE_DIR = Path(__file__).parent / "quarto_template"


class QuartoNotFoundError(RuntimeError):
    pass


class RenderError(RuntimeError):
    pass


def _find_quarto() -> str:
    quarto = shutil.which("quarto")
    if not quarto:
        raise QuartoNotFoundError(
            "Quarto CLI not found. Make sure it is installed and on PATH."
        )
    return quarto


def render_and_zip(
    qmd_content: str,
    images_dir: Path,
    pdf_filename: str,
    render_pdf: bool = True,
) -> bytes:
    """
    Write the QMD + images to a temp directory, run Quarto, and return a ZIP
    of the output as bytes.

    Args:
        qmd_content:  Full text of the .qmd file.
        images_dir:   Directory containing extracted images (img_1.png …).
        pdf_filename: Stem for the output files (e.g. "EU-Rearm-India-09032026").
        render_pdf:   If False, skip Quarto rendering (QMD-only output).

    Returns:
        Raw bytes of the ZIP archive.
    """
    with tempfile.TemporaryDirectory() as tmp_str:
        work = Path(tmp_str)

        # Write QMD
        qmd_path = work / f"{pdf_filename}.qmd"
        qmd_path.write_text(qmd_content, encoding="utf-8")

        # Copy Takshashila template YMLs
        for yml in ("_metadata.yml", "_variables.yml"):
            src = TEMPLATE_DIR / yml
            if src.exists():
                shutil.copy(src, work / yml)

        # Copy images
        out_images = work / "images"
        out_images.mkdir()
        if images_dir.exists():
            for img in images_dir.iterdir():
                shutil.copy(img, out_images / img.name)

        # Create assets/ dir for the PDF
        assets = work / "assets"
        assets.mkdir()

        pdf_path: Path | None = None

        if render_pdf:
            quarto = _find_quarto()
            try:
                result = subprocess.run(
                    [quarto, "render", str(qmd_path), "--to", "pdf"],
                    cwd=str(work),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            except subprocess.TimeoutExpired as exc:
                raise RenderError("Quarto render timed out after 5 minutes.") from exc

            if result.returncode != 0:
                stderr = result.stderr or result.stdout or "(no output)"
                raise RenderError(
                    f"Quarto render failed (exit {result.returncode}):\n{stderr[-3000:]}"
                )

            # Quarto outputs {stem}.pdf in the same directory as the QMD
            rendered = work / f"{pdf_filename}.pdf"
            if rendered.exists():
                dest = assets / f"{pdf_filename}.pdf"
                shutil.move(str(rendered), str(dest))
                pdf_path = dest

        # Build ZIP in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(qmd_path, arcname=f"{pdf_filename}.qmd")

            if pdf_path and pdf_path.exists():
                zf.write(pdf_path, arcname=f"assets/{pdf_filename}.pdf")

            for img in out_images.iterdir():
                zf.write(img, arcname=f"images/{img.name}")

        buf.seek(0)
        return buf.read()
