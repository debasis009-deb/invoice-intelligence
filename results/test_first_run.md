## test split (75 documents)

| Metric | oracle | pdf-text | rapidocr | tesseract |
|---|---|---|---|---|
| invoice_no correct | 100.0% | 100.0% | 85.3% | 100.0% |
| date correct | 100.0% | 100.0% | 96.0% | 100.0% |
| vendor correct | 100.0% | 100.0% | 100.0% | 100.0% |
| currency correct | 100.0% | 100.0% | 100.0% | 98.7% |
| subtotal correct | 100.0% | 100.0% | 100.0% | 100.0% |
| discount correct | 100.0% | 100.0% | 100.0% | 100.0% |
| rounding correct | 100.0% | 100.0% | 100.0% | 100.0% |
| total correct | 100.0% | 100.0% | 100.0% | 100.0% |
| tax_total correct | 100.0% | 100.0% | 72.0% | 98.7% |
| items correct | 100.0% | 100.0% | 89.3% | 38.7% |
| all fields correct | 100.0% | 100.0% | 45.3% | 37.3% |
| item F1 | 1.000 | 1.000 | 0.971 | 0.853 |
| flagged for review | 0 | 0 | 31 | 46 |
| wrong documents | 0 | 0 | 41 | 47 |
| wrong and NOT flagged | 0 | 0 | 10 | 1 |
| flag precision | None | None | 1.0 | 1.0 |
| flag recall | None | None | 0.756 | 0.979 |
| seconds per document (this machine) | 0.0 | 0.02 | 2.09 | 1.51 |
| all fields correct, template H1 | 100.0% | 100.0% | 16.0% | 56.0% |
| all fields correct, template H2 | 100.0% | 100.0% | 56.0% | 32.0% |
| all fields correct, template H3 | 100.0% | 100.0% | 64.0% | 24.0% |
