"""SQLite review queue: every extraction, the human decision and what changed."""
import csv
import io
import json
import os
import sqlite3
from datetime import datetime, timezone

from .schema import Extraction, Invoice

DB_PATH = os.environ.get("INVOICE_DB", "reviews.db")
SCHEMA = """CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY, created TEXT NOT NULL, filename TEXT NOT NULL, engine TEXT NOT NULL,
    status TEXT NOT NULL, extraction TEXT NOT NULL, final TEXT, changed TEXT, reviewed TEXT, file BLOB)"""
CSV_COLUMNS = ["id", "filename", "engine", "status", "reviewed", "vendor", "invoice_no", "date", "currency",
               "subtotal", "discount", "tax_total", "rounding", "total", "items_json", "changed_fields"]


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute(SCHEMA)
    try:  # older databases lack the revision counter used to detect concurrent reviews
        db.execute("ALTER TABLE reviews ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    return db


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(filename: str, file: bytes, result: Extraction) -> int:
    """Queue an extraction. Clean ones start as auto_ok but can still be reviewed."""
    status = "pending" if result.needs_review else "auto_ok"
    with connect() as db:
        cur = db.execute("INSERT INTO reviews (created, filename, engine, status, extraction, file)"
                         " VALUES (?, ?, ?, ?, ?, ?)",
                         (now(), filename, result.engine, status, result.model_dump_json(), file))
        return cur.lastrowid


def get(review_id: int) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    return dict(row) if row else None


def listing(status: str | None = None) -> list[dict]:
    query = "SELECT id, created, filename, engine, status, reviewed, changed FROM reviews"
    with connect() as db:
        rows = db.execute(query + (" WHERE status = ?" if status else "") + " ORDER BY id", (status,) if status else ())
    return [dict(r) for r in rows]


def changed_fields(before: Invoice, after: Invoice) -> list[str]:
    a, b = before.model_dump(mode="json"), after.model_dump(mode="json")
    return [k for k in a if a[k] != b[k]]


class Conflict(Exception):
    """The review changed since the caller read it (someone else saved first)."""


def resolve(review_id: int, final: Invoice, expected_revision: int | None = None) -> dict | None:
    """Record the reviewer's invoice. Status: `corrected` if any field changed; `approved` if unchanged
    and nothing was flagged; `approved_with_issues` if unchanged although checks had flagged it
    (an explicit human override, never automatic).
    Re-reviewing an already reviewed invoice is allowed. If `expected_revision` is given (the `revision` the
    caller read) and the row has moved on, raise Conflict instead of overwriting the other reviewer's save.
    Without it the last save wins."""
    row = get(review_id)
    if row is None:
        return None
    extraction = Extraction.model_validate_json(row["extraction"])
    changed = changed_fields(extraction.invoice, final)
    status = "corrected" if changed else "approved_with_issues" if extraction.issues else "approved"
    where, args = ("id = ?", [review_id]) if expected_revision is None else ("id = ? AND revision = ?",
                                                                            [review_id, expected_revision])
    with connect() as db:
        cur = db.execute("UPDATE reviews SET status = ?, final = ?, changed = ?, reviewed = ?, revision = revision + 1"
                         f" WHERE {where}", [status, final.model_dump_json(), json.dumps(changed), now(), *args])
        if cur.rowcount == 0:
            raise Conflict(f"review {review_id} was changed by someone else; reload it and review again")
    return get(review_id)


def export_csv() -> str:
    """One row per reviewed invoice (final values), for accounting import."""
    out = io.StringIO()
    writer = csv.DictWriter(out, CSV_COLUMNS)
    writer.writeheader()
    with connect() as db:
        rows = db.execute("SELECT * FROM reviews WHERE final IS NOT NULL ORDER BY id").fetchall()
    for row in rows:
        inv = Invoice.model_validate_json(row["final"])
        writer.writerow({
            "id": row["id"], "filename": row["filename"], "engine": row["engine"], "status": row["status"],
            "reviewed": row["reviewed"], "vendor": inv.vendor, "invoice_no": inv.invoice_no, "date": inv.date,
            "currency": inv.currency, "subtotal": inv.subtotal, "discount": inv.discount,
            "tax_total": inv.tax_total, "rounding": inv.rounding, "total": inv.total,
            "items_json": json.dumps([i.model_dump(mode="json") for i in inv.items]),
            "changed_fields": ",".join(json.loads(row["changed"]))})
    return out.getvalue()
