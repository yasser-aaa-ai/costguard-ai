"""AI layer.

Design rule: the deterministic checks produce the *evidence*; the model only explains it,
suggests a likely cause and a fix, and writes the executive summary. It is told to use only
the numbers it is given. If no API key is configured (or a call fails) a template-based
fallback fills the same fields, and the UI labels the source so nobody mistakes one for the other.
"""
from __future__ import annotations

import json
import os
import re

DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
CHUNK = 25
MAX_FINDINGS = 60

SYSTEM_QA = (
    "You are a senior cost consultant reviewing a Bill of Quantities (BOQ) before it is issued. "
    "You receive findings that were produced by deterministic checks; each has evidence. "
    "Rules: (1) use ONLY the facts and numbers given – never invent prices, quantities or sources; "
    "(2) separate what is certain from what is a likely cause (say 'likely' or 'possible'); "
    "(3) be concise and practical; (4) answer with a single JSON object and nothing else."
)

TEMPLATES = {
    "Calculation": (
        "The stated total does not equal quantity × rate.",
        "Replace hard-coded totals with a quantity × rate formula. If the difference is an intentional add-on, show it as its own labelled line.",
        "استبدل الإجماليات المكتوبة يدويًا بمعادلة (الكمية × الفئة). وإن كان الفرق مقصودًا فاعرضه في بند مستقل ومسمّى."),
    "Grand total": (
        "The grand total differs from the sum of its lines.",
        "Re-sum the sheet from its lines and correct either the grand total or the lines before issue.",
        "أعد جمع الشيت من بنوده وصحّح الإجمالي العام أو البنود قبل الإصدار."),
    "Rate build-up": (
        "The rate is not consistent with its base rate and markup.",
        "Make the markup formula identical on every line of the sheet and re-check the affected rate.",
        "وحّد معادلة نسبة الإضافة على كل بنود الشيت وراجع الفئة المتأثرة."),
    "Missing item": (
        "Something needed to price or locate this item is missing.",
        "Confirm whether the item is in scope; add the missing rate/unit/number or mark it provisional, and re-attach its description to its item number.",
        "تأكد هل البند ضمن النطاق؛ أضف السعر أو الوحدة أو الرقم الناقص أو علّمه مبدئيًا، وأعد ربط وصفه برقمه."),
    "Unit vs description": (
        "The unit of measure contradicts how the description says the item is measured.",
        "Decide which governs (description or unit), correct the other, then re-check the quantity and the rate basis.",
        "حدّد أيهما المعتمد (الوصف أم الوحدة) وصحّح الآخر، ثم راجع الكمية وأساس السعر."),
    "Numbering": (
        "The numbering sequence is broken or inconsistent.",
        "Renumber sections and items consistently and keep notes outside the numbered sequence.",
        "أعد ترقيم الأقسام والبنود بشكل متسق وأخرج الملاحظات من تسلسل الترقيم."),
    "Duplicate": (
        "Two items share the same description and rate.",
        "Confirm they cover different scope; if so, add the floor/zone qualifier to each description, otherwise delete one.",
        "تأكد أنهما لنطاقين مختلفين؛ فإن كان كذلك أضف الدور/المنطقة للوصف، وإلا فاحذف أحدهما."),
    "Price outlier": (
        "The rate is far from comparable lines.",
        "Check the rate against the pricing source for the same material and diameter; confirm the decimal place and the unit.",
        "قارن الفئة بمصدر التسعير لنفس المادة والقطر، وتأكد من الفاصلة العشرية والوحدة."),
    "Stray value": (
        "A number sits in an unused column.",
        "Delete the stray value or document what it is.",
        "احذف القيمة الشاردة أو وثّق ما تمثله."),
}


# ----------------------------------------------------------------------------- fallback
def fallback_enrich(findings: list[dict], stats: dict) -> tuple[dict, str]:
    out = {}
    for f in findings:
        expl, rec_en, rec_ar = TEMPLATES.get(f["check"], ("", "Review manually.", "راجع يدويًا."))
        out[f["id"]] = {"explanation": expl, "likely_cause": "", "recommendation_en": rec_en,
                        "recommendation_ar": rec_ar, "source": "Rule-based"}
    summary = (
        f"{stats['total']} findings across {stats['sheets']} sheets: {stats['high']} High, "
        f"{stats['medium']} Medium, {stats['low']} Low. "
        + (f"Largest single issue: {stats['top']}. " if stats.get("top") else "")
        + "Resolve all High items before the BOQ is issued, then review Medium items with the discipline lead."
    )
    return out, summary


# ----------------------------------------------------------------------------- Claude
def _client(api_key: str):
    import anthropic
    return anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=2)


def _json_from(msg) -> dict:
    txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
    return json.loads(txt)


def enrich_findings(findings: list[dict], stats: dict, api_key: str, model: str | None = None):
    """Returns (per-finding dict, executive summary, error or None)."""
    model = model or DEFAULT_MODEL
    fb, fb_summary = fallback_enrich(findings, stats)
    use = findings[:MAX_FINDINGS]
    try:
        client = _client(api_key)
        result, summary = {}, ""
        for k in range(0, len(use), CHUNK):
            chunk = use[k:k + CHUNK]
            want_summary = k == 0
            user = {
                "task": "For each finding give: explanation (1-2 sentences), likely_cause (one short sentence, hedged), "
                        "recommendation_en (one actionable sentence), recommendation_ar (same recommendation in Arabic)."
                        + (" Also give executive_summary (max 80 words) for a cost manager." if want_summary else ""),
                "output_schema": {"executive_summary": "string (only if asked)",
                                  "findings": [{"id": "F-001", "explanation": "", "likely_cause": "",
                                                "recommendation_en": "", "recommendation_ar": ""}]},
                "context": stats, "findings": chunk,
            }
            msg = client.messages.create(model=model, max_tokens=4000, system=SYSTEM_QA,
                                         messages=[{"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)}])
            data = _json_from(msg)
            if want_summary:
                summary = data.get("executive_summary", "")
            for item in data.get("findings", []):
                result[item["id"]] = {
                    "explanation": item.get("explanation", ""), "likely_cause": item.get("likely_cause", ""),
                    "recommendation_en": item.get("recommendation_en", ""),
                    "recommendation_ar": item.get("recommendation_ar", ""), "source": "AI (Claude)"}
        merged = {**fb, **result}                       # anything the model skipped keeps the fallback
        return merged, summary or fb_summary, None
    except Exception as exc:                              # network, auth, quota, bad JSON ...
        return fb, fb_summary, f"{type(exc).__name__}: {exc}"


def enrich_submittals(rows: list[dict], api_key: str, model: str | None = None):
    """rows: [{no, description, types}] -> ({no: note}, error|None). One short note per row."""
    model = model or DEFAULT_MODEL
    system = ("You help a document controller prepare a submittal register from BOQ descriptions. "
              "Use only the text given. For each row return one short note (max 25 words) naming the "
              "concrete items the contractor should submit (e.g. sample, data sheet, certificate) and any "
              "unclear requirement. Answer with one JSON object: {\"notes\": [{\"no\": \"SUB-001\", \"note\": \"\"}]}.")
    try:
        client = _client(api_key)
        notes = {}
        for k in range(0, len(rows), CHUNK):
            msg = client.messages.create(
                model=model, max_tokens=3000, system=system,
                messages=[{"role": "user", "content": json.dumps(rows[k:k + CHUNK], ensure_ascii=False, default=str)}])
            for it in _json_from(msg).get("notes", []):
                notes[it["no"]] = it.get("note", "")
        return notes, None
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"
