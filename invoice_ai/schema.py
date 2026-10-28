"""Data model for an extracted invoice. Money uses Decimal, never float."""
from datetime import date as Date
from decimal import Decimal

from pydantic import BaseModel, Field


class Item(BaseModel):
    description: str
    qty: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class TaxLine(BaseModel):
    label: str
    rate: Decimal | None = None  # percent, e.g. 9 for 9%
    amount: Decimal


class Invoice(BaseModel):
    vendor: str | None = None
    invoice_no: str | None = None
    date: Date | None = None
    currency: str | None = None
    items: list[Item] = Field(default_factory=list)
    subtotal: Decimal | None = None
    discount: Decimal = Decimal(0)
    tax_lines: list[TaxLine] = Field(default_factory=list)
    rounding: Decimal = Decimal(0)
    total: Decimal | None = None

    @property
    def tax_total(self) -> Decimal:
        return sum((t.amount for t in self.tax_lines), Decimal(0))


class Issue(BaseModel):
    code: str
    message: str


class Extraction(BaseModel):
    invoice: Invoice
    issues: list[Issue] = Field(default_factory=list)
    field_confidence: dict[str, float] = Field(default_factory=dict)
    needs_review: bool = False
    engine: str = ""
