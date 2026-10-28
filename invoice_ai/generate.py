"""Synthetic invoice generator: the project's own labelled dataset.

Every document is drawn from a seeded random invoice plus a layout template.
Templates, vendors and item catalogues are split between a `dev` set (used to
write and tune the extraction rules) and a `test` set (held-out templates,
fonts, vendors and items, used once for the reported numbers).
Each document is written as a noisy scan (JPEG) and a digital PDF with a text
layer, with exact ground truth in manifest.json.
"""
import argparse
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .schema import Invoice, Item, TaxLine

FONT_DIR = Path(__file__).parent / "fonts"
FONTS = {  # family -> (regular, bold)
    "sans": ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    "mono": ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf"),
    "serif": ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf"),
    "dejavu": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
}
W, H, DPI = 1240, 1754, 150  # A4 at 150 dpi
CENT = Decimal("0.01")
SYMBOL = {"INR": "Rs. ", "USD": "$", "EUR": "EUR ", "GBP": "GBP "}


@dataclass(frozen=True)
class Template:
    name: str
    split: str
    font: str
    size: int
    labels: dict
    columns: tuple
    title: str = "INVOICE"
    regimes: tuple = ("gst_intra", "vat", "none")
    meta_right: bool = False
    rules: bool = True
    money: str = "plain"  # plain | symbol | suffix
    indian_grouping: bool = False
    tax_fmt: str = "{label} @ {rate}%"
    date_fmt: str = "%d/%m/%Y"
    col_x: tuple = ()  # optional (column, right edge) overrides, e.g. to put rate before quantity


def labels(inv_no, date_, sub, disc, rnd, total):
    return {"inv_no": inv_no, "date": date_, "subtotal": sub, "discount": disc,
            "rounding": rnd, "total": total, "desc": "Description", "hsn": "HSN",
            "qty": "Qty", "rate": "Rate", "amount": "Amount"}


BASE = ("desc", "qty", "rate", "amount")
WITH_HSN = ("desc", "hsn", "qty", "rate", "amount")
TEMPLATES = [
    Template("D1", "dev", "sans", 24, labels("Invoice No", "Date", "Subtotal", "Discount", "Round off", "Total"),
             BASE, regimes=("gst_intra", "none")),
    Template("D2", "dev", "mono", 21, labels("Invoice #", "Invoice Date", "Sub Total", "Discount", "Rounding", "Grand Total"),
             WITH_HSN, title="TAX INVOICE", regimes=("gst_intra", "gst_inter"), meta_right=True,
             money="symbol", date_fmt="%d-%b-%Y"),
    Template("D3", "dev", "sans", 24, labels("Inv No.", "Date", "Subtotal", "Less: Discount", "Round Off", "Total Amount"),
             BASE, regimes=("vat", "none"), money="symbol", tax_fmt="{label} ({rate}%)", date_fmt="%Y-%m-%d"),
    Template("D4", "dev", "mono", 22, labels("Invoice Number", "Date of Issue", "Subtotal", "Discount", "Rounding", "Total"),
             BASE, regimes=("vat",), money="suffix", rules=False, date_fmt="%d %B %Y"),
    Template("D5", "dev", "sans", 23, labels("Invoice No", "Date", "Taxable Value", "Discount", "Round Off", "Total"),
             WITH_HSN, title="TAX INVOICE", regimes=("gst_intra", "gst_inter"), indian_grouping=True,
             meta_right=True, money="symbol"),
    Template("H1", "test", "serif", 25, labels("Bill No.", "Dated", "Net Amount", "Discount Allowed", "Rounding Adj.", "Amount Payable"),
             BASE, title="BILL", regimes=("gst_inter", "vat"), rules=False, money="symbol",
             tax_fmt="{label} {rate}%", date_fmt="%d %B %Y"),
    Template("H2", "test", "dejavu", 21, labels("Tax Invoice No", "Invoice Date", "Subtotal", "Discount", "Round Off", "Total Due"),
             WITH_HSN, title="TAX INVOICE", regimes=("gst_intra",), indian_grouping=True, money="symbol",
             date_fmt="%d-%b-%Y"),
    Template("H3", "test", "dejavu", 21, labels("Receipt No", "Date", "Sub-total", "Discount", "Rounding", "TOTAL"),
             BASE, title="RECEIPT", regimes=("vat", "none"), meta_right=True, money="suffix",
             tax_fmt="{label} ({rate}%)"),
    Template("S1", "final", "serif", 22, labels("Document No", "Issued", "Net Total", "Less Discount", "Rounded By", "Balance Due"),
             WITH_HSN, title="TAX INVOICE", regimes=("gst_intra", "gst_inter"), indian_grouping=True,
             date_fmt="%d.%m.%Y"),
    Template("S2", "final", "mono", 20, labels("Ref No", "Date", "Total before tax", "Discount", "Round-off", "Total payable"),
             BASE, title="STATEMENT", regimes=("vat", "none"), meta_right=True, rules=False, money="suffix",
             tax_fmt="{label} {rate}%", date_fmt="%d %b %Y"),
    Template("P1", "holdout", "sans", 22, labels("Invoice Ref", "Issue Date", "Goods Value", "Trade Discount", "Adjustment", "Amount Due"),
             BASE, title="COMMERCIAL INVOICE", regimes=("gst_intra", "vat", "none"), meta_right=True, rules=False,
             money="suffix", date_fmt="%d %B %Y"),
    Template("P2", "holdout", "serif", 24, labels("Voucher No", "Voucher Date", "Gross", "Rebate", "Round", "Grand Amount"),
             BASE, title="CREDIT BILL", regimes=("gst_inter", "vat"), money="symbol", tax_fmt="{label} ({rate}%)",
             col_x=(("rate", 780), ("qty", 960)), date_fmt="%d-%m-%Y"),
    Template("P3", "holdout", "dejavu", 20, labels("Order Invoice #", "Billing Date", "Merchandise Total", "Concession", "Rounded", "Total Payable"),
             WITH_HSN, title="INVOICE", regimes=("gst_intra", "gst_inter"), indian_grouping=True, rules=False,
             tax_fmt="{label} @{rate}%", date_fmt="%Y/%m/%d"),
]

VENDORS = {
    "dev": ["Kaveri Traders", "Bluepeak Supplies", "Maple Street Stationers", "Orchid Electronics",
            "Sundial Hardware", "Northwind Fabrics", "Lotus Print House", "Harbor Light Foods",
            "Redwood Office Mart", "Tulsi Agro Works", "Pioneer Tools Co", "Silverline Wholesale"],
    "test": ["Amber Valley Retail", "Quartz Digital Store", "Banyan Paper Mills", "Coastal Gear Depot",
             "Evergreen Pharma Supply", "Falcon Auto Parts", "Jasmine Home Goods", "Ironbark Steel Traders",
             "Monsoon Cafe Supplies", "Zenith Cable Works", "Greenfield Seeds", "Starling Furnishings"],
    "holdout": ["Ridgeway Paints", "Coral Bay Marine", "Tandoor Spice Mills", "Lunar Optics", "Sapphire Garments",
                "Bridgeport Bearings", "Fernhill Nursery", "Cobalt Labs Supply"],
    "final": ["Harvest Moon Grocers", "Delta Ridge Plastics", "Opal Coast Textiles", "Granite Peak Tools",
              "Willow Bend Books", "Crescent Dairy Co", "Nimbus Cloud Hardware", "Teakwood Interiors"],
}
CATALOG = {  # (description, low price, high price)
    "dev": [("Notebook A5", 20, 90), ("Ball pen box", 40, 150), ("USB cable 1m", 90, 400),
            ("LED bulb 9W", 60, 220), ("Steel bolt M8", 3, 25), ("Cotton fabric roll", 400, 1800),
            ("Printer paper ream", 220, 520), ("Wall clock", 250, 900), ("Hand tool set", 600, 2400),
            ("Rice bag 5kg", 280, 700), ("Mouse pad", 60, 300), ("Desk lamp", 350, 1500),
            ("Packing tape", 25, 120), ("Electric kettle", 700, 2200), ("Whiteboard marker", 15, 80)],
    "test": [("Copper wire 10m", 150, 800), ("Brake pad set", 500, 2600), ("Herbal tea 250g", 90, 450),
             ("Phone case", 80, 600), ("Bamboo basket", 120, 700), ("Seed packet", 10, 90),
             ("Office chair", 2200, 7500), ("Cleaning liquid 1L", 70, 340), ("HDMI cable 2m", 150, 700),
             ("Cardboard box", 12, 80), ("Steel rod 12mm", 300, 1500), ("Table cloth", 150, 900),
             ("Coffee beans 500g", 350, 1100), ("Cable tie pack", 25, 140), ("Vitamin tablets", 120, 800)],
    "holdout": [("Paint tin 4L", 400, 1900), ("Anchor chain 5m", 700, 3200), ("Chilli powder 1kg", 180, 520),
                ("Reading lens", 250, 1400), ("Denim jacket", 700, 2800), ("Ball bearing", 40, 380),
                ("Fern pot", 90, 480), ("Test tube rack", 120, 640), ("Safety goggles", 90, 450),
                ("Rope coil 20m", 150, 720)],
    "final": [("Garden hose 15m", 300, 1400), ("Plastic crate", 150, 650), ("Linen shirt", 500, 2200),
              ("Paperback novel", 120, 520), ("Milk powder 1kg", 380, 720), ("Router device", 1200, 4800),
              ("Oak shelf", 900, 3600), ("Welding rod pack", 200, 950), ("Face towel", 60, 340),
              ("Glass jar set", 180, 760)],
}
CUSTOMERS = ["Aarav Enterprises", "Meera Clinic", "Rahul Constructions", "Priya Boutique", "Greenway School",
             "Lakeview Cafe", "Sharma and Sons", "Delta Workshop"]
CITIES = ["Pune 411001", "Jaipur 302001", "Mysuru 570001", "Surat 395001", "Leeds LS1 4AP", "Austin TX 73301"]

REGIME_TAX = {
    "gst_intra": lambda r: [("CGST", Decimal(r) / 2), ("SGST", Decimal(r) / 2)],
    "gst_inter": lambda r: [("IGST", Decimal(r))],
    "vat": lambda r: [("VAT", Decimal(r))],
    "none": lambda r: [],
}
REGIME_RATES = {"gst_intra": (5, 12, 18, 28), "gst_inter": (5, 12, 18), "vat": (5, 10, 20), "none": (0,)}
REGIME_CURRENCY = {"gst_intra": ("INR",), "gst_inter": ("INR",), "vat": ("USD", "EUR", "GBP"),
                   "none": ("INR", "USD", "EUR", "GBP")}


def q(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def random_invoice(rng: random.Random, tpl: Template) -> Invoice:
    regime = rng.choice(tpl.regimes)
    currency = rng.choice(REGIME_CURRENCY[regime])
    items = []
    for desc, lo, hi in rng.sample(CATALOG[tpl.split], rng.randint(2, 7)):
        qty = Decimal(rng.randint(1, 20))
        unit = q(Decimal(rng.uniform(lo, hi)))
        items.append(Item(description=desc, qty=qty, unit_price=unit, amount=q(qty * unit)))
    subtotal = sum((i.amount for i in items), Decimal(0))
    discount = Decimal(0)
    if rng.random() < 0.35:
        discount = q(subtotal * Decimal(rng.choice((5, 10, 15))) / 100)
    taxable = subtotal - discount
    rate = rng.choice(REGIME_RATES[regime])
    taxes = [TaxLine(label=lbl, rate=r, amount=q(taxable * r / 100))
             for lbl, r in REGIME_TAX[regime](rate)]
    total = taxable + sum((t.amount for t in taxes), Decimal(0))
    rounding = Decimal(0)
    if rng.random() < 0.4:
        rounding = total.quantize(Decimal(1), rounding=ROUND_HALF_UP) - total
        total += rounding
    prefix = rng.choice(("INV", "BL", "R", "SI"))
    return Invoice(
        vendor=rng.choice(VENDORS[tpl.split]),
        invoice_no=f"{prefix}-{rng.choice((25, 26))}-{rng.randint(100, 99999):05d}",
        date=date(2025, 1, 1) + timedelta(days=rng.randint(0, 700)),
        currency=currency, items=items, subtotal=subtotal, discount=discount,
        tax_lines=taxes, rounding=rounding, total=total)


# ---- layout: a document is a list of text ops; scan and PDF render the same ops ----

@dataclass
class Op:
    x: float
    y: float  # baseline
    text: str
    family: str
    size: int
    bold: bool = False


@lru_cache(maxsize=None)
def pil_font(family: str, size: int, bold: bool) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / FONTS[family][bold]), size)


def fmt_number(value: Decimal, tpl: Template) -> str:
    text = f"{abs(value):,.2f}"
    if tpl.indian_grouping:
        whole, frac = text.split(".")
        whole = whole.replace(",", "")
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        text = ",".join(([head] if head else []) + parts + [tail]) + "." + frac
    return ("-" if value < 0 else "") + text


def fmt_money(value: Decimal, tpl: Template, currency: str) -> str:
    text = fmt_number(value, tpl)
    if tpl.money == "symbol":
        return SYMBOL[currency] + text
    if tpl.money == "suffix":
        return f"{text} {currency}"
    return text


def layout(inv: Invoice, tpl: Template, rng: random.Random) -> list[Op]:
    ops: list[Op] = []
    size, fam, cur = tpl.size, tpl.font, inv.currency
    lab = tpl.labels

    def text(x, y, s, bold=False, sz=size, right=False):
        if right:
            x -= pil_font(fam, sz, bold).getlength(s)
        ops.append(Op(x, y, s, fam, sz, bold))

    text(60, 110, inv.vendor, True, size + 12)
    text(60, 110 + size * 1.6, f"{rng.randint(1, 99)} Market Road, {rng.choice(CITIES)}")
    text(60, 110 + size * 3.2, f"Tax ID: {rng.randint(10**9, 10**10 - 1)}")
    text(W - 60, 110, tpl.title, True, size + 6, right=True)
    y = 330
    mx = 640 if tpl.meta_right else 60
    text(mx, y, f"{lab['inv_no']}: {inv.invoice_no}")
    text(mx, y + size * 1.6, f"{lab['date']}: {inv.date.strftime(tpl.date_fmt)}")
    if not tpl.meta_right:
        y += size * 1.6
    text(60, y + size * 3.2, f"Bill To: {rng.choice(CUSTOMERS)}")
    y += size * 5.5

    right = {"hsn": 700, "qty": 790 if "hsn" in tpl.columns else 760, "rate": 960, "amount": W - 60}
    right.update(dict(tpl.col_x))
    rule_y = lambda yy: ops.append(Op(60, yy, "\x00rule", fam, size))  # noqa: E731
    if tpl.rules:
        rule_y(y - size * 1.2)
    for col in tpl.columns:
        text(60 if col == "desc" else right[col], y, lab[col], True, right=col != "desc")
    if tpl.rules:
        rule_y(y + size * 0.4)
    y += size * 1.9
    for it in inv.items:
        cells = {"desc": it.description, "hsn": str(rng.randint(1000, 9999)),
                 "qty": str(it.qty), "rate": fmt_number(it.unit_price, tpl),
                 "amount": fmt_money(it.amount, tpl, cur)}
        for col in tpl.columns:
            text(60 if col == "desc" else right[col], y, cells[col], right=col != "desc")
        y += size * 1.7
    if tpl.rules:
        rule_y(y - size * 0.8)
    y += size * 0.8

    def total_row(label, value, bold=False):
        nonlocal y
        text(640, y, label, bold)
        text(W - 60, y, fmt_money(value, tpl, cur), bold, right=True)
        y += size * 1.7

    total_row(lab["subtotal"], inv.subtotal)
    if inv.discount:
        total_row(lab["discount"], -inv.discount if rng.random() < 0.5 else inv.discount)
    for t in inv.tax_lines:
        total_row(tpl.tax_fmt.format(label=t.label, rate=f"{t.rate:f}"), t.amount)
    if inv.rounding:
        total_row(lab["rounding"], inv.rounding)
    total_row(lab["total"], inv.total, True)
    if tpl.money == "plain":
        text(60, y + size, f"All amounts in {cur}")
    text(60, H - 140, "Payment terms: net 30 days. Thank you for your business.", sz=size - 3)
    return ops


def render_scan(ops: list[Op], rng: random.Random, path: Path) -> None:
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)
    for op in ops:
        if op.text == "\x00rule":
            draw.line([(op.x, op.y), (W - 60, op.y)], fill=60, width=2)
        else:
            draw.text((op.x, op.y), op.text, fill=20, font=pil_font(op.family, op.size, op.bold), anchor="ls")
    img = img.rotate(rng.uniform(-0.6, 0.6), fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.8)))
    noise = Image.effect_noise((W, H), rng.uniform(6, 18))
    img = Image.blend(img, noise, 0.08)
    img.save(path, quality=rng.randint(60, 90))


@lru_cache(maxsize=None)
def register_pdf_font(family: str, bold: bool) -> str:
    name = f"{family}-{'b' if bold else 'r'}"
    pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / FONTS[family][bold])))
    return name


def render_pdf(ops: list[Op], path: Path) -> None:
    k = 72 / DPI
    pdf = canvas.Canvas(str(path), pagesize=(W * k, H * k))
    for op in ops:
        y = (H - op.y) * k
        if op.text == "\x00rule":
            pdf.line(op.x * k, y, (W - 60) * k, y)
        else:
            pdf.setFont(register_pdf_font(op.family, op.bold), op.size * k)
            pdf.drawString(op.x * k, y, op.text)
    pdf.save()


def oracle_boxes(ops: list[Op]) -> list[dict]:
    """The drawn text as perfect OCR boxes (no recognition errors), for isolating extractor errors."""
    return [{"text": op.text, "x0": op.x, "x1": op.x + pil_font(op.family, op.size, op.bold).getlength(op.text),
             "y": op.y - op.size * 0.35, "height": op.size, "conf": 1.0}
            for op in ops if op.text != "\x00rule"]


def generate(out: Path, split: str, per_template: int, seed: int) -> None:
    folder = out / split
    folder.mkdir(parents=True, exist_ok=True)
    manifest = []
    for tpl in (t for t in TEMPLATES if t.split == split):
        for n in range(per_template):
            rng = random.Random(f"{seed}-{tpl.name}-{n}")
            inv = random_invoice(rng, tpl)
            ops = layout(inv, tpl, rng)
            doc_id = f"{tpl.name}_{n + 1:02d}"
            render_scan(ops, rng, folder / f"{doc_id}.jpg")
            render_pdf(ops, folder / f"{doc_id}.pdf")
            manifest.append({"id": doc_id, "template": tpl.name,
                             "image": f"{doc_id}.jpg", "pdf": f"{doc_id}.pdf",
                             "truth": json.loads(inv.model_dump_json()),
                             "oracle_words": oracle_boxes(ops)})
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--dev", type=int, default=10, help="documents per dev template")
    ap.add_argument("--test", type=int, default=25, help="documents per test template")
    ap.add_argument("--final", type=int, default=20, help="documents per fresh-stress template")
    ap.add_argument("--holdout", type=int, default=20, help="documents per holdout template")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    generate(args.out, "dev", args.dev, args.seed)
    generate(args.out, "test", args.test, args.seed)
    generate(args.out, "final", args.final, args.seed)
    generate(args.out, "holdout", args.holdout, args.seed)


if __name__ == "__main__":
    main()
