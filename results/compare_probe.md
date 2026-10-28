## Geometry vs model on public sample invoices, extraction code 896c729a4b

Development and regression evidence on 10 documents, not a benchmark. Reference values are invoice2data's own JSON; a few references may name a different number than the invoice shows. 'no strict' compares the invoice number exactly, 'no prefix' ignores a leading '#'. Dates are exact ISO. Each cell is correct / 10.

| Mode | Extractor | Total | No strict | No prefix | Date | Flagged | n |
|---|---|---|---|---|---|---|---|
| scan | geometry | 2 | 1 | 1 | 4 | 10 | 10 |
| scan | model | 9 | 8 | 9 | 4 | 10 | 10 |
| text | geometry | 2 | 2 | 2 | 4 | 10 | 10 |
| text | model | 9 | 6 | 7 | 5 | 10 | 10 |

| Document | Mode | Extractor | Total | No strict | No prefix | Date | Flagged | First issues |
|---|---|---|---|---|---|---|---|---|
| AmazonWebServices | text | geometry | False | True | True | False | True | missing_date, line_mismatch, subtotal_mismatch |
| AmazonWebServices | text | model | True | True | True | True | True | model_proposal |
| AmazonWebServices | scan | geometry | False | True | True | False | True | missing_date, missing_total, missing_items |
| AmazonWebServices | scan | model | True | True | True | True | True | model_proposal |
| AzureInterior | text | geometry | True | True | True | False | True | missing_date, tax_rate_mismatch |
| AzureInterior | text | model | True | True | True | False | True | ungrounded_date, model_proposal |
| AzureInterior | scan | geometry | True | False | False | False | True | missing_invoice_no, missing_date, tax_rate_mismatch |
| AzureInterior | scan | model | True | True | True | False | True | ungrounded_date, model_proposal |
| FlipkartInvoice | text | geometry | True | False | False | True | True | missing_invoice_no, line_mismatch, id_looks_like_date |
| FlipkartInvoice | text | model | True | False | True | True | True | model_proposal |
| FlipkartInvoice | scan | geometry | True | False | False | True | True | missing_invoice_no, line_mismatch, id_looks_like_date |
| FlipkartInvoice | scan | model | True | False | True | True | True | model_proposal |
| NetpresseInvoice | text | geometry | False | False | False | True | True | missing_invoice_no, missing_total, missing_items |
| NetpresseInvoice | text | model | True | True | True | True | True | ungrounded_currency, model_proposal |
| NetpresseInvoice | scan | geometry | False | False | False | True | True | missing_invoice_no, missing_total, missing_items |
| NetpresseInvoice | scan | model | True | True | True | True | True | ungrounded_currency, model_proposal |
| QualityHosting | text | geometry | False | False | False | False | True | missing_date, missing_total, missing_items |
| QualityHosting | text | model | False | True | True | False | True | ungrounded_date, ungrounded_currency, ungrounded_total |
| QualityHosting | scan | geometry | False | False | False | False | True | missing_invoice_no, missing_date, missing_total |
| QualityHosting | scan | model | False | True | True | False | True | ungrounded_date, ungrounded_currency, ungrounded_total |
| coolblue1 | text | geometry | False | False | False | True | True | missing_invoice_no, missing_total, missing_items |
| coolblue1 | text | model | True | False | False | True | True | model_proposal |
| coolblue1 | scan | geometry | False | False | False | True | True | missing_invoice_no, missing_total, missing_items |
| coolblue1 | scan | model | True | True | True | False | True | ungrounded_date, ungrounded_currency, model_proposal |
| coolblue2 | text | geometry | False | False | False | False | True | missing_invoice_no, missing_date, missing_total |
| coolblue2 | text | model | True | False | False | False | True | ungrounded_date, model_proposal |
| coolblue2 | scan | geometry | False | False | False | False | True | missing_invoice_no, missing_date, missing_total |
| coolblue2 | scan | model | True | True | True | False | True | ungrounded_date, ungrounded_currency, model_proposal |
| free_fiber | text | geometry | False | False | False | False | True | missing_invoice_no, missing_date, missing_total |
| free_fiber | text | model | True | True | True | False | True | ungrounded_date, model_proposal, multi_page |
| free_fiber | scan | geometry | False | False | False | False | True | missing_invoice_no, missing_date, missing_total |
| free_fiber | scan | model | True | True | True | False | True | ungrounded_date, missing_vendor, model_proposal |
| oyo | text | geometry | False | False | False | True | True | missing_invoice_no, missing_total, missing_items |
| oyo | text | model | True | True | True | True | True | model_proposal |
| oyo | scan | geometry | False | False | False | True | True | missing_invoice_no, missing_total, missing_items |
| oyo | scan | model | True | True | True | True | True | model_proposal |
| saeco | text | geometry | False | False | False | False | True | missing_invoice_no, missing_total, missing_items |
| saeco | text | model | True | False | False | False | True | model_proposal |
| saeco | scan | geometry | False | False | False | False | True | missing_invoice_no, missing_total, missing_items |
| saeco | scan | model | True | False | False | False | True | model_proposal |
