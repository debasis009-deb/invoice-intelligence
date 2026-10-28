"""Geometry-based extraction from OCR text boxes, followed by validation.

Instead of looking for fixed labels, it uses where text sits and whether the numbers add up:

1. The item table is the longest run of lines whose last number is a right-aligned money amount.
   Columns are clusters of right edges; which column is quantity, rate and amount is decided by
   `qty x rate = amount` on the rows (header words are only a fallback).
2. The totals block is the money amounts under the table's amount column. Their roles
   (subtotal, discount, tax, rounding, total) are chosen by solving the invoice arithmetic;
   label words only break ties. Several equally good answers send the invoice to review.
3. Invoice number and date come from "label: value" pairs above the table, scored by value shape.
4. The vendor is the first text on the page.

Supported scope: one page, English, printed, one item table whose number columns are right-aligned,
money amounts with two decimals, a totals block under the table. Anything else should end up
in review (missing fields, failed checks, ambiguity issues), not in a confident wrong answer.
"""
import itertools
import re
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import wordninja

from .ocr import Line, Token, Word, group_words
from .rules import TITLE, find_currency, finalize, parse_amount, parse_date, strip_symbols
from .schema import Extraction, Invoice, Issue, Item, TaxLine
from .validate import TOLERANCE

NUMBER = re.compile(r"-?\(?[\d,]*\d(?:\.\d+)?\)?")
MONEY = re.compile(r"-?\(?[\d,]*\d\.\d{2}\)?")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
TAX_WORDS = re.compile(r"(cgst|sgst|igst|utgst|vat|gst|sales\s*tax|tax)(?![a-z])", re.I)
ID_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/]{2,}")
ID_LABEL = re.compile(r"\b(no|number|num|nr|#|ref|reference|id)\b|inv|bill|receipt|document|voucher", re.I)
NOT_ID_LABEL = re.compile(r"tax\s*id|gstin|vat\s*id|pan\b|phone|tel|account|order(?!\s*inv)|po\b|bill\s*to|customer", re.I)
DATE_LIKE = re.compile(r"\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}")
DATE_LABEL = re.compile(r"date|issued|dated", re.I)
DUE_LABEL = re.compile(r"\bdue\b", re.I)
HINTS = {
    "subtotal": re.compile(r"sub[\s-]*total|taxable|net\s*(?:amount|total)|before\s*tax|total\s*before", re.I),
    "discount": re.compile(r"discount|less|rebate|concession|allowance", re.I),
    "tax": TAX_WORDS,
    "rounding": re.compile(r"round|adjust", re.I),
    "total": re.compile(r"total|payable|balance|amount\s*due|\bdue\b", re.I),
}
COLUMN_HINTS = {"qty": re.compile(r"qty|quantity|units", re.I), "unit_price": re.compile(r"rate|price", re.I)}
ROLES = ("ignore", "subtotal", "discount", "tax", "rounding")
MAX_CANDIDATES = 7  # 5^6 role combinations per total choice stays fast


@dataclass
class Amount:
    value: Decimal
    x1: float
    conf: float


def number_of(token: Token) -> Decimal | None:
    text = strip_symbols(token.text).strip()
    if not NUMBER.fullmatch(text):
        return None
    return parse_amount(text)


def numbers(line: Line) -> list[tuple[Token, Decimal]]:
    return [(t, v) for t in line.tokens if (v := number_of(t)) is not None]


def is_money(token: Token) -> bool:
    return bool(MONEY.fullmatch(strip_symbols(token.text).strip()))


def table_rows(lines: list[Line]) -> list[int]:
    """Indexes of the longest run of item-like lines: >=2 numbers, last one a money amount,
    last-number right edges aligned. Lines without numbers inside the run are wrapped descriptions."""
    def candidate(i: int) -> float | None:
        nums = numbers(lines[i])
        return nums[-1][0].x1 if len(nums) >= 2 and is_money(nums[-1][0]) else None

    best: list[int] = []
    run: list[int] = []
    edges: list[float] = []
    for i, line in enumerate(lines):
        edge = candidate(i)
        height = line.height or 20
        if edge is not None and (not edges or abs(edge - statistics.median(edges)) <= 2 * height):
            run.append(i)
            edges.append(edge)
        elif edge is None and run and not numbers(line) and i + 1 < len(lines) and candidate(i + 1) is not None:
            run.append(i)  # wrapped description line
        else:
            run, edges = ([i], [edge]) if edge is not None else ([], [])
        rows = [j for j in run if candidate(j) is not None]
        if len(rows) > len([j for j in best if candidate(j) is not None]):
            best = list(run)
    return best


def cluster(values: list[float], tolerance: float) -> list[float]:
    """Cluster centres of sorted values split at gaps larger than tolerance."""
    groups: list[list[float]] = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= tolerance:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [statistics.mean(g) for g in groups]


def build_items(lines: list[Line], rows: list[int], header: Line | None,
                notes: list[Issue]) -> tuple[list[Item], float]:
    height = statistics.median(lines[i].height or 20 for i in rows)
    cells = {i: numbers(lines[i]) for i in rows}
    wrapped = [i for i in rows if not cells[i]]
    rows = [i for i in rows if cells[i]]
    centres = cluster([t.x1 for i in rows for t, _ in cells[i]], 1.5 * height)
    centres = [c for c in centres if sum(1 for i in rows for t, _ in cells[i]
                                         if abs(t.x1 - c) <= 1.5 * height) >= max(1, len(rows) // 3)]
    table: list[dict[int, tuple[Decimal, float]]] = []
    for i in rows:
        row: dict[int, tuple[Decimal, float]] = {}
        for token, value in cells[i]:
            col = min(range(len(centres)), key=lambda c: abs(centres[c] - token.x1))
            if abs(centres[col] - token.x1) <= 1.5 * height:
                row[col] = (value, token.conf)
        table.append(row)

    amount_col = len(centres) - 1
    roles = {amount_col: "amount"}
    others = list(range(amount_col))
    ok = {}
    for a, b in itertools.combinations(others, 2):
        hits = [r for r in table if {a, b, amount_col} <= r.keys()]
        ok[(a, b)] = sum(abs(r[a][0] * r[b][0] - r[amount_col][0]) <= TOLERANCE * 2 for r in hits) / max(len(table), 1)
    best = max(ok, key=ok.get, default=None)
    if best and ok[best] >= 0.5:
        a, b = best  # the product is symmetric: the all-integer column is the quantity, else left to right
        whole = [all(r[c][0] == r[c][0].to_integral_value() for r in table if c in r) for c in best]
        qty, rate = (b, a) if whole[1] and not whole[0] else (a, b)
        roles[qty], roles[rate] = "qty", "unit_price"
    else:
        hints = column_hints(header, centres, others)
        if hints:
            roles.update(hints)
        elif len(others) >= 2:  # convention: ... qty, rate, amount
            roles[others[-2]], roles[others[-1]] = "qty", "unit_price"
            notes.append(Issue(code="ambiguous_columns", message="Could not tell which table columns are quantity and rate"))
        elif others:
            roles[others[-1]] = "unit_price"
            notes.append(Issue(code="ambiguous_columns", message="Could not tell what the extra table column is"))

    items, owner = [], {}
    for i, row in zip(rows, table):
        desc_tokens = [t.text for t in lines[i].tokens
                       if number_of(t) is None and strip_symbols(t.text).strip()]  # drop USD, Rs. ...
        fields = {roles[c]: v for c, (v, _) in row.items() if c in roles}
        if "amount" not in fields:
            notes.append(Issue(code="unparsed_row", message=f"No amount found in item row: {lines[i].text!r}"))
            continue
        if len(fields) < 3 and len(roles) >= 3:
            notes.append(Issue(code="incomplete_row", message=f"Missing quantity or rate in: {lines[i].text!r}"))
        raw = " ".join(desc_tokens)
        spaced = respace(raw)
        if spaced != raw:
            notes.append(Issue(code="spacing_guessed", message=f"Spaces were guessed in the description {raw!r} -> {spaced!r}; check it"))
        items.append(Item(description=spaced, **fields))
        owner[i] = items[-1]
    for i in wrapped:  # a line without numbers continues the item above it
        above = [j for j in owner if j < i]
        if above:
            owner[max(above)].description += " " + lines[i].text
    return items, min((c for r in table for _, c in r.values()), default=1.0)


def column_hints(header: Line | None, centres: list[float], others: list[int]) -> dict[int, str]:
    if header is None:
        return {}
    found = {}
    for token in header.tokens:
        for role, rx in COLUMN_HINTS.items():
            if rx.search(token.text):
                col = min(range(len(centres)), key=lambda c: abs(centres[c] - token.x1))
                if col in others:
                    found[col] = role
    return found


def totals_candidates(lines: list[Line], start: int, amount_edge: float) -> list[tuple[str, Amount]]:
    out = []
    for line in lines[start:]:
        nums = numbers(line)
        if not nums or not is_money(nums[-1][0]):
            continue
        token, value = nums[-1]
        if abs(token.x1 - amount_edge) > 2.5 * (line.height or 20):
            continue
        label = " ".join(t.text for t in line.tokens if t is not token and number_of(t) is None)
        out.append((label, Amount(value, token.x1, token.conf)))
    return out


def label_cost(label: str, role: str) -> float:
    hinted = [r for r, rx in HINTS.items() if rx.search(label) and not (r == "total" and HINTS["subtotal"].search(label))]
    if role in hinted:
        return 0.0
    if hinted:
        return 1.0
    return 0.0 if role == "ignore" else 0.2


def solve_totals(cands: list[tuple[str, Amount]], items_sum: Decimal | None) -> tuple[dict, list[Issue]]:
    """Assign a role to each amount so that total = subtotal - discount + tax + rounding."""
    if not cands:
        return {}, []
    if len(cands) > MAX_CANDIDATES:
        cands = cands[-MAX_CANDIDATES:]
    best: list[tuple[float, dict]] = []
    for t_idx, (t_label, t_amt) in enumerate(cands):
        t_cost = (0.0 if HINTS["total"].search(t_label) and not HINTS["subtotal"].search(t_label) else 0.5) \
            + 0.3 * (len(cands) - 1 - t_idx)
        rest = [(i, c) for i, c in enumerate(cands) if i != t_idx]
        for combo in itertools.product(ROLES, repeat=len(rest)):
            if combo.count("subtotal") > 1 or combo.count("rounding") > 1 or combo.count("discount") > 1:
                continue
            cost, sub, disc, tax, rnd, taxes = t_cost, None, Decimal(0), Decimal(0), Decimal(0), []
            feasible = True
            for role, (i, (label, amt)) in zip(combo, rest):
                cost += label_cost(label, role)
                if role == "subtotal":
                    sub = amt.value
                    cost -= 0.5 if items_sum is not None and abs(amt.value - items_sum) <= TOLERANCE else 0
                elif role == "discount":
                    disc = abs(amt.value)
                    if abs(amt.value) <= 1 and not HINTS["discount"].search(label):
                        cost += 0.3  # a small unlabelled adjustment is more likely rounding
                elif role == "tax":
                    feasible &= amt.value >= 0
                    tax += amt.value
                    taxes.append((label, amt))
                elif role == "rounding":
                    feasible &= abs(amt.value) <= 1
                    rnd = amt.value
            base = sub if sub is not None else items_sum
            if not feasible or base is None:
                continue
            if abs(base - disc + tax + rnd - t_amt.value) > TOLERANCE:
                continue
            fields = {"total": t_amt, "subtotal": sub, "discount": disc, "rounding": rnd, "taxes": taxes}
            best.append((cost, fields))
    if best:
        best.sort(key=lambda b: b[0])
        top = [f for c, f in best if c <= best[0][0] + 0.05]
        signatures = {(f["total"].value, f["subtotal"], f["discount"], f["rounding"],
                       tuple(a.value for _, a in f["taxes"])) for f in top}
        notes = []
        if len(signatures) > 1:
            notes.append(Issue(code="ambiguous_totals", message="More than one way to read the totals block fits the arithmetic"))
        return best[0][1], notes
    return label_fallback(cands), []


def label_fallback(cands: list[tuple[str, Amount]]) -> dict:
    """Arithmetic did not close (OCR error?): read roles from labels so the checks can flag it."""
    fields = {"total": None, "subtotal": None, "discount": Decimal(0), "rounding": Decimal(0), "taxes": []}
    for label, amt in cands:
        if HINTS["subtotal"].search(label) and fields["subtotal"] is None:
            fields["subtotal"] = amt.value
        elif HINTS["discount"].search(label):
            fields["discount"] = abs(amt.value)
        elif HINTS["rounding"].search(label):
            fields["rounding"] = amt.value
        elif TAX_WORDS.search(label) or PERCENT.search(label):
            fields["taxes"].append((label, amt))
        elif HINTS["total"].search(label):
            fields["total"] = amt
    if fields["total"] is None:
        fields["total"] = cands[-1][1]
    return fields


def respace(text: str) -> str:
    """OCR often drops the spaces inside a text box ("Testtuberack", "chain5m"). Put them back with a
    dictionary word splitter (wordninja). Known words, short words, codes and numbers are left alone."""
    out = []
    for token in text.split():
        parts = re.sub(r"(?<=[A-Za-z]{4})(?=\d)", " ", token).split()  # chain5m -> chain 5m
        for part in parts:
            known = wordninja.DEFAULT_LANGUAGE_MODEL._wordcost.get(part.lower()) is not None
            if part.isalpha() and len(part) >= 7 and not known and not part.isupper():
                out += wordninja.split(part)
            else:
                out.append(part)
    return " ".join(out)


def vendor_name(line: Line) -> str | None:
    """First text on the page. A document title on the same row is cut off at the first wide
    horizontal gap; when OCR merged both into one box, a known title word at the end is removed."""
    kept = [line.tokens[0]]
    for token in line.tokens[1:]:
        if token.x0 - kept[-1].x1 > 3 * (line.height or 20):
            break
        kept.append(token)
    name = TITLE.sub("", " ".join(t.text for t in kept)).strip()
    return re.sub(r"(?<=[a-z]{2})(?=[A-Z][a-z])", " ", name) or None  # TandoorSpice -> Tandoor Spice


def read_header(lines: list[Line], conf: dict[str, float]) -> tuple[str | None, date | None, list[Issue]]:
    """Invoice number and date from `label: value` pairs above the table."""
    numbers_found: list[tuple[int, str, float]] = []
    date_like: list[str] = []
    dates: list[tuple[int, date, float]] = []
    for line in lines:
        label, sep, value = line.text.partition(":")
        if not sep:  # no colon: label words followed by the value, as in "InvoiceNo INV-1"
            label, value = line.text.rsplit(" ", 1) if " " in line.text else (line.text, "")
        value = value.strip()
        if (d := parse_date(value or label)) and not NOT_ID_LABEL.search(label):
            if ID_LABEL.search(label) and not DATE_LABEL.search(label):  # "Invoice No: 2025/08/23"
                date_like.append(value or label)
                continue
            rank = 0 if DATE_LABEL.search(label) and not DUE_LABEL.search(label) else 2 if DUE_LABEL.search(label) else 1
            dates.append((rank, d, line.conf))
            continue
        id_match = ID_VALUE.fullmatch(value.split(" ")[0]) if value else None
        if id_match and DATE_LIKE.fullmatch(id_match.group()):  # a date is not an invoice number
            date_like.append(id_match.group())
            continue
        if id_match and re.search(r"\d", id_match.group()) and ID_LABEL.search(label) and not NOT_ID_LABEL.search(label):
            rank = 0 if re.search(r"invoice|inv|bill|receipt|document", label, re.I) else 1
            numbers_found.append((rank, id_match.group(), line.conf))
    notes = []
    number = pick(numbers_found, "invoice_no", "invoice number", conf, notes)
    day = pick(dates, "date", "date", conf, notes)
    if date_like and not number:
        notes.append(Issue(code="id_looks_like_date", message=f"The invoice number field holds a date-like value ({date_like[0]}); it was not used as the invoice number"))
    return number, day, notes


def pick(found: list, field: str, name: str, conf: dict[str, float], notes: list[Issue]):
    if not found:
        return None
    found.sort(key=lambda f: f[0])
    best = [f for f in found if f[0] == found[0][0]]
    if len({f[1] for f in best}) > 1:
        notes.append(Issue(code=f"ambiguous_{field}", message=f"More than one possible {name}: "
                           + ", ".join(sorted({str(f[1]) for f in best}))))
    conf[field] = round(best[0][2], 3)
    return best[0][1]


def extract(words: list[Word], engine: str = "") -> Extraction:
    lines = group_words(words)
    inv = Invoice(currency=find_currency(lines))
    conf: dict[str, float] = {}
    notes: list[Issue] = []
    rows = table_rows(lines) if lines else []
    first = rows[0] if rows else len(lines)
    header_line = lines[first - 1] if rows and first > 0 and not numbers(lines[first - 1]) else None
    above = lines[:first - (1 if header_line else 0)]

    if lines and lines[0].tokens:
        inv.vendor = vendor_name(lines[0])
        conf["vendor"] = round(lines[0].conf, 3)
    inv.invoice_no, inv.date, header_notes = read_header(above, conf)
    notes += header_notes

    if rows:
        inv.items, conf["items"] = build_items(lines, rows, header_line, notes)
        edge = statistics.median(numbers(lines[i])[-1][0].x1 for i in rows if numbers(lines[i]))
        cands = totals_candidates(lines, rows[-1] + 1, edge)
        items_sum = sum((i.amount for i in inv.items), Decimal(0)) if inv.items else None
        fields, totals_notes = solve_totals(cands, items_sum)
        notes += totals_notes
        if fields:
            inv.total, inv.subtotal = fields["total"].value, fields["subtotal"]
            conf["total"] = round(fields["total"].conf, 3)
            inv.discount, inv.rounding = fields["discount"], fields["rounding"]
            inv.tax_lines = [TaxLine(label=(m.group(1).upper() if (m := TAX_WORDS.search(label)) else "TAX"),
                                     rate=Decimal(p.group(1)) if (p := PERCENT.search(label)) else None,
                                     amount=amt.value) for label, amt in fields["taxes"]]
    else:
        notes.append(Issue(code="no_table", message="No item table with right-aligned money amounts was found"))
    return finalize(inv, conf, engine, notes)
