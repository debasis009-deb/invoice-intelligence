from datetime import date
from decimal import Decimal as D

from invoice_ai.schema import Invoice, Item, TaxLine
from invoice_ai.validate import check



def invoice(**changes) -> Invoice:
    base = dict(invoice_no="A-1", date=date(2026, 1, 5), items=[
        Item(description="Pen", qty=D(10), unit_price=D("2.50"), amount=D("25.00")),
        Item(description="Book", qty=D(2), unit_price=D("50.00"), amount=D("100.00"))],
        subtotal=D("125.00"), total=D("125.00"))
    return Invoice(**{**base, **changes})


def codes(inv: Invoice) -> list[str]:
    return [i.code for i in check(inv)]


def test_consistent_invoice_has_no_issues():
    assert codes(invoice()) == []


def test_discount_two_tax_lines_and_rounding():
    inv = invoice(discount=D("12.50"),
                  tax_lines=[TaxLine(label="CGST", rate=D(9), amount=D("10.13")),
                             TaxLine(label="SGST", rate=D(9), amount=D("10.13"))],
                  rounding=D("-0.76"), total=D("132.00"))
    assert codes(inv) == []


def test_wrong_total_is_flagged_not_rejected():
    assert codes(invoice(total=D("152.00"))) == ["total_mismatch"]


def test_line_and_subtotal_mismatch():
    inv = invoice(subtotal=D("130.00"), total=D("130.00"))
    inv.items[0].amount = D("20.00")
    assert set(codes(inv)) == {"line_mismatch", "subtotal_mismatch"}


def test_tax_rate_mismatch():
    tax = TaxLine(label="VAT", rate=D(20), amount=D("5.00"))
    assert "tax_rate_mismatch" in codes(invoice(tax_lines=[tax], total=D("130.00")))


def test_tax_inclusive_invoice_goes_to_review():
    # total already contains tax and no tax line adds up: must not pass silently
    inv = invoice(total=D("147.50"), tax_lines=[TaxLine(label="VAT", rate=D(18), amount=D("22.50"))])
    assert codes(inv) == []  # consistent when tax is added on top
    inv = invoice(total=D("125.00"), tax_lines=[TaxLine(label="VAT", rate=D(18), amount=D("22.50"))])
    assert "total_mismatch" in codes(inv)


def test_missing_fields_are_reported():
    assert set(codes(Invoice())) == {"missing_invoice_no", "missing_date", "missing_total", "missing_items"}
