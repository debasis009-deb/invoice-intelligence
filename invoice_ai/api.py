"""FastAPI service: extract an invoice, queue it for review, export decisions."""
import io

import pypdfium2
from PIL import Image
from fastapi import FastAPI, HTTPException, Response, UploadFile

from . import store
from .extract import extract
from .ocr import ENGINES, read_words
from .schema import Extraction, Invoice, Issue

MAX_BYTES = 10 * 1024 * 1024
ALLOWED = (".jpg", ".jpeg", ".png", ".pdf")

app = FastAPI(title="Invoice Intelligence")


MAGIC = {".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".png": (b"\x89PNG\r\n\x1a\n",), ".pdf": (b"%PDF-",)}
MAX_PIXELS = 25_000_000
MAX_PAGES = 10


def check_upload(data: bytes, filename: str) -> int:
    """Reject files that are too big, not what their name says, or too expensive to read.
    Returns the number of pages (1 for images)."""
    ext = "." + filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in ALLOWED:
        raise ValueError(f"Unsupported file type, use one of {ALLOWED}")
    if len(data) > MAX_BYTES:
        raise ValueError("File is larger than 10 MB")
    if not data.startswith(MAGIC[ext]):
        raise ValueError(f"The file content is not a valid {ext[1:].upper()}")
    try:
        if ext == ".pdf":
            pages = len(pypdfium2.PdfDocument(data))
            if pages > MAX_PAGES:
                raise ValueError(f"PDF has {pages} pages, the limit is {MAX_PAGES}")
            return pages
        width, height = Image.open(io.BytesIO(data)).size
    except ValueError:
        raise
    except Exception as err:  # corrupt or hostile file
        raise ValueError("The file could not be opened") from err
    if width * height > MAX_PIXELS:
        raise ValueError(f"Image is {width}x{height} pixels, the limit is {MAX_PIXELS // 1_000_000} million")
    return 1


def run_extraction(data: bytes, filename: str, engine: str = "auto", extractor: str = "geometry") -> Extraction:
    pages = check_upload(data, filename)
    try:
        words, used = read_words(data, filename, engine)
    except ValueError:
        raise
    except Exception as err:
        raise ValueError("The file could not be read") from err
    if extractor == "model":
        from .llm import extract_with_model  # experimental, API only
        result = extract_with_model(words, used)
    elif extractor == "geometry":
        result = extract(words, used)
    else:
        raise ValueError("extractor must be geometry or model")
    if pages > 1:  # only the first page is read
        result.issues.append(Issue(code="multi_page", message=f"The file has {pages} pages; only page 1 was read"))
        result.needs_review = True
    return result


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "engines": list(ENGINES)}


@app.post("/extract")
async def extract_invoice(file: UploadFile, engine: str = "auto", extractor: str = "geometry") -> dict:
    data = await file.read()
    try:
        result = run_extraction(data, file.filename or "upload", engine, extractor)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"review_id": store.add(file.filename or "upload", data, result), **result.model_dump(mode="json")}


@app.get("/reviews")
def list_reviews(status: str | None = None) -> list[dict]:
    return store.listing(status)


@app.get("/reviews/export.csv")
def export_reviews() -> Response:
    return Response(store.export_csv(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=reviews.csv"})


@app.get("/reviews/{review_id}")
def get_review(review_id: int) -> dict:
    row = store.get(review_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such review")
    row.pop("file")
    return row


@app.post("/reviews/{review_id}/resolve")
def resolve_review(review_id: int, invoice: Invoice, expected_revision: int | None = None) -> dict:
    """Save a review. Pass expected_revision (from GET /reviews/{id}) to refuse overwriting a newer save (409)."""
    try:
        row = store.resolve(review_id, invoice, expected_revision)
    except store.Conflict as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    if row is None:
        raise HTTPException(status_code=404, detail="No such review")
    return {"id": review_id, "status": row["status"], "changed": row["changed"], "revision": row["revision"]}


@app.get("/", include_in_schema=False)
def index() -> dict:
    return {"docs": "/docs", "review_ui": "/ui"}
