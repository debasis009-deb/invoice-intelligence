"""Arithmetic and completeness checks. Checks never reject an invoice: they
produce issues that send it to human review.

Assumed model: total = (subtotal - discount) + sum(tax lines) + rounding,
where tax lines are generic (CGST/SGST/IGST/VAT/...). Tax-inclusive invoices
do not fit this model and will be flagged for review, not silently accepted.
"""
from decimal import Decimal

from .schema import Invoice, Issue

TOLERANCE = Decimal("0.02")
REQUIRED = {"invoice_no": "invoice number", "date": "date", "total": "total"}


def check(inv: Invoice) -> list[Issue]:
    out = [Issue(code=f"missing_{f}", message=f"No {name} found")
           for f, name in REQUIRED.items() if getattr(inv, f) is None]
    if not inv.items:
        out.append(Issue(code="missing_items", message="No line items found"))

    for n, it in enumerate(inv.items, 1):
        if None not in (it.qty, it.unit_price, it.amount):
            expected = it.qty * it.unit_price
            if abs(expected - it.amount) > TOLERANCE:
                out.append(Issue(code="line_mismatch", message=(
                    f"Line {n}: {it.qty} x {it.unit_price} = {expected}, read {it.amount}")))

    amounts = [it.amount for it in inv.items]
    items_sum = sum(amounts, Decimal(0)) if amounts and None not in amounts else None
    if inv.subtotal is not None and items_sum is not None \
            and abs(items_sum - inv.subtotal) > TOLERANCE:
        out.append(Issue(code="subtotal_mismatch", message=(
            f"Items add up to {items_sum}, subtotal read {inv.subtotal}")))

    base = inv.subtotal if inv.subtotal is not None else items_sum
    if base is not None and inv.total is not None:
        expected = base - inv.discount + inv.tax_total + inv.rounding
        if abs(expected - inv.total) > TOLERANCE:
            out.append(Issue(code="total_mismatch", message=(
                f"Expected total {expected}, read {inv.total}")))

    if base is not None:
        slack = TOLERANCE * max(1, len(inv.items))  # per-line tax rounding
        for tax in inv.tax_lines:
            if tax.rate is None:
                continue
            bases = (base, base - inv.discount)
            if all(abs(b * tax.rate / 100 - tax.amount) > slack for b in bases):
                out.append(Issue(code="tax_rate_mismatch", message=(
                    f"{tax.label} {tax.rate}% of {base} does not give {tax.amount}")))
    return out
