"""Fetch arXiv PDFs and extract bounded per-page text.

PyMuPDF is imported lazily so the rest of the pipeline stays standard-library.
The cache lives outside git (``.cache/pdfs``) and is optional; failures are
classified as PdfError/NoPdf so callers can record them without losing work.
"""
from __future__ import annotations
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "3D-Seg-Daily/1.0 (+https://github.com/Nuyoah0123/3D-Semantic-Segmentation-Domain-Adaptation)"
ARXIV_PDF = re.compile(r"^https://arxiv\.org/pdf/[\w.\-/]+$", re.I)
_last_request = [0.0]

class PdfError(RuntimeError):
    """The PDF exists but could not be fetched or parsed; retrying may help."""

class NoPdf(PdfError):
    """This record has no usable open PDF (missing id, 403/404, not a PDF)."""

def _url(arxiv: str, version: int | None) -> str:
    suffix = f"v{version}" if version else ""
    return f"https://arxiv.org/pdf/{arxiv}{suffix}"

def download_pdf(arxiv: str, version: int | None, cache_dir: Path, *, interval: float = 3.0,
                 timeout: int = 90, max_bytes: int = 60_000_000) -> Path:
    """Download a version-pinned arXiv PDF to the cache; never follow off-site redirects."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{arxiv.replace('/', '_')}v{version or 1}.pdf"
    if path.exists():
        head = path.read_bytes()[:5]
        if head == b"%PDF-" and 0 < path.stat().st_size <= max_bytes:
            return path
        path.unlink()
    wait = interval - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.monotonic()
    request = urllib.request.Request(_url(arxiv, version), headers={"User-Agent": UA, "Accept": "application/pdf"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not ARXIV_PDF.match(response.geturl()):
                raise NoPdf("unexpected-redirect")
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        raise NoPdf(f"http-{error.code}") from None
    except urllib.error.URLError as error:
        raise PdfError("network-" + type(error.reason).__name__) from None
    if len(data) > max_bytes:
        raise PdfError("pdf-too-large")
    if not data.startswith(b"%PDF-"):
        raise NoPdf("not-a-pdf")
    path.write_bytes(data)
    return path

def load_pages(pdf_path: Path, *, max_pages: int = 40, max_chars_per_page: int = 80_000) -> list[dict]:
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError:
            raise PdfError("pdf-backend-missing") from None
    try:
        with fitz.open(pdf_path) as document:
            pages = []
            for index, page in enumerate(document):
                if index >= max_pages:
                    break
                text = (page.get_text("text", sort=True) or "").strip()
                if text:
                    pages.append({"page": index + 1, "text": text[:max_chars_per_page]})
    except PdfError:
        raise
    except Exception:
        raise PdfError("pdf-read-failed") from None
    if not pages:
        raise PdfError("empty-pdf")
    return pages

_RESULTS_PAGE = re.compile(r"experiment|evaluation|result|ablation|comparison|limitation|conclusion|table", re.I)

def select_pages(pages: list[dict], *, max_chars: int = 60_000, first: int = 2) -> list[dict]:
    """Keep an auditable, bounded mix of introduction and results/limitation pages."""
    priority = sorted(pages, key=lambda p: (p["page"] <= first, bool(_RESULTS_PAGE.search(p["text"][:2000])), -p["page"]), reverse=True)
    chosen: list[dict] = []
    remaining = max(2000, int(max_chars))
    for page in priority:
        if remaining < 800:
            break
        text = page["text"][:remaining]
        chosen.append({"page": page["page"], "text": text})
        remaining -= len(text)
    return sorted(chosen, key=lambda p: p["page"])
