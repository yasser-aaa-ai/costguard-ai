"""Regression tests on the anonymized sample. Each expectation was verified by hand against the file."""
from pathlib import Path

import pytest

from core import ai, checks, parser, submittals

SAMPLE = Path(__file__).parents[1] / "data" / "sample_boq_anonymized.xlsx"


@pytest.fixture(scope="module")
def parsed():
    return parser.parse_workbook(parser.read_workbook(SAMPLE.read_bytes(), SAMPLE.name))


@pytest.fixture(scope="module")
def found(parsed):
    return checks.run_checks(parsed)


def test_line_counts_and_totals_reconcile(parsed):
    counts = parsed.lines.groupby("sheet", sort=False).size().tolist()
    assert counts == [32, 22, 30, 29]
    for m in parsed.metas:
        s = parsed.lines.loc[parsed.lines["sheet"] == m.name, "total"].sum()
        assert s == pytest.approx(m.stated_total, abs=0.01)


def test_detached_rows_relinked(parsed):
    assert parsed.realigned_count == 12


def test_hardcoded_1000_on_29_em_lines(found):
    f = [x for x in found if x.check == checks.CHECK_CALC]
    assert len(f) == 1 and f[0].amount == pytest.approx(29000) and "29 line totals" in f[0].message


def test_unit_mismatch_item_15(found):
    f = [x for x in found if x.check == checks.CHECK_UNIT]
    assert len(f) == 1 and f[0].item == "15" and f[0].row == 48


def test_missing_sections_in_architectural(found):
    msgs = " ".join(x.message for x in found if x.check == checks.CHECK_NUMBERING)
    assert "سابعًا" in msgs and "عاشرًا" in msgs


def test_unpriced_items_in_finishing(found):
    f = [x for x in found if x.check == checks.CHECK_MISSING and "no rate or total" in x.message]
    assert len(f) == 6


def test_unnumbered_priced_line_and_missing_unit(found):
    msgs = [x.message for x in found if x.check == checks.CHECK_MISSING]
    assert any("has no item number" in m for m in msgs)
    assert any("no unit of measure" in m for m in msgs)


def test_single_price_outlier(found):
    f = [x for x in found if x.check == checks.CHECK_OUTLIER]
    assert len(f) == 1 and f[0].sheet == "الاعمال الصحية" and f[0].row == 28


def test_fallback_covers_every_finding(found):
    payload = [x.to_dict() for x in found]
    items, summary = ai.fallback_enrich(payload, dict(total=len(found), sheets=4, high=1, medium=12, low=5, top=""))
    assert set(items) == {x.id for x in found} and summary


def test_submittal_register(parsed):
    reg = submittals.extract(parsed.lines)
    assert len(reg) == 20
    assert "بيلدمور" in " ".join(reg["Brand / Manufacturer"])
    assert reg["Submittal No"].is_unique
