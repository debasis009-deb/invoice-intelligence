"""Optional extractor that asks a small local language model for the header fields.

Off by default. Needs `pip install -r requirements-model.txt` and a GGUF model file you download yourself,
with its path in the INVOICE_MODEL_PATH environment variable. Nothing is downloaded automatically.
The model's answer is only a proposal: every value is parsed, normalised and checked against the page text,
anything that fails goes to review as an issue and the field is left empty. The result is always flagged for review.
It reads the header fields (vendor, invoice number, date, currency, total), not the item table.
"""
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .ocr import Word, group_words
from .rules import DATE_PATTERNS, parse_date
from .schema import Extraction, Invoice, Issue

PROMPT = ('You extract fields from invoice text. Reply with JSON only: {"vendor": str|null, "invoice_number": str|null, '
          '"invoice_date": "YYYY-MM-DD"|null, "currency": str|null, "total_amount": number|null}. '
          'total_amount is the final amount to pay. Use null when a field is not in the text. Do not guess.')
CURRENCIES = {"INR", "USD", "EUR", "GBP"}
MAX_TEXT = 7000
MAX_TOKENS = 150
_llm = None


def model_path() -> str:
    path = os.environ.get("INVOICE_MODEL_PATH", "")
    if not path or not os.path.isfile(path):
        raise ValueError("Model engine is not set up: set INVOICE_MODEL_PATH to a downloaded .gguf file (see README)")
    return path


def _load():
    global _llm
    if _llm is None:
        try:
            from llama_cpp import Llama
        except ImportError as err:
            raise ValueError("Model engine needs: pip install -r requirements-model.txt") from err
        _llm = Llama(model_path(), n_ctx=4096, n_threads=os.cpu_count() or 2, verbose=False)
    return _llm


def ask(text: str) -> str:
    result = _load().create_chat_completion(
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": text[:MAX_TEXT]}],
        response_format={"type": "json_object"}, temperature=0, max_tokens=MAX_TOKENS)
    return result["choices"][0]["message"]["content"]


MONTH_FIRST = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*,\s*(\d{4})\b")
CURRENCY_TEXT = {"INR": r"INR|\bRs\b|\u20b9", "USD": r"USD|\$", "EUR": r"EUR|\u20ac", "GBP": r"GBP|\u00a3"}


def dates_in(text: str) -> set[date]:
    """Every date written in the text: ISO, day-first numeric, '5 Jan 2025' and 'January 5, 2025'."""
    found = set()
    for pattern, _ in DATE_PATTERNS:
        for match in pattern.finditer(text):
            if d := parse_date(match.group()):
                found.add(d)
    for month, day, year in MONTH_FIRST.findall(text):
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                found.add(datetime.strptime(f"{month} {day} {year}", fmt).date())
                break
            except ValueError:
                continue
    return found


def amounts_in(text: str) -> set[Decimal]:
    """Every number in the text, read both as 1,234.56 and as 1.234,56."""
    out = set()
    for token in re.findall(r"\d[\d.,]*\d|\d", text):
        readings = [token.replace(",", "")]
        if re.search(r",\d{2}$", token):  # decimal comma: 1.234,56
            readings.append(token.replace(".", "").replace(",", "."))
        for value in readings:
            try:
                out.add(Decimal(value))
            except InvalidOperation:
                pass
    return out


def parse_model_date(value) -> tuple[date | None, bool]:
    """Returns (date, ambiguous). ISO dates are exact; other written forms use day-first and are
    ambiguous when day and month could be swapped."""
    if not isinstance(value, str):
        return None, False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            return date.fromisoformat(value.strip()), False
        except ValueError:
            return None, False
    d = parse_date(value)
    return d, bool(d and d.day <= 12 and d.day != d.month)


def build(answer: str, text: str, engine: str) -> Extraction:
    inv, issues = Invoice(), []

    def reject(code: str, message: str):
        issues.append(Issue(code=code, message=message))

    try:
        data = json.loads(answer)
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        reject("model_bad_output", "The model did not return a JSON object; no fields were taken from it")
        data = {}
    squashed = re.sub(r"\s+", "", text).lower()
    vendor, number = data.get("vendor"), data.get("invoice_number")
    if isinstance(vendor, str) and vendor.strip() and re.sub(r"\s+", "", vendor).lower() in squashed:
        inv.vendor = vendor.strip()
    elif vendor:
        reject("ungrounded_vendor", f"Model proposed vendor {vendor!r}, which is not in the page text")
    if isinstance(number, (str, int)) and str(number).strip():
        number = str(number).strip()
        if number.lstrip("#").lower() in squashed:
            inv.invoice_no = number.lstrip("#")
        else:
            reject("ungrounded_invoice_no", f"Model proposed invoice number {number!r}, which is not in the page text")
    day, ambiguous = parse_model_date(data.get("invoice_date"))
    if day and day in dates_in(text):
        inv.date = day
        if ambiguous:
            reject("ambiguous_date", f"Date {day} was written in a form where day and month could be swapped")
    elif data.get("invoice_date"):
        reject("ungrounded_date", f"Model proposed date {data.get('invoice_date')!r}, which is not a date in the page text")
    currency = data.get("currency")
    if isinstance(currency, str) and currency.upper() in CURRENCIES and re.search(CURRENCY_TEXT[currency.upper()], text):
        inv.currency = currency.upper()
    elif currency:
        reject("ungrounded_currency", f"Model proposed currency {currency!r}, which is not confirmed by the page text")
    total = data.get("total_amount")
    try:
        amount = Decimal(str(total)).quantize(Decimal("0.01")) if total is not None and not isinstance(total, bool) else None
    except InvalidOperation:
        amount = None
    if amount is not None and amount in amounts_in(text):
        inv.total = amount
    elif total is not None:
        reject("ungrounded_total", f"Model proposed total {total!r}, which is not an amount in the page text")
    for name, value in (("vendor", inv.vendor), ("invoice_no", inv.invoice_no), ("date", inv.date), ("total", inv.total)):
        if value is None and not any(i.code.endswith(name.replace("_no", "_no")) for i in issues):
            reject(f"missing_{name}", f"No {name.replace('_', ' ')} was extracted")
    issues.append(Issue(code="model_proposal", message="Fields come from a language model and are only checked for presence in the text; "
                                                       "check them. Items were not extracted by this engine"))
    return Extraction(invoice=inv, issues=issues, needs_review=True, engine=engine)


def extract_with_model(words: list[Word], engine: str = "") -> Extraction:
    text = "\n".join(line.text for line in group_words(words))
    try:
        answer = ask(text)
    except ValueError:
        raise  # set-up problems are reported to the caller as they are
    except Exception as err:  # timeouts, memory errors, runtime failures: no guessing, send to review
        return Extraction(invoice=Invoice(), needs_review=True, engine=engine,
                          issues=[Issue(code="model_failed", message=f"The model run failed ({type(err).__name__}); nothing was extracted")])
    return build(answer, text, engine)
