## final split (40 documents), extraction code a65ac89e52

| Metric | oracle | pdf-text | rapidocr | tesseract |
|---|---|---|---|---|
| invoice_no correct | 100.0% | 100.0% | 85.0% | 100.0% |
| date correct | 100.0% | 100.0% | 97.5% | 100.0% |
| vendor correct | 100.0% | 100.0% | 100.0% | 100.0% |
| currency correct | 100.0% | 100.0% | 100.0% | 97.5% |
| subtotal correct | 100.0% | 100.0% | 100.0% | 65.0% |
| discount correct | 100.0% | 100.0% | 100.0% | 80.0% |
| rounding correct | 100.0% | 100.0% | 100.0% | 82.5% |
| total correct | 100.0% | 100.0% | 100.0% | 65.0% |
| tax_total correct | 100.0% | 100.0% | 100.0% | 85.0% |
| items correct | 100.0% | 100.0% | 72.5% | 35.0% |
| all fields correct (normalized text) | 100.0% | 100.0% | 67.5% | 30.0% |
| all fields exact text (case and spaces as written) | 100.0% | 100.0% | 5.0% | 30.0% |
| item F1 | 1.000 | 1.000 | 0.920 | 0.654 |
| flagged for review | 0 | 0 | 13 | 31 |
| wrong documents | 0 | 0 | 13 | 28 |
| wrong and NOT flagged | 0 | 0 | 0 | 2 |
| flag precision | None | None | 1.0 | 0.839 |
| flag recall | None | None | 1.0 | 0.929 |
| seconds per document (this machine) | 0.0 | 0.02 | 2.15 | 1.37 |
| all fields correct, template S1 | 100.0% | 100.0% | 100.0% | 45.0% |
| all fields correct, template S2 | 100.0% | 100.0% | 35.0% | 15.0% |
