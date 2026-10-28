## holdout split (60 documents), extraction code a65ac89e52

| Metric | oracle | pdf-text | rapidocr | tesseract |
|---|---|---|---|---|
| invoice_no correct | 66.7% | 66.7% | 60.0% | 66.7% |
| date correct | 66.7% | 66.7% | 66.7% | 66.7% |
| vendor correct | 100.0% | 100.0% | 100.0% | 100.0% |
| currency correct | 100.0% | 100.0% | 100.0% | 100.0% |
| subtotal correct | 100.0% | 100.0% | 100.0% | 98.3% |
| discount correct | 95.0% | 95.0% | 95.0% | 95.0% |
| rounding correct | 90.0% | 90.0% | 90.0% | 90.0% |
| total correct | 100.0% | 100.0% | 100.0% | 98.3% |
| tax_total correct | 95.0% | 95.0% | 95.0% | 93.3% |
| items correct | 100.0% | 100.0% | 95.0% | 50.0% |
| all fields correct (normalized text) | 56.7% | 56.7% | 53.3% | 28.3% |
| all fields exact text (case and spaces as written) | 56.7% | 56.7% | 8.3% | 28.3% |
| item F1 | 1.000 | 1.000 | 0.989 | 0.811 |
| flagged for review | 26 | 26 | 28 | 47 |
| wrong documents | 26 | 26 | 28 | 43 |
| wrong and NOT flagged | 0 | 0 | 0 | 1 |
| flag precision | 1.0 | 1.0 | 1.0 | 0.894 |
| flag recall | 1.0 | 1.0 | 1.0 | 0.977 |
| seconds per document (this machine) | 0.0 | 0.02 | 2.05 | 1.34 |
| all fields correct, template P1 | 70.0% | 70.0% | 60.0% | 40.0% |
| all fields correct, template P2 | 100.0% | 100.0% | 100.0% | 45.0% |
| all fields correct, template P3 | 0.0% | 0.0% | 0.0% | 0.0% |
