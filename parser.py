"""BOQ parser.

Real-world BOQ sheets often keep the numbers (qty / rate / total) on a different row
from the item number or description. This module re-links those detached rows into one
clean "line" per priced item and records everything that could not be linked, so the
checks in `checks.py` can report it instead of silently dropping it.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

# ----------------------------------------------------------------------------- text utils
_DIACRITICS = re.compile(r"[\u064B-\u0652]")


def norm(value) -> str:
    """Normalise Arabic/English text for matching (not for display)."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    s = str(value)
    for ch in ("\u0640", "\u200f", "\u200e", "\u202b", "\u202c"):
        s = s.replace(ch, "")
    s = _DIACRITICS.sub("", s)
    s = (s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
          .replace("ى", "ي").replace("ة", "ه"))
    return re.sub(r"\s+", " ", s).strip().lower()


def num(value):
    """Return a float for numeric cells (or numeric-looking strings), else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if re.fullmatch(r"-?\d+(\.\d+)?", s):
            return float(s)
    return None


def text(value) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return str(value).strip() if not isinstance(value, (int, float)) else ""


ORDINALS = ["اولا", "ثانيا", "ثالثا", "رابعا", "خامسا", "سادسا", "سابعا", "ثامنا",
            "تاسعا", "عاشرا", "حادي عشر", "ثاني عشر", "ثالث عشر"]
ORDINAL_LABEL = {i + 1: w for i, w in enumerate(
    ["أولًا", "ثانيًا", "ثالثًا", "رابعًا", "خامسًا", "سادسًا", "سابعًا", "ثامنًا",
     "تاسعًا", "عاشرًا", "حادي عشر", "ثاني عشر", "ثالث عشر"])}
# longest first so "ثاني عشر" is tested before "ثانيا"
_ORD_ORDER = sorted(range(len(ORDINALS)), key=lambda i: -len(ORDINALS[i]))


def section_ordinal(tn: str):
    for i in _ORD_ORDER:
        if tn.startswith(ORDINALS[i]) and (len(tn) == len(ORDINALS[i]) or not tn[len(ORDINALS[i])].isalpha()):
            return i + 1
    return None


UNIT_WORDS = {"unit", "units", "l.sum", "lsum", "ls", "no", "nos", "set", "عدد", "مقطوعيه"}
NOTE_WORDS = ("ملاحظات", "notes", "note:")
GRAND_PATTERNS = ("الاجمالي العام", "اجمالي اعمال", "grand total")


# ----------------------------------------------------------------------------- IO
def read_workbook(data: bytes, filename: str) -> dict[str, pd.DataFrame]:
    """Read every sheet as a raw grid (header=None). Supports xls / xlsx / csv."""
    name = filename.lower()
    if name.endswith(".csv"):
        sheets = {"csv": pd.read_csv(io.BytesIO(data), header=None)}
    elif name.endswith(".xls"):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, engine="xlrd")
    else:
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, engine="openpyxl")
    return {k: v.astype(object) for k, v in sheets.items()}


# ----------------------------------------------------------------------------- layout
@dataclass
class Layout:
    header_row: int
    no: int
    desc: int
    unit: int
    qty: int
    rates: list[int]
    total: int
    sub_qty: int | None = None


def find_layout(df: pd.DataFrame) -> Layout | None:
    for i in range(min(30, len(df))):
        cells = [norm(v) for v in df.iloc[i]]
        qty = next((j for j, c in enumerate(cells) if c in ("الكميه", "qty", "quantity")), None)
        unit = next((j for j, c in enumerate(cells) if c in ("الوحده", "unit")), None)
        if qty is None or unit is None:
            continue
        total = next((j for j, c in enumerate(cells) if "اجمالي" in c or c == "total"), None)
        rates = [j for j, c in enumerate(cells) if c in ("الفئه", "rate", "unit rate", "price")]
        if total is None or not rates:
            continue
        desc = next((j for j, c in enumerate(cells) if "بيان" in c), 1)
        sub = next((j for j, c in enumerate(cells) if c == "qty." and j > qty), None)
        return Layout(i, 0, desc, unit, qty, rates, total, sub)
    return None


# ----------------------------------------------------------------------------- results
@dataclass
class SheetMeta:
    name: str
    layout: Layout | None = None
    sections: list[tuple[int, int, str]] = field(default_factory=list)   # (ordinal, excel_row, text)
    item_numbers: list[tuple[int, int]] = field(default_factory=list)    # (item_no, excel_row)
    stated_total: float | None = None
    stated_row: int | None = None
    notes: list[dict] = field(default_factory=list)          # numbered rows that are notes
    numbered_empty: list[dict] = field(default_factory=list)  # numbered rows with no numbers
    unpriced: list[dict] = field(default_factory=list)        # qty but no rate / total
    stray: list[dict] = field(default_factory=list)           # values in unused rate columns
    skipped: str | None = None


@dataclass
class Parsed:
    lines: pd.DataFrame
    metas: list[SheetMeta]

    @property
    def realigned_count(self) -> int:
        return int(self.lines["realigned"].sum()) if len(self.lines) else 0


LINE_COLUMNS = ["sheet", "row", "item_no", "section", "label", "description", "unit", "qty",
                "base", "markup", "rate", "total", "total_calc", "diff", "realigned",
                "unnumbered", "linked_row"]


# ----------------------------------------------------------------------------- core
def _read_row(df: pd.DataFrame, i: int, L: Layout) -> dict:
    g = lambda c: df.iat[i, c] if c is not None and c < df.shape[1] else None
    rates = [num(g(c)) for c in L.rates]
    final = rates[-1]
    base = markup = None
    if len(rates) >= 3:
        base, markup = rates[-3], rates[-2]
    return dict(
        i=i, excel_row=i + 1, no=num(g(L.no)), text=text(g(L.desc)), unit=text(g(L.unit)) or None,
        qty=num(g(L.qty)), base=base, markup=markup, rate=final, total=num(g(L.total)),
        raw_qty=g(L.qty), raw_total=g(L.total), raw_sub=g(L.sub_qty) if L.sub_qty else None,
        all_text=" ".join(norm(v) for v in df.iloc[i].tolist() if isinstance(v, str)),
        extra_rates=[num(g(c)) for c in L.rates[:-1]] if len(L.rates) < 3 else [],
    )


def _numeric_values(r: dict):
    return [v for v in (r["qty"], r["base"], r["markup"], r["rate"], r["total"]) if v is not None]


def parse_sheet(name: str, df: pd.DataFrame) -> tuple[list[dict], SheetMeta]:
    meta = SheetMeta(name=name)
    L = find_layout(df)
    if L is None:
        meta.skipped = "No BOQ header (qty / unit / rate / total) found"
        return [], meta
    meta.layout = L

    lines: list[dict] = []
    pending: list[dict] = []
    unit_wait: dict | None = None
    section_text = ""

    def make_line(item: dict, orphan: dict | None, unnumbered=False) -> dict:
        o = orphan or {}
        pick = lambda k: item[k] if item.get(k) is not None else o.get(k)
        texts = [t for t in (item["text"], o.get("text", "")) if t]
        desc = max(texts, key=len) if texts else ""
        label = min(texts, key=len) if len(texts) == 2 else (texts[0] if texts else "")
        return dict(sheet=name, row=item["excel_row"],
                    item_no=int(item["no"]) if item["no"] is not None else None,
                    section=section_text, label=label, description=desc, unit=pick("unit"),
                    qty=pick("qty"), base=pick("base"), markup=pick("markup"), rate=pick("rate"),
                    total=pick("total"), realigned=bool(orphan), unnumbered=unnumbered,
                    linked_row=o.get("excel_row") if orphan else None, _i=item["i"])

    for i in range(L.header_row + 1, len(df)):
        r = _read_row(df, i, L)
        if not r["all_text"] and not _numeric_values(r) and r["no"] is None:
            continue
        # drop pending orphans that are too far away to belong to a later item
        for o in [o for o in pending if i - o["i"] > 6]:
            pending.remove(o)
            lines.append(make_line(o, None, unnumbered=True))

        # stated grand total
        if any(p in r["all_text"] for p in GRAND_PATTERNS):
            val = r["total"]
            if val is None:
                nums = [num(v) for v in df.iloc[i].tolist() if num(v) is not None]
                val = nums[-1] if nums else None
            meta.stated_total, meta.stated_row = val, r["excel_row"]
            continue

        # unpriced block (columns shifted: unit sits under the quantity column, name under total)
        if isinstance(r["raw_qty"], str) and norm(r["raw_qty"]) in UNIT_WORDS and isinstance(r["raw_total"], str):
            qty = num(df.iat[i, L.qty + 1]) if L.qty + 1 < df.shape[1] else None
            if qty is None and L.sub_qty is not None:
                qty = num(r["raw_sub"])
            meta.unpriced.append(dict(row=r["excel_row"], name=text(r["raw_total"]),
                                      unit=text(r["raw_qty"]), qty=qty))
            continue

        tn = norm(r["text"])
        sect = section_ordinal(tn)
        if sect is not None:
            meta.sections.append((sect, r["excel_row"], r["text"]))
            section_text = r["text"]

        for k, v in enumerate(r["extra_rates"]):
            if v is not None:
                meta.stray.append(dict(row=r["excel_row"], value=v, column=L.rates[k] + 1))

        has_data = bool(_numeric_values(r)) and any(v != 0 for v in _numeric_values(r))

        if r["no"] is not None:                                   # ---- numbered item row
            meta.item_numbers.append((int(r["no"]), r["excel_row"]))
            if any(t in tn for t in NOTE_WORDS) and not _numeric_values(r):
                meta.notes.append(dict(item_no=int(r["no"]), row=r["excel_row"], text=r["text"]))
                continue
            missing = [k for k in ("qty", "rate", "total") if r[k] is None]
            orphan = pending.pop(0) if (missing and pending) else None
            merged_missing = [k for k in missing if orphan is None or orphan[k] is None]
            if orphan is None and len(missing) == 3 and not r["base"]:
                meta.numbered_empty.append(dict(item_no=int(r["no"]), row=r["excel_row"], text=r["text"]))
                continue
            line = make_line(r, orphan)
            lines.append(line)
            unit_wait = line if not line["unit"] else None
        else:                                                      # ---- unnumbered row
            if has_data:
                pending.append(r)
            elif unit_wait is not None:
                if r["unit"] and r["i"] - unit_wait["_i"] <= 3:
                    unit_wait["unit"] = r["unit"]
                    if r["text"]:
                        unit_wait["label"] = (unit_wait["label"] + " | " + r["text"]).strip(" |")
                    unit_wait = None
                elif r["i"] - unit_wait["_i"] > 3:
                    unit_wait = None

    for o in pending:                                              # never matched to an item
        lines.append(make_line(o, None, unnumbered=True))
    return lines, meta


def parse_workbook(sheets: dict[str, pd.DataFrame]) -> Parsed:
    all_lines, metas = [], []
    for name, df in sheets.items():
        lines, meta = parse_sheet(name, df)
        all_lines.extend(lines)
        metas.append(meta)
    df = pd.DataFrame(all_lines)
    if df.empty:
        return Parsed(pd.DataFrame(columns=LINE_COLUMNS), metas)
    df = df.drop(columns=["_i"])
    df["total_calc"] = df.apply(lambda r: r["qty"] * r["rate"] if pd.notna(r["qty"]) and pd.notna(r["rate"]) else None, axis=1)
    df["diff"] = df.apply(lambda r: r["total"] - r["total_calc"] if pd.notna(r["total"]) and pd.notna(r["total_calc"]) else None, axis=1)
    order = {m.name: k for k, m in enumerate(metas)}
    df["_o"] = df["sheet"].map(order)
    df = df.sort_values(["_o", "row"], kind="stable").reset_index(drop=True)
    return Parsed(df[LINE_COLUMNS], metas)
