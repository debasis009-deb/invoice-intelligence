import json
import random

from invoice_ai.generate import CATALOG, TEMPLATES, VENDORS, generate, random_invoice
from invoice_ai.validate import check


def test_generated_ground_truth_is_arithmetically_consistent():
    for tpl in TEMPLATES:
        for n in range(30):
            inv = random_invoice(random.Random(n), tpl)
            assert check(inv) == [], (tpl.name, n)


def test_final_stress_split_is_disjoint_from_dev_and_test():
    final = {t.name for t in TEMPLATES if t.split == "final"}
    assert final and final.isdisjoint(t.name for t in TEMPLATES if t.split != "final")
    for other in ("dev", "test"):
        assert set(VENDORS["final"]).isdisjoint(VENDORS[other])
        assert {c[0] for c in CATALOG["final"]}.isdisjoint(c[0] for c in CATALOG[other])


def test_dev_and_test_share_no_template_font_vendor_or_item():
    dev = [t for t in TEMPLATES if t.split == "dev"]
    test = [t for t in TEMPLATES if t.split == "test"]
    assert {t.name for t in dev}.isdisjoint(t.name for t in test)
    assert {t.font for t in dev}.isdisjoint(t.font for t in test)
    assert set(VENDORS["dev"]).isdisjoint(VENDORS["test"])
    assert {c[0] for c in CATALOG["dev"]}.isdisjoint(c[0] for c in CATALOG["test"])


def test_split_uses_only_its_own_vendors_and_items(tmp_path):
    generate(tmp_path, "test", 2, seed=1)
    docs = json.loads((tmp_path / "test" / "manifest.json").read_text())
    assert {d["template"] for d in docs} == {"H1", "H2", "H3"}
    items = {i["description"] for d in docs for i in d["truth"]["items"]}
    assert items <= {c[0] for c in CATALOG["test"]}
    assert {d["truth"]["vendor"] for d in docs} <= set(VENDORS["test"])


def test_generation_is_deterministic(tmp_path):
    generate(tmp_path / "a", "dev", 1, seed=7)
    generate(tmp_path / "b", "dev", 1, seed=7)
    read = lambda p: json.loads((p / "dev" / "manifest.json").read_text())  # noqa: E731
    assert read(tmp_path / "a") == read(tmp_path / "b")
