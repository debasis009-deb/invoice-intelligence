"""Measure extraction accuracy and review-flag quality on a generated split.

Engines: rapidocr and tesseract read the scanned JPEG, pdf-text reads the
digital PDF, and `oracle` skips OCR and feeds the drawn text straight to the
extractor, so extractor mistakes can be told apart from OCR mistakes.
"""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter

from . import rules
from .extract import extract as extract_geometry
from .ocr import Word, group_words, read_words
from .schema import Invoice

SCALAR_FIELDS = ("invoice_no", "date", "vendor", "currency", "subtotal", "discount", "rounding", "total")
FIELDS = SCALAR_FIELDS + ("tax_total", "items")


def norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").casefold())


def item_keys(inv: Invoice, exact: bool = False) -> Counter:
    return Counter((i.description if exact else norm(i.description), i.qty, i.unit_price, i.amount)
                   for i in inv.items)


def compare(truth: Invoice, got: Invoice, exact: bool = False) -> dict[str, bool]:
    """Field-by-field match. Normalized (default) ignores case, spaces and punctuation in
    text fields (vendor, invoice number, item descriptions); exact compares them as written."""
    same = (lambda a, b: a == b) if exact else (lambda a, b: norm(a) == norm(b))
    ok = {f: same(getattr(truth, f), getattr(got, f)) if f in ("invoice_no", "vendor")
          else getattr(truth, f) == getattr(got, f) for f in SCALAR_FIELDS}
    ok["tax_total"] = truth.tax_total == got.tax_total
    ok["items"] = item_keys(truth, exact) == item_keys(got, exact)
    return ok


def run_engine(doc: dict, folder: Path, engine: str, cache: Path) -> tuple[list[Word], float]:
    """OCR boxes and seconds for one document; OCR output is cached so a long run can resume."""
    slot = cache / engine / f"{folder.name}_{doc['id']}.json"
    if slot.exists() and engine not in ("oracle", "pdf-text"):
        saved = json.loads(slot.read_text())
        return [Word(**w) for w in saved["words"]], saved["seconds"]
    start = perf_counter()
    if engine == "oracle":
        words = [Word(**w) for w in doc["oracle_words"]]
    else:
        name = doc["pdf"] if engine == "pdf-text" else doc["image"]
        words, _ = read_words((folder / name).read_bytes(), name, engine)
    seconds = perf_counter() - start
    if engine not in ("oracle", "pdf-text"):
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_text(json.dumps({"words": [vars(w) for w in words], "seconds": seconds}))
    return words, seconds


def evaluate(folder: Path, engines: list[str], limit: int | None, cache: Path, extractor: str) -> dict:
    docs = json.loads((folder / "manifest.json").read_text())[:limit]
    report, errors = {}, []
    for engine in engines:
        field_ok, by_template = Counter(), defaultdict(list)
        exact_ok = 0
        item_tp = item_pred = item_true = flagged_bad = flagged = bad = accepted_bad = 0
        seconds = 0.0
        for doc in docs:
            truth = Invoice.model_validate(doc["truth"])
            words, took = run_engine(doc, folder, engine, cache)
            seconds += took
            result = (extract_geometry(words, engine) if extractor == "geometry"
                      else rules.extract(group_words(words), engine))
            ok = compare(truth, result.invoice)
            exact_ok += all(compare(truth, result.invoice, exact=True).values())
            for f, good in ok.items():
                field_ok[f] += good
                if not good:
                    errors.append({"doc": doc["id"], "engine": engine, "field": f,
                                   "flagged": result.needs_review,
                                   "truth": str(getattr(truth, f, None) if f != "tax_total" else truth.tax_total),
                                   "got": str(getattr(result.invoice, f, None) if f != "tax_total" else result.invoice.tax_total)})
            wrong = not all(ok.values())
            by_template[doc["template"]].append(not wrong)
            bad += wrong
            flagged += result.needs_review
            flagged_bad += wrong and result.needs_review
            accepted_bad += wrong and not result.needs_review
            hit = item_keys(truth) & item_keys(result.invoice)
            item_tp += sum(hit.values())
            item_pred += len(result.invoice.items)
            item_true += len(truth.items)
        n = len(docs)
        precision = item_tp / item_pred if item_pred else 0.0
        recall = item_tp / item_true if item_true else 0.0
        report[engine] = {
            "documents": n,
            "field_accuracy": {f: round(field_ok[f] / n, 3) for f in FIELDS},
            "all_fields_correct": round((n - bad) / n, 3),
            "all_fields_exact_text": round(exact_ok / n, 3),
            "item_f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
            "all_fields_correct_by_template": {t: round(sum(v) / len(v), 3) for t, v in sorted(by_template.items())},
            "review": {"flagged": flagged, "wrong_documents": bad,
                       "flag_precision": round(flagged_bad / flagged, 3) if flagged else None,
                       "flag_recall": round(flagged_bad / bad, 3) if bad else None,
                       "wrong_but_not_flagged": accepted_bad},
            "seconds_per_document": round(seconds / n, 2),
        }
    return {"report": report, "errors": errors}


def code_version() -> str:
    """Short hash of the extraction, model and upload-checking code, so each result file names the code that produced it."""
    here = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ("schema.py", "validate.py", "ocr.py", "rules.py", "extract.py", "llm.py", "api.py"):
        digest.update((here / name).read_bytes())
    return digest.hexdigest()[:10]


def markdown(split: str, report: dict) -> str:
    engines = list(report)
    rows = ["| Metric | " + " | ".join(engines) + " |", "|---|" + "---|" * len(engines)]
    n = report[engines[0]]["documents"]
    for f in FIELDS:
        rows.append(f"| {f} correct | " + " | ".join(f"{report[e]['field_accuracy'][f]:.1%}" for e in engines) + " |")
    rows.append("| all fields correct (normalized text) | " + " | ".join(f"{report[e]['all_fields_correct']:.1%}" for e in engines) + " |")
    rows.append("| all fields exact text (case and spaces as written) | " + " | ".join(f"{report[e]['all_fields_exact_text']:.1%}" for e in engines) + " |")
    rows.append("| item F1 | " + " | ".join(f"{report[e]['item_f1']:.3f}" for e in engines) + " |")
    rows.append("| flagged for review | " + " | ".join(str(report[e]["review"]["flagged"]) for e in engines) + " |")
    rows.append("| wrong documents | " + " | ".join(str(report[e]["review"]["wrong_documents"]) for e in engines) + " |")
    rows.append("| wrong and NOT flagged | " + " | ".join(str(report[e]["review"]["wrong_but_not_flagged"]) for e in engines) + " |")
    rows.append("| flag precision | " + " | ".join(str(report[e]["review"]["flag_precision"]) for e in engines) + " |")
    rows.append("| flag recall | " + " | ".join(str(report[e]["review"]["flag_recall"]) for e in engines) + " |")
    rows.append("| seconds per document (this machine) | " + " | ".join(str(report[e]["seconds_per_document"]) for e in engines) + " |")
    templates = sorted({t for e in engines for t in report[e]["all_fields_correct_by_template"]})
    for t in templates:
        rows.append(f"| all fields correct, template {t} | " + " | ".join(
            f"{report[e]['all_fields_correct_by_template'].get(t, 0):.1%}" for e in engines) + " |")
    return f"## {split} split ({n} documents), extraction code {code_version()}\n\n" + "\n".join(rows) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--split", choices=("dev", "test", "final", "holdout"), default="test")
    ap.add_argument("--engines", nargs="+", default=["oracle", "pdf-text", "rapidocr", "tesseract"])
    ap.add_argument("--extractor", choices=("geometry", "rules"), default="geometry")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()
    result = evaluate(args.data / args.split, args.engines, args.limit, args.out / "ocr_cache", args.extractor)
    args.out.mkdir(exist_ok=True)
    (args.out / f"{args.split}_{args.extractor}.json").write_text(json.dumps(
        {"extraction_code": code_version(), **result["report"]}, indent=1))
    (args.out / f"{args.split}_{args.extractor}.md").write_text(markdown(args.split, result["report"]))
    with (args.out / f"{args.split}_{args.extractor}_errors.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["doc", "engine", "field", "flagged", "truth", "got"])
        w.writeheader()
        w.writerows(result["errors"])
    print((args.out / f"{args.split}_{args.extractor}.md").read_text())


if __name__ == "__main__":
    main()
