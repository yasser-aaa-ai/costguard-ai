"""CostGuard AI – BOQ QA Copilot + Submittal Register Generator (Streamlit)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from core import ai, checks, parser, report, submittals

APP_DIR = Path(__file__).parent
SAMPLE = APP_DIR / "data" / "sample_boq_anonymized.xlsx"
MAX_AI_RUNS = 5          # per browser session – protects the API budget on a public link

st.set_page_config(page_title="CostGuard AI – BOQ QA Copilot", page_icon="🛡️", layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 1.6rem; max-width: 1250px;}
  .hero {background: linear-gradient(135deg,#1f2a44 0%,#33457a 100%); color:#fff; padding:1.1rem 1.4rem;
         border-radius:14px; margin-bottom:1rem;}
  .hero h1 {margin:0; font-size:1.7rem; color:#fff;}
  .hero p {margin:.25rem 0 0; opacity:.85; font-size:.95rem;}
  .card {border:1px solid #e3e6ee; border-radius:12px; padding:.9rem 1.1rem; background:#fafbfe; margin:.4rem 0;}
  .pill {display:inline-block; padding:.05rem .55rem; border-radius:999px; font-size:.78rem; font-weight:600;}
  .High {background:#f8cbad;} .Medium {background:#ffe699;} .Low {background:#ddebf7;} .Normal {background:#e2efda;}
  .src {font-size:.75rem; color:#667; }
  [data-testid="stMetricValue"] {font-size:1.6rem;}
</style>
""", unsafe_allow_html=True)

st.markdown("""<div class="hero"><h1>🛡️ CostGuard AI</h1>
<p>Upload a BOQ → get an evidence-backed QA review in seconds, then generate the submittal register from the same file.</p></div>""",
            unsafe_allow_html=True)


# ----------------------------------------------------------------------------- helpers
def get_api_key() -> str | None:
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        key = None
    return key or os.getenv("ANTHROPIC_API_KEY")


@st.cache_data(show_spinner=False)
def load_sample_bytes() -> tuple[bytes, str]:
    return SAMPLE.read_bytes(), SAMPLE.name


@st.cache_data(show_spinner="Reading the file and re-linking detached rows…")
def analyse(data: bytes, name: str, tol: float, z: float):
    parsed = parser.parse_workbook(parser.read_workbook(data, name))
    found = checks.run_checks(parsed, tol, z)
    fdf = checks.findings_frame(found)
    recon = checks.reconciliation(parsed)
    reg = submittals.extract(parsed.lines)
    return parsed, fdf, recon, reg


@st.cache_data(show_spinner=False)
def cached_qa_report(fname, metrics, recon, fdf, lines, summary_txt, register):
    return report.build_report(fname, metrics, recon, fdf, lines, summary_txt, register)


@st.cache_data(show_spinner=False)
def cached_register_report(register):
    return report.build_register_workbook(register)


def clean(record: dict) -> dict:
    return {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in record.items()}


def money(x) -> str:
    return "-" if x is None or pd.isna(x) else f"{x:,.0f}"


# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("1 · BOQ source")
    source = st.radio("Choose", ["Use anonymized sample", "Upload my file"], label_visibility="collapsed")
    upload = None
    if source == "Upload my file":
        upload = st.file_uploader("BOQ (.xls / .xlsx / .csv)", type=["xls", "xlsx", "csv"])
        st.caption("Use sample, anonymised or public data only. Files are processed in memory and not stored by the app.")
    with st.expander("Settings"):
        tol = st.number_input("Calculation tolerance (absolute)", 0.0, 100.0, 0.5, 0.1)
        zcut = st.slider("Price-outlier sensitivity (lower = more alerts)", 2.5, 6.0, 3.5, 0.1)
    st.header("2 · AI")
    st.write("🟢 Claude connected" if get_api_key() else "🟡 Hybrid AI Validation Engine")
    st.caption(f"Model: `{ai.DEFAULT_MODEL}` · {MAX_AI_RUNS} AI runs per session")

if source == "Use anonymized sample":
    data, fname = load_sample_bytes()
else:
    if upload is None:
        st.info("Upload a BOQ file in the sidebar, or switch to the anonymized sample.")
        st.stop()
    data, fname = upload.getvalue(), upload.name

try:
    parsed, base_fdf, recon, base_register = analyse(data, fname, float(tol), float(zcut))
except Exception as exc:
    st.error(f"Could not read this file: {exc}")
    st.stop()

for m in parsed.metas:
    if m.skipped:
        st.warning(f"Sheet “{m.name}” skipped: {m.skipped}.")
if parsed.lines.empty:
    st.error("No BOQ lines were detected. The file needs a header row with quantity, unit, rate and total columns.")
    st.stop()

run_key = hashlib.md5(data + f"{tol}{zcut}".encode()).hexdigest()[:12]
st.session_state.setdefault("ai", {})
st.session_state.setdefault("sub_notes", {})
st.session_state.setdefault("ai_runs", 0)

fdf = base_fdf.copy()
ai_res = st.session_state["ai"].get(run_key)
for col in ("explanation", "recommendation_en", "recommendation_ar", "rec_source"):
    fdf[col] = ""
if ai_res:
    for i, row in fdf.iterrows():
        a = ai_res["items"].get(row["id"])
        if a:
            fdf.loc[i, ["explanation", "recommendation_en", "recommendation_ar", "rec_source"]] = [
                a["explanation"], a["recommendation_en"], a["recommendation_ar"], a["source"]]

n_high, n_med, n_low = (int((fdf["severity"] == s).sum()) for s in ("High", "Medium", "Low"))
at_stake = float(fdf.loc[fdf["severity"].isin(["High", "Medium"]), "amount"].abs().sum())

register = base_register.copy()
notes = st.session_state["sub_notes"].get(run_key, {})
if notes and not register.empty:
    register["AI Note"] = register["Submittal No"].map(notes).fillna("")

tab1, tab2 = st.tabs(["🔍 BOQ Quality Assurance Copilot", "📋 Submittal Register Generator"])

# ============================================================================= TAB 1
with tab1:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Lines analysed", len(parsed.lines))
    c2.metric("Detached rows re-linked", parsed.realigned_count,
              help="Numbers that sat on a different row from their item/description and were re-attached.")
    c3.metric("Findings", len(fdf), f"{n_high} High · {n_med} Med · {n_low} Low", delta_color="off")
    c4.metric("Value at stake", money(at_stake),
              help="Indicative sum of amounts on High/Medium findings, in the BOQ's own currency. Findings can overlap.")
    c5.metric("Sheets", int((recon["Lines"] > 0).sum()))

    with st.expander("Sheet reconciliation – stated totals vs recomputed", expanded=True):
        show = recon.copy()
        st.dataframe(show, hide_index=True, width="stretch", column_config={
            c: st.column_config.NumberColumn(format="%,.2f") for c in show.columns[2:]})
        gap = recon.loc[recon["Stated − recomputed"].abs() > tol]
        if len(gap):
            for _, r in gap.iterrows():
                st.warning(f"**{r['Sheet']}**: the stated total is {r['Stated − recomputed']:,.2f} higher than "
                           f"quantity × rate gives ({r['Stated − recomputed'] / r['Recomputed (qty × rate)']:.1%}).")
        else:
            st.success("Every sheet's stated total equals quantity × rate.")

    st.subheader("Findings")
    f1, f2, f3 = st.columns([1.2, 1.4, 1.4])
    sev_sel = f1.multiselect("Severity", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
    chk_sel = f2.multiselect("Check", checks.ALL_CHECKS, default=[c for c in checks.ALL_CHECKS if c in set(fdf["check"])])
    sh_sel = f3.multiselect("Sheet", list(dict.fromkeys(fdf["sheet"])), default=list(dict.fromkeys(fdf["sheet"])))
    view = fdf[fdf["severity"].isin(sev_sel) & fdf["check"].isin(chk_sel) & fdf["sheet"].isin(sh_sel)]
    cols = ["id", "severity", "check", "sheet", "row", "item", "message", "amount"] + (["recommendation_en", "rec_source"] if ai_res else [])
    st.dataframe(view[cols], hide_index=True, width="stretch", height=min(480, 60 + 36 * len(view)),
                 column_config={"amount": st.column_config.NumberColumn("amount", format="%,.2f"),
                                "message": st.column_config.TextColumn("finding", width="large"),
                                "recommendation_en": st.column_config.TextColumn("recommendation", width="large")})

    b1, b2 = st.columns([1.4, 3])
    if b1.button("✨ Generate AI explanations & recommendations", type="primary"):
        stats = dict(total=len(fdf), sheets=int((recon["Lines"] > 0).sum()), high=n_high, medium=n_med, low=n_low,
                     top=(fdf.sort_values("amount", key=lambda s: s.abs(), ascending=False).iloc[0]["message"] if len(fdf) else ""),
                     reconciliation=recon.to_dict("records"))
        payload = [clean(r) for r in fdf[["id", "severity", "check", "sheet", "row", "item", "message", "evidence", "amount"]].to_dict("records")]
        key, err = get_api_key(), None
        if key and st.session_state["ai_runs"] < MAX_AI_RUNS:
            with st.spinner("Claude is reviewing the findings…"):
                items, summary, err = ai.enrich_findings(payload, stats, key)
            st.session_state["ai_runs"] += 1
        else:
            items, summary = ai.fallback_enrich(payload, stats)
            err = "No API key configured – showing rule-based recommendations." if not key else "AI run limit reached for this session."
        st.session_state["ai"][run_key] = {"items": items, "summary": summary, "error": err}
        st.rerun()
    ai_res = st.session_state["ai"].get(run_key)
    if ai_res:
        if ai_res["error"]:
            b2.warning(f"Using rule-based recommendations. Reason: {ai_res['error'][:180]}")
        st.markdown(f'<div class="card"><b>Executive summary</b><br>{ai_res["summary"]}</div>', unsafe_allow_html=True)

    if len(view):
        st.subheader("Inspect a finding")
        pick = st.selectbox("Finding", view["id"].tolist(),
                            format_func=lambda i: f"{i} · {view.set_index('id').loc[i, 'check']} · "
if t1.button("✨ Add AI notes (what exactly to submit)"):
key = get_api_key()
if key and st.session_state["ai_runs"] < MAX_AI_RUNS:
rows = [{"no": r["Submittal No"], "description": r["Description"], "types": r["Submittal Types"]}
for _, r in register.iterrows()]
with st.spinner("Claude is reading the requirements…"):
out, err = ai.enrich_submittals(rows, key)
st.session_state["ai_runs"] += 1
if err:
t2.warning(f"AI notes unavailable: {err[:160]}")
else:
st.session_state["sub_notes"][run_key] = out
st.rerun()
else:
t2.warning("AI notes need an API key (or the session limit was reached). The register works without them.")
st.download_button("⬇️ Download Submittal Register (Excel)", cached_register_report(register),
"Submittal_Register.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
st.divider()
st.caption("CostGuard AI · prototype for the AAA Cost Code AI Challenge 2026 · sample data only · "
"findings are review aids – a qualified person must confirm each one.")


st.divider()
st.caption("CostGuard AI · prototype for the AAA Cost Code AI Challenge 2026 · sample data only · "
           "findings are review aids – a qualified person must confirm each one.")
