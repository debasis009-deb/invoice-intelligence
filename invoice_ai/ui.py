"""Gradio review screen: upload, check the highlighted problems, fix, save."""
import json
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation

import gradio as gr
import pandas as pd

from . import store
from .api import ALLOWED, app, run_extraction
from .ocr import ENGINES, load_pages
from .schema import Invoice, Item, TaxLine

ITEM_COLS = ["description", "qty", "unit_price", "amount"]
TAX_COLS = ["label", "rate", "amount"]


def dec(value) -> Decimal | None:
    if value is None or str(value).strip() in ("", "nan", "None"):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as err:
        raise ValueError(f"'{value}' is not a number") from err


def to_frame(rows: list[dict], cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=cols).astype(object).where(lambda f: f.notna(), "")


def build_invoice(vendor, number, day, currency, subtotal, discount, rounding, total, tax_df, item_df) -> Invoice:
    items = [Item(description=str(r["description"]), qty=dec(r["qty"]), unit_price=dec(r["unit_price"]),
                  amount=dec(r["amount"])) for _, r in item_df.iterrows() if str(r["description"]).strip()]
    taxes = [TaxLine(label=str(r["label"]), rate=dec(r["rate"]), amount=dec(r["amount"]))
             for _, r in tax_df.iterrows() if str(r["label"]).strip()]
    return Invoice(vendor=vendor or None, invoice_no=number or None,
                   date=date.fromisoformat(day) if day.strip() else None, currency=currency or None,
                   items=items, subtotal=dec(subtotal), discount=dec(discount) or Decimal(0),
                   tax_lines=taxes, rounding=dec(rounding) or Decimal(0), total=dec(total))


def preview_html(page) -> str:
    """Show the page as an inline data URI. Gradio's file URLs break behind Colab's proxy."""
    import base64
    import io
    img = page.convert("RGB")
    img.thumbnail((1100, 1500))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return (f'<img alt="uploaded invoice" style="max-width:100%;max-height:620px;object-fit:contain" '
            f'src="data:image/jpeg;base64,{b64}">')


def on_extract(path, engine):
    if not path:
        raise gr.Error("Choose an invoice image or PDF first.")
    data = open(path, "rb").read()
    try:
        result = run_extraction(data, path, engine)
    except ValueError as err:
        raise gr.Error(str(err)) from err
    review_id = store.add(path.rsplit("/", 1)[-1], data, result)
    inv = result.invoice
    problems = "\n".join(f"- **{i.code}**: {i.message}" for i in result.issues) or "No problems found."
    status = ("Needs review" if result.needs_review else "No problems found, still check the numbers")
    return (preview_html(load_pages(data, path)[0]), f"### {status} (engine: {result.engine})\n{problems}", review_id, 0,
            inv.vendor or "", inv.invoice_no or "", str(inv.date or ""), inv.currency or "",
            str(inv.subtotal or ""), str(inv.discount), str(inv.rounding), str(inv.total or ""),
            to_frame([t.model_dump(mode="json") for t in inv.tax_lines], TAX_COLS),
            to_frame([i.model_dump(mode="json") for i in inv.items], ITEM_COLS))


def save_review(review_id, revision, *fields) -> tuple[str, int | None]:
    """Save the form. Returns (message, revision to keep). If someone else saved this review meanwhile,
    nothing is overwritten and the form keeps the user's edited values."""
    if not review_id:
        raise gr.Error("Extract an invoice first.")
    try:
        invoice = build_invoice(*fields)
    except ValueError as err:
        raise gr.Error(str(err)) from err
    try:
        row = store.resolve(int(review_id), invoice, int(revision or 0))
    except store.Conflict:
        return ("**Not saved.** This review was changed by someone else after you opened it. Your edits are still "
                "in the form: copy them if needed, extract the file again to load the latest saved version."), revision
    changed = ", ".join(json.loads(row["changed"])) or "nothing"
    note = " You approved it although checks had flagged it." if row["status"] == "approved_with_issues" else ""
    return f"Saved as **{row['status']}**. Fields changed: {changed}.{note}", row["revision"]


def on_save(review_id, revision, *fields):
    return save_review(review_id, revision, *fields)


def on_export():
    """Inline download link plus the CSV text. Gradio file links point at an internal host behind Colab's proxy."""
    import base64
    text = store.export_csv()
    b64 = base64.b64encode(text.encode()).decode()
    link = (f'<a download="reviews.csv" href="data:text/csv;base64,{b64}" '
            f'style="font-weight:600">Download reviews.csv</a>')
    return link, text


with gr.Blocks(title="Invoice Intelligence") as ui:
    gr.Markdown("# Invoice Intelligence\nUpload an invoice, check what was flagged, correct it, save.")
    review_id, revision = gr.State(), gr.State()
    with gr.Row():
        upload = gr.File(label="Invoice (JPG, PNG or PDF)", type="filepath", file_types=list(ALLOWED))
        engine = gr.Dropdown(["auto", *ENGINES], value="auto", label="Reader")
        run = gr.Button("Extract", variant="primary")
    problems = gr.Markdown()
    with gr.Row():
        preview = gr.HTML(label="Document")
        with gr.Column():
            vendor, number = gr.Textbox(label="Vendor"), gr.Textbox(label="Invoice number")
            day, currency = gr.Textbox(label="Date (YYYY-MM-DD)"), gr.Textbox(label="Currency")
            subtotal, discount = gr.Textbox(label="Subtotal"), gr.Textbox(label="Discount")
            rounding, total = gr.Textbox(label="Rounding"), gr.Textbox(label="Total")
    items = gr.Dataframe(headers=ITEM_COLS, label="Items", interactive=True, row_count=(1, "dynamic"))
    taxes = gr.Dataframe(headers=TAX_COLS, label="Tax lines", interactive=True, row_count=(1, "dynamic"))
    save, export = gr.Button("Save review", variant="primary"), gr.Button("Export CSV")
    message, download = gr.Markdown(), gr.HTML()
    csv_text = gr.Textbox(label="CSV text (copy if the download link is blocked)", lines=6, interactive=False)
    form = [vendor, number, day, currency, subtotal, discount, rounding, total, taxes, items]
    run.click(on_extract, [upload, engine], [preview, problems, review_id, revision, *form])
    save.click(on_save, [review_id, revision, *form], [message, revision])
    export.click(on_export, None, [download, csv_text])

app = gr.mount_gradio_app(app, ui, path="/ui")
