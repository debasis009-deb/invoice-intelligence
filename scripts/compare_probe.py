"""Compare the geometry and model extractors on public sample invoices (invoice2data test files, MIT).
Each PDF is read twice: from its text layer, and as a rendered scan read by RapidOCR.
Reference values are that project's own JSON files. Resumable: model answers are cached in results/probe_cache.
Usage: INVOICE_MODEL_PATH=model.gguf python scripts/compare_probe.py [seconds_budget]"""
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from invoice_ai.api import run_extraction  # noqa: E402
from invoice_ai.evaluate import code_version  # noqa: E402
from invoice_ai.ocr import load_pages  # noqa: E402

BASE = "https://raw.githubusercontent.com/invoice-x/invoice2data/master/tests/compare/"
NAMES = ["AmazonWebServices", "AzureInterior", "FlipkartInvoice", "NetpresseInvoice", "QualityHosting",
         "coolblue1", "coolblue2", "free_fiber", "oyo", "saeco"]
CACHE = Path("results/probe_cache")
budget = float(sys.argv[1]) if len(sys.argv) > 1 else 1e9
start = time.time()


def get(name: str) -> bytes:
    path = CACHE / name
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(BASE + name, timeout=30) as r:
            path.write_bytes(r.read())
    return path.read_bytes()


def run(name: str, mode: str, extractor: str):
    """mode: text (PDF text layer) or scan (page 1 rendered to PNG, RapidOCR)."""
    key = CACHE / f"{name}.{mode}.{extractor}.json"
    if key.exists():
        return json.loads(key.read_text())
    if time.time() - start > budget:
        return None
    pdf = get(name + ".pdf")
    if mode == "text":
        result = run_extraction(pdf, name + ".pdf", "pdf-text", extractor)
    else:
        buf = io.BytesIO()
        load_pages(pdf, name + ".pdf")[0].convert("RGB").save(buf, "PNG")
        result = run_extraction(buf.getvalue(), name + ".png", "rapidocr", extractor)
    out = result.model_dump(mode="json")
    key.write_text(json.dumps(out))
    return out


def score(out: dict, ref: dict) -> dict:
    inv = out["invoice"]
    got_no = inv["invoice_no"] or ""
    return {
        "total": inv["total"] is not None and abs(float(inv["total"]) - float(ref["amount"])) < 0.01,
        "no_strict": got_no == str(ref["invoice_number"]),
        "no_prefix": got_no.lstrip("#") == str(ref["invoice_number"]).lstrip("#") and got_no != "",
        "date": inv["date"] == ref["date"],
        "flagged": out["needs_review"],
    }


table, summary, pending = [], {}, 0
for name in NAMES:
    ref = json.loads(get(name + ".json"))[0]
    for mode in ("text", "scan"):
        for extractor in ("geometry", "model"):
            out = run(name, mode, extractor)
            if out is None:
                pending += 1
                continue
            s = score(out, ref)
            summary.setdefault((mode, extractor), []).append(s)
            table.append(f"| {name} | {mode} | {extractor} | {s['total']} | {s['no_strict']} | {s['no_prefix']} | {s['date']} | "
                         f"{s['flagged']} | {', '.join(i['code'] for i in out['issues'][:3])} |")
lines = [f"## Geometry vs model on public sample invoices, extraction code {code_version()}", "",
         "Development and regression evidence on 10 documents, not a benchmark. Reference values are invoice2data's own JSON; "
         "a few references may name a different number than the invoice shows. 'no strict' compares the invoice number exactly, "
         "'no prefix' ignores a leading '#'. Dates are exact ISO. Each cell is correct / 10.", ""]
if pending:
    lines.append(f"INCOMPLETE: {pending} runs not done yet (time budget).\n")
lines += ["| Mode | Extractor | Total | No strict | No prefix | Date | Flagged | n |", "|---|---|---|---|---|---|---|---|"]
for (mode, extractor), rows in sorted(summary.items()):
    c = lambda k: sum(r[k] for r in rows)  # noqa: E731
    lines.append(f"| {mode} | {extractor} | {c('total')} | {c('no_strict')} | {c('no_prefix')} | {c('date')} | {c('flagged')} | {len(rows)} |")
lines += ["", "| Document | Mode | Extractor | Total | No strict | No prefix | Date | Flagged | First issues |", "|---|---|---|---|---|---|---|---|---|"] + table
Path("results/compare_probe.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines[:14]))
