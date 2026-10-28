"""Label-based rule extractor from OCR text lines (baseline), plus helpers shared with the geometry extractor.

This is a deterministic baseline, not a learned model: it is easy to test and
every decision can be traced to a line of text. Limits: one page, one table,
single-line item rows, day-first dates, `.` as the decimal separator.
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .ocr import Line
from .schema import Extraction, Invoice, Issue, Item, TaxLine
from .validate import check

LOW_CONFIDENCE = 0.80  # below this OCR confidence a key field goes to review
AMOUNT = re.compile(r"-?\(?\d[\d,]*(?:\.\d+)?\)?")
RATE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
INVOICE_NO = re.compile(
    r"(?:invoice|inv|bill|receipt)\s*(?:no|number|num|#)\.?\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]*)", re.I)
DATE_PATTERNS = (  # (regex, strptime formats)
    (re.compile(r"\b\d{4}[/.-]\d{1,2}[/.-]\d{1,2}\b"), ("%Y %m %d",)),
    (re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b"), ("%d %m %Y",)),
    (re.compile(r"\b\d{1,2}[ -]?[A-Za-z0]{3,9}[ -]?\d{4}\b"), ("%d %B %Y", "%d %b %Y")),
)
# ISO codes are matched without word boundaries because OCR can drop spaces ("inEUR")
CURRENCIES = (("INR", r"INR|\b[Rr][Ss]\.|\u20b9"), ("USD", r"USD|\$"),
              ("EUR", r"EUR|\u20ac"), ("GBP", r"GBP|\u00a3"))
SUBTOTAL = re.compile(r"sub[\s-]*total|taxable\s*(?:value|amount)|net\s*amount|amount\s*before\s*tax", re.I)
DISCOUNT = re.compile(r"discount", re.I)
ROUNDING = re.compile(r"round", re.I)
TAX = re.compile(r"\b(cgst|sgst|igst|utgst|vat|gst|sales\s*tax|tax)(?![a-z])", re.I)  # OCR may glue the rate: VAT20%
TOTAL = re.compile(r"grand\s*total|total\s*(?:amount|due|payable)|amount\s*(?:due|payable)|balance\s*due"
                   r"|net\s*payable|^total\b", re.I)
COLUMNS = (("hsn", re.compile(r"\bhsn\b", re.I)),
           ("qty", re.compile(r"\bqty\b|\bquantity\b", re.I)),
           ("unit_price", re.compile(r"\brate\b|\bprice\b|unit\s+price", re.I)),
           ("amount", re.compile(r"\bamount\b|\bline\s+total\b", re.I)))
DESCRIPTION_HEADER = re.compile(r"\b(description|item|particulars|product)\b", re.I)
TITLE = re.compile(r"\b(?:tax\s*)?(?:invoice|bill|receipt)\s*$", re.I)
SUMMARY = (SUBTOTAL, DISCOUNT, TAX, ROUNDING, TOTAL)


def parse_amount(token: str) -> Decimal | None:
    negative = token.startswith("-") or token.startswith("(")
    try:
        value = Decimal(token.strip("-()").replace(",", ""))
    except InvalidOperation:
        return None
    return -value if negative else value


def last_amount(text: str) -> Decimal | None:
    """The last number on a line, ignoring tax-rate percentages."""
    tokens = [m for m in AMOUNT.finditer(RATE.sub(" ", text))]
    return parse_amount(tokens[-1].group()) if tokens else None


def parse_date(text: str) -> date | None:
    for pattern, formats in DATE_PATTERNS:
        if m := pattern.search(text):
            value = re.sub(r"(?<=\d[ -])0(?=[A-Za-z]{2})", "O", m.group())  # OCR reads Oct as 0ct
            value = re.sub(r"[/.-]", " ", value)  # 05-Jul-2025 -> 05 Jul 2025
            value = re.sub(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)", " ", value)  # OCR glued 23January2025
            for fmt in formats:
                try:
                    return datetime.strptime(" ".join(value.split()), fmt).date()
                except ValueError:
                    continue
    return None


def find_currency(lines: list[Line]) -> str | None:
    text = "\n".join(line.text for line in lines)
    for code, pattern in CURRENCIES:
        if re.search(pattern, text):
            return code
    return None


def parse_items(rows: list[Line], header: Line) -> tuple[list[Item], list[str]]:
    """Items plus the rows that could not be read as items (to be reviewed)."""
    columns = sorted((m.start(), name) for name, rx in COLUMNS if (m := rx.search(header.text)))
    names = [n for _, n in columns]
    items, unparsed = [], []
    for row in rows:
        tokens = strip_symbols(row.text).split()
        values = []
        while tokens and AMOUNT.fullmatch(tokens[-1]) and len(values) < len(names):
            values.insert(0, parse_amount(tokens.pop()))
        if len(values) < len(names):  # a numeric cell is missing or unreadable
            unparsed.append(row.text)
            continue
        cells = dict(zip(names, values))
        cells.pop("hsn", None)
        items.append(Item(description=" ".join(tokens), **cells))
    return items, unparsed


def strip_symbols(text: str) -> str:
    return re.sub(r"\b[Rr][Ss]\.?|[\u20b9$\u20ac\u00a3]|INR|USD|EUR|GBP", " ", text)


def repair(line: Line) -> Line:
    """Fix an OCR split inside a decimal amount: '1,347. 90' -> '1,347.90'."""
    return Line(re.sub(r"(\d)\.\s+(\d{2})\b", r"\1.\2", line.text), line.conf)


def extract(lines: list[Line], engine: str = "") -> Extraction:
    lines = [repair(line) for line in lines]
    inv = Invoice(currency=find_currency(lines))
    conf: dict[str, float] = {}
    unparsed: list[str] = []

    def note(field: str, line: Line) -> None:
        conf[field] = round(line.conf, 3)

    for line in lines:
        if inv.invoice_no is None and (m := INVOICE_NO.search(line.text)):
            inv.invoice_no = m.group(1)
            note("invoice_no", line)
        if inv.date is None and re.search(r"date", line.text, re.I) and (d := parse_date(line.text)):
            inv.date = d
            note("date", line)
    if inv.date is None:  # no labelled date: take the first date anywhere
        for line in lines:
            if d := parse_date(line.text):
                inv.date = d
                note("date", line)
                break

    if lines:  # the letterhead is the first line; drop a document title on the same row
        inv.vendor = TITLE.sub("", lines[0].text).strip() or None
        note("vendor", lines[0])

    header_at = next((i for i, line in enumerate(lines)
                      if DESCRIPTION_HEADER.search(line.text)
                      and sum(bool(rx.search(line.text)) for _, rx in COLUMNS) >= 2), None)
    body = lines[header_at + 1:] if header_at is not None else []
    summary_at = next((i for i, line in enumerate(body) if any(r.search(line.text) for r in SUMMARY)), len(body))
    if header_at is not None:
        inv.items, unparsed = parse_items(body[:summary_at], lines[header_at])
        if inv.items:
            conf["items"] = round(min(line.conf for line in body[:summary_at]), 3)

    for line in body[summary_at:] if header_at is not None else lines:
        amount = last_amount(strip_symbols(line.text))
        if amount is None:
            continue
        if SUBTOTAL.search(line.text):
            inv.subtotal = amount
            note("subtotal", line)
        elif DISCOUNT.search(line.text):
            inv.discount = abs(amount)
        elif ROUNDING.search(line.text):
            inv.rounding = amount
        elif TOTAL.search(line.text):
            inv.total = amount
            note("total", line)
        elif TAX.search(line.text):
            rate = RATE.search(line.text)
            label = TAX.search(line.text).group(1).upper()
            inv.tax_lines.append(TaxLine(label=label, rate=Decimal(rate.group(1)) if rate else None,
                                         amount=amount))
    return finalize(inv, conf, engine, [Issue(code="unparsed_row", message=f"Could not read item row: {row!r}")
                                        for row in unparsed])


def ambiguous_id(number: str) -> bool:
    """OCR swaps I/l/1 and O/0, which no arithmetic check can catch. Flag IDs whose
    first segment mixes letters with digits or mixes upper and lower case."""
    head = re.split(r"[-/]", number)[0]
    return bool(re.search(r"[A-Za-z]", head) and re.search(r"\d", head)) or (
        any(c.isupper() for c in head) and any(c.islower() for c in head))


def finalize(inv: Invoice, conf: dict[str, float], engine: str, extra: list[Issue]) -> Extraction:
    """Run the arithmetic checks and add extractor-specific issues to build the Extraction."""
    issues = check(inv) + extra
    if inv.invoice_no and ambiguous_id(inv.invoice_no):
        issues.append(Issue(code="ambiguous_invoice_no",
                            message=f"Invoice number {inv.invoice_no!r} has characters OCR often confuses (I/l/1, O/0)"))
    for field in ("invoice_no", "date", "total", "items"):
        if field in conf and conf[field] < LOW_CONFIDENCE:
            issues.append(Issue(code="low_confidence",
                                message=f"OCR confidence for {field} is {conf[field]:.2f}"))
    return Extraction(invoice=inv, issues=issues, field_confidence=conf,
                      needs_review=bool(issues), engine=engine)
