## Public sample invoices (invoice2data test files, MIT), extraction code 896c729a4b

PDF text layer only. Reference values come from that project, not from this repository. Compared fields: total, invoice number, date.

- Documents: 10; total correct: 2; total, number and date all correct: 0; flagged for review: 10

| Document | Flagged | Total | Invoice no | Date | First issues |
|---|---|---|---|---|---|
| AmazonWebServices | yes | wrong | ok | wrong | missing_date, line_mismatch, subtotal_mismatch, ambiguous_columns |
| AzureInterior | yes | ok | ok | wrong | missing_date, tax_rate_mismatch |
| FlipkartInvoice | yes | ok | wrong | ok | missing_invoice_no, line_mismatch, id_looks_like_date |
| NetpresseInvoice | yes | wrong | wrong | ok | missing_invoice_no, missing_total, missing_items, no_table |
| QualityHosting | yes | wrong | wrong | wrong | missing_date, missing_total, missing_items, no_table |
| coolblue1 | yes | wrong | wrong | ok | missing_invoice_no, missing_total, missing_items, no_table |
| coolblue2 | yes | wrong | wrong | wrong | missing_invoice_no, missing_date, missing_total, missing_items |
| free_fiber | yes | wrong | wrong | wrong | missing_invoice_no, missing_date, missing_total, line_mismatch |
| oyo | yes | wrong | wrong | ok | missing_invoice_no, missing_total, missing_items, no_table |
| saeco | yes | wrong | wrong | wrong | missing_invoice_no, missing_total, missing_items, no_table |
