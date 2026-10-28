"""Evaluate the optional model extractor against the geometry extractor on a generated split (RapidOCR scans).

Model answers are cached per document so a long run can be resumed:
    INVOICE_MODEL_PATH=model.gguf python scripts/model_eval.py --split test --budget 90
Run it again until it prints the final report. Header fields only: the model does not read items or tax.
"""
import argparse
import json
import time
from pathlib import Path

from invoice_ai import llm
from invoice_ai.evaluate import compare, run_engine
from invoice_ai.extract import extract as extract_geometry
from invoice_ai.ocr import group_words
from invoice_ai.schema import Invoice

HEADER = ("invoice_no", "date", "vendor", "currency", "total")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--budget", type=float, default=90, help="stop after this many seconds of model work")
    args = ap.parse_args()
    folder = args.data / args.split
    docs = json.loads((folder / "manifest.json").read_text())
    cache = args.out / "model_cache" / args.split
    cache.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    for doc in docs:
        slot = cache / f"{doc['id']}.json"
        if slot.exists():
            continue
        if time.perf_counter() - start > args.budget:
            print(f"{len(list(cache.glob('*.json')))}/{len(docs)} done, run again")
            return
        words, _ = run_engine(doc, folder, "rapidocr", args.out / "ocr_cache")
        text = "\n".join(line.text for line in group_words(words))
        t0 = time.perf_counter()
        answer = llm.ask(text)
        slot.write_text(json.dumps({"answer": answer, "text": text, "seconds": time.perf_counter() - t0}))
    report(docs, folder, cache, args)


def report(docs, folder, cache, args) -> None:
    n = len(docs)
    ok = {"geometry": dict.fromkeys(HEADER, 0), "model": dict.fromkeys(HEADER, 0)}
    all_g = all_m = bad_json = 0
    agree = agree_right = agree_wrong = 0
    secs = 0.0
    rows = []
    for doc in docs:
        saved = json.loads((cache / f"{doc['id']}.json").read_text())
        words, _ = run_engine(doc, folder, "rapidocr", args.out / "ocr_cache")
        truth = Invoice.model_validate(doc["truth"])
        geo = extract_geometry(words, "rapidocr")
        mod = llm.build(saved["answer"], saved["text"], "rapidocr")
        secs += saved["seconds"]
        bad_json += any(i.code == "model_bad_output" for i in mod.issues)
        cg, cm = compare(truth, geo.invoice), compare(truth, mod.invoice)
        for f in HEADER:
            ok["geometry"][f] += cg[f]
            ok["model"][f] += cm[f]
        all_g += all(cg[f] for f in HEADER)
        all_m += all(cm[f] for f in HEADER)
        same = all(getattr(geo.invoice, f) == getattr(mod.invoice, f) if f not in ("invoice_no", "vendor")
                   else str(getattr(geo.invoice, f) or "").replace(" ", "").lower() == str(getattr(mod.invoice, f) or "").replace(" ", "").lower()
                   for f in HEADER)
        if same:
            agree += 1
            full = all(cg[f] for f in HEADER)
            agree_right += full
            agree_wrong += not full
        rows.append({"doc": doc["id"], "template": doc["template"], "geometry_ok": cg, "model_ok": cm})
    out = {"split": args.split, "documents": n,
           "header_field_accuracy": {e: {f: round(v / n, 3) for f, v in d.items()} for e, d in ok.items()},
           "all_five_header_fields_correct": {"geometry": round(all_g / n, 3), "model": round(all_m / n, 3)},
           "model_bad_json": bad_json,
           "geometry_and_model_agree_on_all_five": {"documents": agree, "geometry_fully_correct": agree_right, "geometry_wrong": agree_wrong},
           "model_seconds_per_document": round(secs / n, 2)}
    (args.out / f"{args.split}_model.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
