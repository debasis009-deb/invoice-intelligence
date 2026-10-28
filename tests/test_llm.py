"""Model engine: checks run without a model by feeding canned model answers."""
import json
from datetime import date
from decimal import Decimal

import pytest

from invoice_ai import llm
from invoice_ai.ocr import Word

TEXT = "Acme Traders\nInvoice No: AB-1001\nDate: 05/07/2025\nGrand Total Rs 1,250.00"


def codes(result):
    return [i.code for i in result.issues]


def test_grounded_answer_is_kept_but_still_flagged():
    answer = json.dumps({"vendor": "Acme Traders", "invoice_number": "AB-1001", "invoice_date": "2025-07-05",
                         "currency": "INR", "total_amount": 1250})
    r = llm.build(answer, TEXT, "pdf-text")
    assert r.invoice.invoice_no == "AB-1001" and r.invoice.total == Decimal("1250.00")
    assert r.invoice.date == date(2025, 7, 5) and r.invoice.currency == "INR"
    assert r.needs_review and "model_proposal" in codes(r)


def test_values_not_in_the_text_are_dropped_with_an_issue():
    answer = json.dumps({"vendor": "Other Ltd", "invoice_number": "ZZ-9", "invoice_date": "2024-01-01",
                         "currency": "USD", "total_amount": 99.5})
    r = llm.build(answer, TEXT, "pdf-text")
    assert r.invoice.vendor is None and r.invoice.invoice_no is None and r.invoice.total is None
    assert r.invoice.date is None and r.invoice.currency is None
    assert {"ungrounded_vendor", "ungrounded_invoice_no", "ungrounded_date", "ungrounded_total", "ungrounded_currency"} <= set(codes(r))


def test_day_month_swap_risk_is_flagged():
    r = llm.build(json.dumps({"invoice_date": "05/07/2025"}), TEXT, "pdf-text")
    assert r.invoice.date == date(2025, 7, 5) and "ambiguous_date" in codes(r)


def test_european_amount_and_month_first_date_are_grounded():
    text = "Total 1.234,56\nDue January 5, 2025"
    r = llm.build(json.dumps({"total_amount": 1234.56, "invoice_date": "2025-01-05"}), text, "")
    assert r.invoice.total == Decimal("1234.56") and r.invoice.date == date(2025, 1, 5)


@pytest.mark.parametrize("answer", ["not json", "[1, 2]", '{"total_amount": "abc", "invoice_number": {"a": 1}}'])
def test_bad_model_output_gives_empty_flagged_result(answer):
    r = llm.build(answer, TEXT, "")
    assert r.needs_review and r.invoice.total is None and r.invoice.invoice_no is None


def test_model_failure_goes_to_review_without_guessing(monkeypatch):
    def boom(text):
        raise RuntimeError("out of memory")
    monkeypatch.setattr(llm, "ask", boom)
    r = llm.extract_with_model([Word("hello", 0, 40, 10, 10, 1.0)], "x")
    assert r.needs_review and codes(r)[0] == "model_failed" and r.invoice.total is None


def test_model_engine_needs_explicit_setup(monkeypatch):
    monkeypatch.delenv("INVOICE_MODEL_PATH", raising=False)
    with pytest.raises(ValueError, match="INVOICE_MODEL_PATH"):
        llm.model_path()
