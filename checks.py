"""Deterministic BOQ checks. Every finding carries evidence (sheet, Excel row, numbers)."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict

import pandas as pd

from .parser import ORDINAL_LABEL, Parsed, SheetMeta, norm

SEVERITY_RANK = {"High": 0, "Medium": 1, "Low": 2}

CHECK_CALC = "Calculation"
CHECK_TOTAL = "Grand total"
CHECK_BUILDUP = "Rate build-up"
CHECK_MISSING = "Missing item"
CHECK_UNIT = "Unit vs description"
CHECK_NUMBERING = "Numbering"
CHECK_DUP = "Duplicate"
CHECK_OUTLIER = "Price outlier"
CHECK_STRAY = "Stray value"
ALL_CHECKS = [CHECK_CALC, CHECK_TOTAL, CHECK_BUILDUP, CHECK_MISSING, CHECK_UNIT,
              CHECK_NUMBERING, CHECK_DUP, CHECK_OUTLIER, CHECK_STRAY]


@dataclass
class Finding:
    sheet: str
    row: int | None
    item: str
    check: str
    severity: str
    message: str
    evidence: str = ""
    amount: float | None = None
    id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------- helpers
UNIT_CLASSES = {
    "area": {"م2", "م٢", "m2", "sqm", "م²", "m²"},
    "volume": {"م3", "م٣", "m3", "م³", "m³"},
    "length": {"مط", "m", "lm", "rm", "ml"},
    "count": {"عدد", "no", "nos", "unit", "units", "each", "ea", "set"},
    "lump": {"مقطوعيه", "lsum", "ls", "lumpsum"},
}
UNIT_PHRASES = [
    (r"بالمتر المسطح|بالمتر المربع|متر مسطح", "area"),
    (r"بالمتر المكعب", "volume"),
    (r"بالمتر الطولي|بالمتر الطول", "length"),
    (r"بالعدد", "count"),
    (r"بالمقطوعيه|مقطوعيه", "lump"),
]
CLASS_LABEL = {"area": "m² (area)", "volume": "m³ (volume)", "length": "linear m",
               "count": "count / each", "lump": "lump sum"}


def unit_class(unit) -> str | None:
    if not unit:
        return None
    u = re.sub(r"[.\s]", "", norm(unit))
    for cls, words in UNIT_CLASSES.items():
        if u in {re.sub(r"[.\s]", "", w) for w in words}:
            return cls
    return None


def expected_class(description: str) -> str | None:
    tn = norm(description)
    best = None
    for rx, cls in UNIT_PHRASES:
        m = re.search(rx, tn)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), cls)
    return best[1] if best else None


def _item(r) -> str:
    return "" if pd.isna(r["item_no"]) else str(int(r["item_no"]))


def _short(s: str, n: int = 90) -> str:
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= n else s[:n] + "…"


def _f(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".") if abs(x) < 1e15 else str(x)


# ----------------------------------------------------------------------------- checks
def check_calculations(lines: pd.DataFrame, tol: float) -> list[Finding]:
    out: list[Finding] = []
    d = lines[lines["diff"].notna() & (lines["diff"].abs() > tol)]
    groups: dict[tuple, list] = defaultdict(list)
    for _, r in d.iterrows():
        groups[(r["sheet"], round(float(r["diff"]), 2))].append(r)
    for (sheet, diff), rows in groups.items():
        if len(rows) >= 3:
            items = ", ".join(_item(r) or f"row {r['row']}" for r in rows)
            out.append(Finding(
                sheet, int(rows[0]["row"]), f"{len(rows)} items", CHECK_CALC, "High",
                f"{len(rows)} line totals exceed quantity × rate by exactly {_f(diff)} each "
                f"(combined {_f(diff * len(rows))}).",
                f"Items: {items}. Same unexplained difference on every line suggests a hard-coded "
                f"add-on or a copied formula error.", diff * len(rows)))
        else:
            for r in rows:
                rel = abs(r["diff"]) / max(abs(r["total_calc"]), 1)
                out.append(Finding(
                    sheet, int(r["row"]), _item(r), CHECK_CALC,
                    "High" if abs(r["diff"]) >= 100 or rel > 0.01 else "Medium",
                    f"Stated total {_f(r['total'])} ≠ quantity {_f(r['qty'])} × rate {_f(r['rate'])} "
                    f"= {_f(r['total_calc'])} (difference {_f(r['diff'])}).",
                    _short(r["label"]), float(r["diff"])))
    # total present but cannot be validated
    for _, r in lines[lines["total"].notna() & lines["rate"].isna() & lines["qty"].notna()].iterrows():
        out.append(Finding(r["sheet"], int(r["row"]), _item(r), CHECK_CALC, "Medium",
                           f"Total {_f(r['total'])} is given but the rate is missing, so it cannot be validated.",
                           _short(r["label"]), None))
    return out


def check_grand_totals(parsed: Parsed, tol: float) -> list[Finding]:
    out = []
    for m in parsed.metas:
        if m.stated_total is None:
            if m.layout is not None:
                out.append(Finding(m.name, None, "", CHECK_TOTAL, "Medium",
                                   "No grand-total row found on this sheet.", ""))
            continue
        s = parsed.lines.loc[parsed.lines["sheet"] == m.name, "total"].sum()
        if abs(s - m.stated_total) > tol:
            out.append(Finding(m.name, m.stated_row, "", CHECK_TOTAL, "High",
                               f"Stated grand total {_f(m.stated_total)} ≠ sum of line totals {_f(s)}.",
                               f"Difference {_f(m.stated_total - s)}.", m.stated_total - s))
    return out


def check_buildup(parsed: Parsed) -> list[Finding]:
    out = []
    ln = parsed.lines
    ln = ln[ln["base"].notna() & ln["markup"].notna() & ln["rate"].notna()]
    for sheet, g in ln.groupby("sheet", sort=False):
        pct = (g["markup"] / g["base"]).round(3)
        if pct.empty:
            continue
        mode = Counter(pct).most_common(1)[0][0]
        for idx, r in g.iterrows():
            gap = abs(r["base"] + r["markup"] - r["rate"])
            if gap > max(0.01, 0.0005 * abs(r["rate"])):
                out.append(Finding(sheet, int(r["row"]), _item(r), CHECK_BUILDUP, "Medium",
                                   f"Rate {_f(r['rate'])} ≠ base {_f(r['base'])} + markup {_f(r['markup'])}.",
                                   _short(r["label"]), gap))
            elif abs(pct[idx] - mode) > 0.005:
                out.append(Finding(sheet, int(r["row"]), _item(r), CHECK_BUILDUP, "Medium",
                                   f"Markup is {pct[idx]:.1%} here but {mode:.1%} on most lines of this sheet.",
                                   _short(r["label"]), None))
    return out


def check_missing(parsed: Parsed) -> list[Finding]:
    out = []
    for m in parsed.metas:
        for u in m.unpriced:
            out.append(Finding(
                m.name, u["row"], "", CHECK_MISSING, "Medium",
                f"“{u['name']}” has a quantity ({_f(u['qty']) if u['qty'] is not None else '?'} {u['unit']}) "
                f"but no rate or total, so it adds nothing to the sheet total.", "Unpriced item"))
        for n in m.numbered_empty:
            out.append(Finding(m.name, n["row"], str(n["item_no"]), CHECK_MISSING, "Medium",
                               "Numbered item has no quantity, rate or total.", _short(n["text"])))
    ln = parsed.lines
    for _, r in ln[ln["unnumbered"]].iterrows():
        out.append(Finding(r["sheet"], int(r["row"]), "", CHECK_MISSING, "Medium",
                           f"A priced line (qty {_f(r['qty'])}, total {_f(r['total'])}) has no item number; "
                           f"its description is not attached to any numbered item.",
                           _short(r["label"]), float(r["total"]) if pd.notna(r["total"]) else None))
    for _, r in ln[ln["unit"].isna() | (ln["unit"] == "")].iterrows():
        out.append(Finding(r["sheet"], int(r["row"]), _item(r), CHECK_MISSING, "Medium",
                           "Priced line has no unit of measure.", _short(r["label"]),
                           float(r["total"]) if pd.notna(r["total"]) else None))
    for _, r in ln[ln["qty"].isna() & (ln["total"].notna() | ln["rate"].notna())].iterrows():
        out.append(Finding(r["sheet"], int(r["row"]), _item(r), CHECK_MISSING, "Medium",
                           "Line has a price but no quantity.", _short(r["label"])))
    for _, r in ln[(ln["qty"].fillna(1) <= 0) | (ln["rate"].fillna(1) <= 0)].iterrows():
        out.append(Finding(r["sheet"], int(r["row"]), _item(r), CHECK_MISSING, "Medium",
                           "Zero or negative quantity / rate.", _short(r["label"])))
    return out


def check_units(parsed: Parsed) -> list[Finding]:
    out = []
    for _, r in parsed.lines.iterrows():
        exp = expected_class(r["description"])
        act = unit_class(r["unit"])
        if exp and act and exp != act:
            out.append(Finding(
                r["sheet"], int(r["row"]), _item(r), CHECK_UNIT, "Medium",
                f"Description measures this item as {CLASS_LABEL[exp]} but the unit column says "
                f"“{r['unit']}” ({CLASS_LABEL[act]}).",
                _short(r["description"], 110),
                float(r["total"]) if pd.notna(r["total"]) else None))
    return out


def check_numbering(parsed: Parsed) -> list[Finding]:
    out = []
    for m in parsed.metas:
        nums = sorted(n for n, _ in m.item_numbers)
        for n, c in Counter(nums).items():
            if c > 1:
                out.append(Finding(m.name, None, str(n), CHECK_NUMBERING, "Medium",
                                   f"Item number {n} is used {c} times.", ""))
        if nums:
            missing = [k for k in range(nums[0], nums[-1] + 1) if k not in set(nums)]
            if missing:
                out.append(Finding(m.name, None, "", CHECK_NUMBERING, "Medium",
                                   f"Item numbers missing from the sequence: {', '.join(map(str, missing))}.", ""))
        for n in m.notes:
            out.append(Finding(m.name, n["row"], str(n["item_no"]), CHECK_NUMBERING, "Low",
                               "A note is numbered like a work item and takes a number in the sequence.",
                               _short(n["text"])))
        secs = m.sections
        if len(secs) >= 2:
            seen = [s[0] for s in secs]
            if seen[0] != 1:
                out.append(Finding(m.name, secs[0][1], "", CHECK_NUMBERING, "Low",
                                   f"Section numbering starts at “{ORDINAL_LABEL[seen[0]]}” – no first section heading.", ""))
            for a, b in zip(secs, secs[1:]):
                for k in range(a[0] + 1, b[0]):
                    out.append(Finding(m.name, b[1], "", CHECK_NUMBERING, "Medium",
                                       f"Section “{ORDINAL_LABEL[k]}” is missing between "
                                       f"“{ORDINAL_LABEL[a[0]]}” (row {a[1]}) and “{ORDINAL_LABEL[b[0]]}” (row {b[1]}).", ""))
    return out


def check_duplicates(parsed: Parsed) -> list[Finding]:
    out = []
    ln = parsed.lines
    seen: dict[tuple, list] = defaultdict(list)
    for _, r in ln.iterrows():
        if len(str(r["description"])) < 40 or pd.isna(r["rate"]):
            continue
        seen[(r["sheet"], norm(r["description"])[:160], round(float(r["rate"]), 2))].append(r)
    for (sheet, _, rate), rows in seen.items():
        if len(rows) >= 2:
            items = ", ".join(_item(r) or f"row {r['row']}" for r in rows)
            out.append(Finding(sheet, int(rows[0]["row"]), items, CHECK_DUP, "Low",
                               f"Items {items} share the same description and rate ({_f(rate)}). "
                               f"Confirm they cover different scope (e.g. another floor or zone).",
                               _short(rows[0]["description"], 110)))
    return out


def check_outliers(parsed: Parsed, z_cut: float = 3.5, min_group: int = 6) -> list[Finding]:
    """Robust (median/MAD on log-rate) outliers among linear-metre *size-series* lines
    (pipes, cables: the text mentions a diameter) on the same sheet.
    Deliberately conservative: mixed-material groups (e.g. stone skirting next to cladding)
    produce false alarms, so only lines that look like one family are compared."""
    out = []
    ln = parsed.lines[parsed.lines["rate"].notna()].copy()
    ln["cls"] = ln["unit"].map(unit_class)
    series = ln["description"].map(norm).str.contains(r"قطر|اقطار|diameter|ø", regex=True) | \
             ln["label"].map(norm).str.contains(r"قطر|اقطار|diameter|ø", regex=True)
    ln = ln[series]
    for (sheet, cls), g in ln.groupby(["sheet", "cls"], sort=False):
        if cls != "length" or len(g) < min_group:
            continue
        logs = g["rate"].map(lambda v: math.log10(v) if v > 0 else float("nan")).dropna()
        med = logs.median()
        mad = (logs - med).abs().median()
        if not mad:
            continue
        for idx, v in logs.items():
            z = 0.6745 * (v - med) / mad
            if abs(z) > z_cut:
                r = g.loc[idx]
                typical = 10 ** med
                out.append(Finding(
                    sheet, int(r["row"]), _item(r), CHECK_OUTLIER, "Medium",
                    f"Rate {_f(r['rate'])} per {r['unit']} is {typical / r['rate']:.1f}× lower than the typical "
                    f"{_f(typical)} for linear-metre lines on this sheet (robust z = {z:.1f})."
                    if r["rate"] < typical else
                    f"Rate {_f(r['rate'])} per {r['unit']} is {r['rate'] / typical:.1f}× higher than the typical "
                    f"{_f(typical)} for linear-metre lines on this sheet (robust z = {z:.1f}).",
                    _short(r["label"]), float(r["total"]) if pd.notna(r["total"]) else None))
    return out


def check_stray(parsed: Parsed) -> list[Finding]:
    out = []
    for m in parsed.metas:
        for s in m.stray:
            out.append(Finding(m.name, s["row"], "", CHECK_STRAY, "Low",
                               f"Value {_f(s['value'])} sits in an otherwise unused rate column.",
                               "May be a leftover from an earlier rate."))
    return out


# ----------------------------------------------------------------------------- runner
def run_checks(parsed: Parsed, tol: float = 0.5, z_cut: float = 3.5) -> list[Finding]:
    if parsed.lines.empty:
        return []
    found: list[Finding] = []
    found += check_calculations(parsed.lines, tol)
    found += check_grand_totals(parsed, tol)
    found += check_buildup(parsed)
    found += check_missing(parsed)
    found += check_units(parsed)
    found += check_numbering(parsed)
    found += check_duplicates(parsed)
    found += check_outliers(parsed, z_cut)
    found += check_stray(parsed)
    sheet_order = {m.name: i for i, m in enumerate(parsed.metas)}
    found.sort(key=lambda f: (SEVERITY_RANK[f.severity], sheet_order.get(f.sheet, 99), f.row or 0))
    for i, f in enumerate(found, 1):
        f.id = f"F-{i:03d}"
    return found


def findings_frame(findings: list[Finding]) -> pd.DataFrame:
    cols = ["id", "severity", "check", "sheet", "row", "item", "message", "evidence", "amount"]
    return pd.DataFrame([f.to_dict() for f in findings], columns=cols)


def reconciliation(parsed: Parsed) -> pd.DataFrame:
    rows = []
    for m in parsed.metas:
        g = parsed.lines[parsed.lines["sheet"] == m.name]
        if g.empty:
            continue
        stated = m.stated_total
        sum_tot = g["total"].sum()
        recomputed = g.apply(lambda r: r["qty"] * r["rate"] if pd.notna(r["qty"]) and pd.notna(r["rate"]) else (r["total"] if pd.notna(r["total"]) else 0), axis=1).sum()
        rows.append({"Sheet": m.name, "Lines": len(g), "Stated total": stated,
                     "Sum of line totals": sum_tot, "Recomputed (qty × rate)": recomputed,
                     "Stated − recomputed": (stated - recomputed) if stated is not None else None})
    return pd.DataFrame(rows)
