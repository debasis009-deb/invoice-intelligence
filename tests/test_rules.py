from datetime import date
from decimal import Decimal as D

import pytest

from invoice_ai.rules import ambiguous_id, extract, parse_amount, parse_date
from invoice_ai.ocr import Line


def lines(*texts: str, conf: float = 0.97) -> list[Line]:
    return [Line(t, conf) for t in texts]


GST = lines(
    "Kaveri Traders TAX INVOICE", "12 Market Road, Pune", "Invoice No: INV-26-00417", "Date: 05-Jul-2025",
    "Description HSN Qty Rate Amount",
    "Notebook A5 4821 10 25.00 Rs. 250.00",
    "Steel bolt M8 7318 4 12.50 Rs. 50.00",
    "Sub Total Rs. 300.00", "Discount Rs. -30.00",
    "CGST @ 9% Rs. 24.30", "SGST @ 9% Rs. 24.30", "Round off Rs. -0.60", "Grand Total Rs. 318.00")


def test_gst_invoice_with_discount_rounding_and_two_tax_lines():
    result = extract(GST)
    inv = result.invoice
    assert (inv.vendor, inv.invoice_no, inv.date, inv.currency) == ("Kaveri Traders", "INV-26-00417", date(2025, 7, 5), "INR")
    assert [(i.description, i.qty, i.unit_price, i.amount) for i in inv.items] == [
        ("Notebook A5", D(10), D("25.00"), D("250.00")), ("Steel bolt M8", D(4), D("12.50"), D("50.00"))]
    assert (inv.subtotal, inv.discount, inv.rounding, inv.total) == (D("300.00"), D(30), D("-0.60"), D("318.00"))
    assert [(t.label, t.rate, t.amount) for t in inv.tax_lines] == [("CGST", D(9), D("24.30")), ("SGST", D(9), D("24.30"))]
    assert result.issues == [] and not result.needs_review


def test_vat_invoice_without_hsn_column_and_currency_suffix():
    result = extract(lines(
        "Orchid Electronics INVOICE", "Invoice Number: R-25-74045", "Date of Issue: 23 January 2025",
        "Description Qty Rate Amount", "Mouse pad 13 120.06 1,560.78 GBP",
        "Subtotal 1,560.78 GBP", "VAT @ 20% 312.16 GBP", "Total 1,872.94 GBP"))
    assert result.invoice.total == D("1872.94") and result.invoice.currency == "GBP"
    assert result.invoice.date == date(2025, 1, 23) and not result.needs_review


def test_ocr_that_drops_spaces_still_extracts():
    result = extract(lines("HarborLightFoods TAXINVOICE", "InvoiceNo:INV-25-68633", "Date:21/08/2026",
                           "Description Qty Rate Amount", "Wall clock 20 640.71 RS.12,814.20",
                           "TaxableValue RS.12,814.20", "Total Rs.12,814.20"))
    inv = result.invoice
    assert (inv.vendor, inv.invoice_no, inv.date, inv.subtotal) == ("HarborLightFoods", "INV-25-68633", date(2026, 8, 21), D("12814.20"))


def test_wrong_digit_is_flagged_by_arithmetic():
    bad = [Line(t.replace("Rs. 250.00", "Rs. 150.00"), 0.97) for t in (l.text for l in GST)]
    codes = {i.code for i in extract(bad).issues}
    assert {"line_mismatch", "subtotal_mismatch"} <= codes


def test_unreadable_item_row_is_flagged_not_dropped_silently():
    broken = [Line(l.text.replace("10 25.00", "25.00"), 0.97) for l in GST]
    result = extract(broken)
    assert "unparsed_row" in {i.code for i in result.issues} and result.needs_review


def test_split_decimal_is_repaired():
    result = extract(lines("Acme", "Invoice No: 1", "Date: 2026-01-02", "Description Qty Rate Amount",
                           "Tape 3 10.00 30. 00", "Subtotal 30. 00", "Total 30. 00"))
    assert result.invoice.total == D("30.00") and not result.needs_review


def test_low_ocr_confidence_on_key_field_goes_to_review():
    result = extract([Line(l.text, 0.5 if "Grand" in l.text else 0.97) for l in GST])
    assert any(i.code == "low_confidence" for i in result.issues)


@pytest.mark.parametrize("text, expected", [
    ("2026-03-04", date(2026, 3, 4)), ("04/03/2026", date(2026, 3, 4)), ("4-Mar-2026", date(2026, 3, 4)),
    ("4 March 2026", date(2026, 3, 4)), ("4March2026", date(2026, 3, 4)), ("no date here", None),
    ("31/02/2026", None)])
def test_parse_date_is_day_first(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("1,234.50", D("1234.50")), ("-30.00", D("-30.00")), ("(30.00)", D("-30.00")), ("12", D(12))])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_glued_tax_rate_and_zero_for_o_in_month():
    result = extract(lines("Acme", "Invoice No: A-1", "Date: 22-0ct-2025", "Description Qty Rate Amount",
                           "Tape 3 10.00 30.00", "Subtotal 30.00", "VAT20% 6.00", "Total 36.00"))
    assert result.invoice.date == date(2025, 10, 22)
    assert [(t.label, t.rate, t.amount) for t in result.invoice.tax_lines] == [("VAT", D(20), D("6.00"))]
    assert not result.needs_review


@pytest.mark.parametrize("number, ambiguous", [("INV-25-00417", False), ("SI-26-1", False), ("S1-26-1", True),
                                               ("Sl-26-1", True), ("2026-0042", False), ("R-25-7", False)])
def test_confusable_invoice_numbers_go_to_review(number, ambiguous):
    assert ambiguous_id(number) is ambiguous
