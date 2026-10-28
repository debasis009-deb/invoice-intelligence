"""Geometry extractor tests on hand-built text boxes (no OCR involved)."""
from datetime import date
from decimal import Decimal as D

from invoice_ai.extract import extract
from invoice_ai.ocr import Word

H = 20


def row(y, *cells):
    """cells: (text, right_edge) -> Word boxes on one row; width follows text length."""
    return [Word(text, right - len(text) * 11, right, y, H, 0.97) for text, right in cells]


def page(*rows):
    return [w for r in rows for w in r]


def left(y, text, x=60):
    return Word(text, x, x + len(text) * 11, y, H, 0.97)


def invoice(columns=("qty", "rate"), totals=None, header_rows=None, items=None):
    items = items or [("Pen box", 10, D("2.50")), ("Book", 2, D("50.00"))]
    cols = {"qty": 760, "rate": 960} if columns == ("qty", "rate") else {"rate": 760, "qty": 960}
    words = [left(100, "Acme Traders"), Word("STATEMENT", 900, 1090, 100, H, 0.97)]
    words += header_rows or [left(300, "Document No: DOC-26-0042"), left(330, "Issued: 05 Jul 2025")]
    y = 420
    for name, qty, rate in items:
        cells = {"qty": (str(qty), cols["qty"]), "rate": (f"{rate:,.2f}", cols["rate"])}
        words += row(y, (name, 300), *[cells[c] for c in columns], (f"{qty * rate:,.2f}", 1180))
        y += 34
    for label, value in totals or [("Goods Value", "125.00"), ("Total", "125.00")]:
        y += 34
        words += [left(y, label, 640), *row(y, (value, 1180))]
    return words


def test_labels_are_hints_not_requirements():
    got = extract(invoice()).invoice
    assert (got.vendor, got.invoice_no, got.date) == ("Acme Traders", "DOC-26-0042", date(2025, 7, 5))
    assert (got.subtotal, got.total) == (D("125.00"), D("125.00"))
    assert [(i.qty, i.unit_price, i.amount) for i in got.items] == [
        (D(10), D("2.50"), D("25.00")), (D(2), D("50.00"), D("100.00"))]


def test_unlabeled_adjustment_is_solved_by_arithmetic():
    totals = [("Goods Value", "125.00"), ("Trade concession", "12.50"), ("VAT 10%", "11.25"),
              ("Adjustment", "-0.25"), ("Amount Due", "123.50")]
    result = extract(invoice(totals=totals))
    inv = result.invoice
    assert (inv.subtotal, inv.discount, inv.rounding, inv.total) == (D("125.00"), D("12.50"), D("-0.25"), D("123.50"))
    assert [(t.label, t.rate, t.amount) for t in inv.tax_lines] == [("TAX", D(10), D("11.25"))] or \
        [(t.rate, t.amount) for t in inv.tax_lines] == [(D(10), D("11.25"))]
    assert not result.needs_review


def test_rate_before_quantity_column_order():
    inv = extract(invoice(columns=("rate", "qty"))).invoice
    assert [(i.qty, i.unit_price) for i in inv.items] == [(D(10), D("2.50")), (D(2), D("50.00"))]


def test_wrapped_description_line_does_not_break_the_table():
    words = invoice()
    words += [left(437, "(wrapped note)", 60)]
    assert len(extract(words).invoice.items) == 2


def test_wrong_digit_in_an_amount_is_flagged():
    words = invoice()
    for w in words:
        if w.text == "100.00":
            w.text = "180.00"
    codes = {i.code for i in extract(words).issues}
    assert "line_mismatch" in codes and "subtotal_mismatch" in codes


def test_two_invoice_numbers_with_equal_rank_are_ambiguous():
    header = [left(300, "Invoice No: A-1001"), left(330, "Invoice No: B-2002"), left(360, "Date: 05 Jul 2025")]
    codes = {i.code for i in extract(invoice(header_rows=header)).issues}
    assert "ambiguous_invoice_no" in codes


def test_page_without_a_table_goes_to_review():
    result = extract([left(100, "Hello world"), left(140, "No numbers here")])
    assert result.needs_review and "no_table" in {i.code for i in result.issues}
    assert extract([]).needs_review


def test_due_date_is_not_taken_as_invoice_date():
    header = [left(300, "Invoice No: A-1001"), left(330, "Due Date: 05 Aug 2025"), left(360, "Date: 05 Jul 2025")]
    assert extract(invoice(header_rows=header)).invoice.date == date(2025, 7, 5)


def _header(*pairs):
    from invoice_ai.extract import read_header
    from invoice_ai.ocr import Word, group_words
    words = [Word(text, 10, 10 + 8 * len(text), 20 + 30 * i, 14, 0.99) for i, text in enumerate(pairs)]
    return read_header(group_words(words), {})


def test_year_first_date_with_slashes():
    from datetime import date
    from invoice_ai.rules import parse_date
    assert parse_date("2025/08/23") == date(2025, 8, 23)


def test_order_invoice_label_is_an_invoice_number():
    number, day, notes = _header("Order Invoice #: BL-26-84940", "Billing Date: 2025/08/23")
    assert number == "BL-26-84940" and str(day) == "2025-08-23" and not notes


def test_date_like_value_is_not_an_invoice_number():
    number, day, notes = _header("Invoice No: 2025/08/23")
    assert number is None and [n.code for n in notes] == ["id_looks_like_date"]


def test_small_unlabelled_adjustment_is_rounding():
    from decimal import Decimal as D
    from invoice_ai.extract import Amount, solve_totals
    cands = [("Goods Value", Amount(D("1000.00"), 0, 1)), ("Adjustment", Amount(D("0.15"), 0, 1)),
             ("Amount Due", Amount(D("1000.15"), 0, 1))]
    fields, notes = solve_totals(cands, D("1000.00"))
    assert fields["rounding"] == D("0.15") and fields["discount"] == 0 and not notes


def test_respace_restores_dropped_spaces_but_keeps_codes_and_known_words():
    from invoice_ai.extract import respace
    assert respace("Testtuberack") == "Test tube rack"
    assert respace("Anchor chain5m") == "Anchor chain 5m"
    assert respace("Notebook A5 SKU-1234") == "Notebook A5 SKU-1234"
