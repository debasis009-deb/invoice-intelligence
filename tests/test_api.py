import json

import pytest
from fastapi.testclient import TestClient

from invoice_ai import store
from invoice_ai.api import app
from invoice_ai.generate import generate
from invoice_ai.schema import Extraction, Invoice, Issue


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "reviews.db"))
    generate(tmp_path, "dev", 1, seed=3)
    client = TestClient(app)
    client.docs = tmp_path / "dev"
    client.truth = {d["id"]: d["truth"] for d in json.loads((client.docs / "manifest.json").read_text())}
    return client


def upload(client, name, engine="pdf-text"):
    path = client.docs / name
    return client.post(f"/extract?engine={engine}", files={"file": (name, path.read_bytes())})


def test_extract_digital_pdf_matches_truth_and_skips_review(client):
    body = upload(client, "D2_01.pdf").json()
    truth = client.truth["D2_01"]
    assert float(body["invoice"]["total"]) == float(truth["total"])
    assert body["invoice"]["invoice_no"] == truth["invoice_no"]
    assert body["needs_review"] is False
    assert client.get(f"/reviews/{body['review_id']}").json()["status"] == "auto_ok"


def test_review_correction_is_recorded_and_exported(client):
    body = upload(client, "D1_01.pdf").json()
    fixed = body["invoice"] | {"vendor": "Corrected Vendor"}
    out = client.post(f"/reviews/{body['review_id']}/resolve", json=fixed).json()
    assert out["status"] == "corrected" and json.loads(out["changed"]) == ["vendor"]
    csv_text = client.get("/reviews/export.csv").text
    assert "Corrected Vendor" in csv_text and ",corrected," in csv_text
    same = upload(client, "D1_01.pdf").json()
    assert client.post(f"/reviews/{same['review_id']}/resolve", json=same["invoice"]).json()["status"] == "approved"


def test_approving_a_flagged_invoice_unchanged_is_recorded_as_override(client):
    flagged = Extraction(invoice=Invoice(vendor="Acme"), issues=[Issue(code="missing_total", message="No total found")],
                         needs_review=True, engine="test")
    review_id = store.add("x.pdf", b"", flagged)
    assert store.resolve(review_id, flagged.invoice)["status"] == "approved_with_issues"
    clean = store.add("y.pdf", b"", Extraction(invoice=Invoice(vendor="Acme"), engine="test"))
    assert store.resolve(clean, Invoice(vendor="Acme"))["status"] == "approved"


def test_rejects_unsupported_file_and_unknown_review(client):
    assert client.post("/extract", files={"file": ("notes.txt", b"hello")}).status_code == 422
    assert client.get("/reviews/999").status_code == 404
    assert client.post("/reviews/999/resolve", json={}).status_code == 404


@pytest.mark.parametrize("engine", ["rapidocr", "tesseract"])
def test_scan_end_to_end_with_real_ocr(client, engine):
    body = upload(client, "D1_01.jpg", engine).json()
    assert body["engine"] == engine
    assert body["invoice"]["invoice_no"] == client.truth["D1_01"]["invoice_no"]
    assert body["invoice"]["total"] is not None


def test_upload_checks():
    import io
    import pytest
    from PIL import Image
    from invoice_ai.api import check_upload
    with pytest.raises(ValueError, match="not a valid PNG"):
        check_upload(b"MZ not an image", "invoice.png")  # wrong content for the name
    with pytest.raises(ValueError, match="Unsupported"):
        check_upload(b"x", "invoice.exe")
    with pytest.raises(ValueError, match="larger"):
        check_upload(b"%PDF-" + b"0" * (10 * 1024 * 1024), "big.pdf")
    with pytest.raises(ValueError, match="could not be opened"):
        check_upload(b"%PDF-1.4 broken", "broken.pdf")
    buf = io.BytesIO()
    Image.new("L", (6000, 5000)).save(buf, "PNG")
    with pytest.raises(ValueError, match="pixels"):
        check_upload(buf.getvalue(), "huge.png")
    buf = io.BytesIO()
    Image.new("L", (50, 50)).save(buf, "PNG")
    assert check_upload(buf.getvalue(), "ok.PNG") == 1


def test_concurrent_review_is_not_overwritten(client):
    """Two reviewers open the same review; the second save must not silently replace the first."""
    body = upload(client, "D1_01.pdf").json()
    rid = body["review_id"]
    rev = client.get(f"/reviews/{rid}").json()["revision"]
    first = dict(body["invoice"], invoice_no="FIRST")
    second = dict(body["invoice"], invoice_no="SECOND")
    assert client.post(f"/reviews/{rid}/resolve?expected_revision={rev}", json=first).status_code == 200
    stale = client.post(f"/reviews/{rid}/resolve?expected_revision={rev}", json=second)
    assert stale.status_code == 409
    assert "FIRST" in client.get("/reviews/export.csv").text and "SECOND" not in client.get("/reviews/export.csv").text
    # a deliberate re-review of an already reviewed invoice still works with the current revision
    rev2 = client.get(f"/reviews/{rid}").json()["revision"]
    assert client.post(f"/reviews/{rid}/resolve?expected_revision={rev2}", json=second).status_code == 200


def test_ui_save_uses_revision_and_keeps_edits_on_conflict(client):
    from invoice_ai import ui
    body = upload(client, "D1_01.pdf").json()
    rid = body["review_id"]
    inv = Invoice.model_validate(body["invoice"])
    fields = lambda i: (i.vendor, i.invoice_no or "", str(i.date or ""), i.currency or "", str(i.subtotal or ""),
                        str(i.discount), str(i.rounding), str(i.total or ""),
                        ui.to_frame([], ui.TAX_COLS), ui.to_frame([], ui.ITEM_COLS))
    msg1, rev1 = ui.save_review(rid, 0, *fields(inv.model_copy(update={"vendor": "Reviewer One"})))
    assert msg1.startswith("Saved") and rev1 == 1
    msg2, rev2 = ui.save_review(rid, 0, *fields(inv.model_copy(update={"vendor": "Reviewer Two"})))
    assert msg2.startswith("**Not saved") and rev2 == 0
    assert store.get(rid)["revision"] == 1 and "Reviewer One" in store.get(rid)["final"]
    msg3, rev3 = ui.save_review(rid, rev1, *fields(inv.model_copy(update={"vendor": "Reviewer Two"})))
    assert msg3.startswith("Saved") and rev3 == 2
