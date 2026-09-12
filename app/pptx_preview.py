"""Conversão de apresentações PowerPoint para imagens de visualização."""
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def convert_pptx_to_images(data: bytes, dpi: int = 150) -> list[tuple[str, bytes]]:
    """Converte cada slide do PPTX em PNG usando LibreOffice + PyMuPDF."""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError('O visualizador de PowerPoint requer PyMuPDF.') from exc

    soffice = shutil.which('libreoffice') or shutil.which('soffice')
    if not soffice:
        raise RuntimeError('O servidor não possui LibreOffice instalado para converter apresentações PowerPoint.')

    with tempfile.TemporaryDirectory(prefix='portal-pptx-') as tmp:
        root = Path(tmp)
        source = root / 'presentation.pptx'
        source.write_bytes(data)
        outdir = root / 'pdf'
        outdir.mkdir()
        result = subprocess.run(
            [soffice, '--headless', '--convert-to', 'pdf', '--outdir', str(outdir), str(source)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        pdf_path = outdir / 'presentation.pdf'
        if result.returncode != 0 or not pdf_path.exists():
            detail = (result.stderr or result.stdout).decode('utf-8', errors='ignore').strip()
            raise RuntimeError(detail or 'Não foi possível converter a apresentação para PDF.')

        document = fitz.open(pdf_path)
        images = []
        scale = dpi / 72
        matrix = fitz.Matrix(scale, scale)
        try:
            for index, page in enumerate(document, start=1):
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                images.append((f'slide-{index:03d}.png', pix.tobytes('png')))
        finally:
            document.close()
        return images
