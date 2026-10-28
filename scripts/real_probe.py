"""Run the extractor on public sample invoices from the invoice2data project (MIT licence) and
compare with that project's own reference values. The files are downloaded, not stored here.
Usage: python scripts/real_probe.py   (needs internet; writes results/real_public_probe.md)"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from invoice_ai.api import run_extraction  # noqa: E402
from invoice_ai.evaluate import code_version  # noqa: E402

BASE = "https://raw.githubusercontent.com/invoice-x/invoice2data/master/tests/compare/"
NAMES = ["AmazonWebServices", "AzureInterior", "FlipkartInvoice", "NetpresseInvoice", "QualityHosting",
         "coolblue1", "coolblue2", "free_fiber", "oyo", "saeco"]


def get(name: str) -> bytes:
    with urllib.request.urlopen(BASE + name, timeout=30) as response:
        return response.read()


rows, ok_total, ok_all, flagged = [], 0, 0, 0
for name in NAMES:
    ref = json.loads(get(name + ".json"))[0]
    result = run_extraction(get(name + ".pdf"), name + ".pdf", "pdf-text")
    inv = result.invoice
    total_ok = inv.total is not None and abs(float(inv.total) - float(ref["amount"])) < 0.01
    no_ok = (inv.invoice_no or "").lstrip("#") == str(ref["invoice_number"]).lstrip("#")
    date_ok = str(inv.date) == ref["date"]
    all_ok = total_ok and no_ok and date_ok
    ok_total += total_ok
    ok_all += all_ok
    flagged += result.needs_review
    rows.append(f"| {name} | {'yes' if result.needs_review else 'no'} | {'ok' if total_ok else 'wrong'} | "
                f"{'ok' if no_ok else 'wrong'} | {'ok' if date_ok else 'wrong'} | {', '.join(i.code for i in result.issues[:4])} |")
text = (f"## Public sample invoices (invoice2data test files, MIT), extraction code {code_version()}\n\n"
        f"PDF text layer only. Reference values come from that project, not from this repository. Compared fields: total, invoice number, date.\n\n"
        f"- Documents: {len(NAMES)}; total correct: {ok_total}; total, number and date all correct: {ok_all}; flagged for review: {flagged}\n\n"
        "| Document | Flagged | Total | Invoice no | Date | First issues |\n|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n")
Path("results/real_public_probe.md").write_text(text)
print(text)
