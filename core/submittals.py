"""Submittal Register Generator – rule-based extraction (Arabic + English) from BOQ descriptions.

Reads the *parsed* lines (descriptions already re-linked to their numbers), so it benefits
from the same clean-up as the QA tab.
"""
from __future__ import annotations

import re

import pandas as pd

from .parser import norm

# (type, regex on normalised text, what the contractor normally has to submit)
RULES = [
    ("Sample approval", r"اعتماد ال?عينه|العينات? المعتمده|العينه المعتمده|approved sample|sample approval",
     "Physical sample + technical data sheet"),
    ("Approved-equal alternative", r"او ما ?يماثل|او ما ?يعادل|or equal|approved equal",
     "Equivalence statement against the specified product"),
    ("Approved manufacturer list", r"الشركات المذكوره|من نفس انتاج",
     "Product from the listed manufacturers + catalogue"),
    ("Shop drawings", r"الرسومات التفصيليه|shop drawing", "Shop drawings"),
    ("Colour / finish selection", r"باللون المطلوب|اللون المطلوب", "Colour / finish sample"),
    ("Standards compliance", r"nfpa|الدفاع المدني|\biso ?\d|\bul ?\d", "Compliance certificate / approval"),
]
BRAND_RX = [
    re.compile(r"(?:انتاج|إنتاج)\s*شرك[ةه]\s+([^\s،,.:()]+(?:\s+[^\s،,.:()]+)?)"),
    re.compile(r"ماركة\s+([A-Za-z0-9\u0600-\u06FF ]+?)(?=\s*[.،]|\s+و(?:جميع|المفتاح)|$)"),
]
STOP = {"او", "أو", "ما", "مايماثلها", "بعد", "و", "موصى", "موصي", "بها", "من", "هذه", "الشركات"}

REGISTER_COLUMNS = ["Submittal No", "Sheet", "BOQ Item", "Section", "Description", "Submittal Types",
                    "Brand / Manufacturer", "Suggested Submittals", "Priority", "Status",
                    "Responsible", "Reviewer", "Required By", "Date Submitted", "Revision", "AI Note"]


def _brand(raw: str) -> str:
    names = []
    for rx in BRAND_RX:
        for m in rx.finditer(raw):
            for part in re.split(r"\s+(?:او|أو)\s+", m.group(1).strip()):
                words = [w for w in part.split() if w not in STOP]
                if words:
                    names.append(" ".join(words))
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return " / ".join(out)


def extract(lines: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in lines.iterrows():
        raw = str(r["description"])
        tn = norm(raw)
        types, deliver = [], []
        for name, rx, what in RULES:
            if re.search(rx, tn):
                types.append(name)
                deliver.append(what)
        brand = _brand(raw)
        if brand:
            types.append("Manufacturer named")
            deliver.append("Manufacturer catalogue / data sheet")
        if not types:
            continue
        item = "" if pd.isna(r["item_no"]) else str(int(r["item_no"]))
        n = len(types)
        rows.append({
            "Sheet": r["sheet"], "BOQ Item": item, "Section": str(r["section"]).strip(" :"),
            "Description": re.sub(r"\s+", " ", raw)[:220],
            "Submittal Types": " | ".join(types), "Brand / Manufacturer": brand,
            "Suggested Submittals": "; ".join(dict.fromkeys(deliver)),
            "Priority": "High" if n >= 3 else "Medium" if n == 2 else "Normal",
            "Status": "Pending", "Responsible": "Contractor", "Reviewer": "", "Required By": "",
            "Date Submitted": "", "Revision": "", "AI Note": "", "_n": n,
        })
    if not rows:
        return pd.DataFrame(columns=REGISTER_COLUMNS)
    df = pd.DataFrame(rows)
    rank = {"High": 0, "Medium": 1, "Normal": 2}
    df["_r"] = df["Priority"].map(rank)
    df = df.sort_values(["_r", "Sheet"], kind="stable").reset_index(drop=True)
    df.insert(0, "Submittal No", [f"SUB-{i:03d}" for i in range(1, len(df) + 1)])
    return df[REGISTER_COLUMNS]


def type_counts(register: pd.DataFrame) -> pd.Series:
    if register.empty:
        return pd.Series(dtype=int)
    return register["Submittal Types"].str.split(" | ", regex=False).explode().value_counts()
