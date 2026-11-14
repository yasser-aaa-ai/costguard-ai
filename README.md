# 🛡️ CostGuard AI — BOQ QA Copilot + Submittal Register Generator

Prototype for the **AAA Cost Code AI Challenge 2026**. Upload a BOQ (Arabic/English, `.xls/.xlsx/.csv`) and get an
evidence-backed QA review, then generate a submittal register from the same file.

> Sample data only. `data/sample_boq_anonymized.xlsx` is a 2015 historical estimate with owner / project /
> contractor lines removed and no author metadata. Do not upload confidential client data.

## What it does

| Tab | Features |
|---|---|
| **1 · BOQ Quality Assurance Copilot** | Re-links detached description/number rows · calculation validation (qty × rate vs total, hard-coded differences, grand totals, rate build-up) · missing items (unpriced, no number, no unit) · unit-vs-description consistency · numbering validation (sections and items) · duplicate descriptions · conservative price-outlier test · AI explanations, likely cause and recommendation (EN + AR) · Excel report |
| **2 · Submittal Register Generator** | Extracts submittal requirements (sample approval, "or equal", named manufacturers, approved-manufacturer lists, shop drawings, colour/finish, standards) · builds a register with status drop-down · optional AI notes · Excel export |

## Architecture

```
 upload (.xls/.xlsx/.csv)                       data/sample_boq_anonymized.xlsx
            │                                                │
            └──────────────► core/parser.py ◄────────────────┘
                 detect header → classify rows → re-link detached numbers
                 → clean lines table + per-sheet meta (sections, notes, unpriced, stray)
                                   │
              ┌────────────────────┴─────────────────────┐
              ▼                                          ▼
      core/checks.py (deterministic)             core/submittals.py (rules)
      findings WITH evidence                      register rows
              │                                          │
              ▼                                          ▼
      core/ai.py  ── Claude explains, hedges      core/ai.py (optional notes)
      the cause, recommends (EN/AR),             
      writes the summary.  No key / error →       
      template fallback, labelled "Rule-based"    
              │                                          │
              └──────────────► core/report.py ◄──────────┘
                               Excel exports      app.py = Streamlit UI
```

**Design rule:** rules produce the *evidence*; the model only explains it and may not invent numbers. Every
recommendation shows its source (`AI (Claude)` or `Rule-based`).

## UI design

- Sidebar: ① source (sample / upload) + settings, ② AI status.
- Tab 1: KPI row → sheet reconciliation → filterable findings table → AI button + executive summary →
  "Inspect a finding" card (evidence, why it matters, recommendation in English and Arabic) → Excel download.
- Tab 2: KPIs → submittal-type chart → filterable register → AI notes → Excel download.

## Folder structure

```
costguard-ai/
├─ app.py                     # Streamlit UI (2 tabs)
├─ requirements.txt           # pinned to the tested versions
├─ README.md
├─ .gitignore
├─ .streamlit/config.toml     # theme
├─ core/
│  ├─ parser.py               # load + re-link rows
│  ├─ checks.py               # QA checks
│  ├─ submittals.py           # Tab 2 extraction
│  ├─ ai.py                   # Claude + fallback
│  └─ report.py               # Excel exports
├─ data/sample_boq_anonymized.xlsx
└─ tests/test_core.py         # 10 regression tests on the sample
```

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
pytest -q tests
# optional AI: export ANTHROPIC_API_KEY=...   (or .streamlit/secrets.toml, never committed)
```

## Deployment plan (Streamlit Community Cloud)

1. Push this folder to a GitHub repo (public is simplest so judges can open the source).
2. share.streamlit.io → **New app** → pick repo, branch `main`, main file `app.py`.
3. **Advanced settings → Secrets**: `ANTHROPIC_API_KEY = "sk-ant-..."`. Optional: `CLAUDE_MODEL`.
4. Deploy, then **test the link from a different browser/device with no login**.
5. Cost guard: the app caps AI runs per session (`MAX_AI_RUNS`); also set a spend limit on the API key.
6. Keep it reachable until **3 Dec 2026**. Free apps can go to sleep when idle (as far as I know): open the link
   shortly before judging and put "click *Wake up* if you see a sleep screen" in the judge instructions.
7. Fallback if hosting fails: the app runs from a clone with the 3 commands above; record the demo video from a
   local run in any case.

## Known limits (be honest with the judges)

- The parser was developed on this file's layout family. It is tested on one real BOQ and one small synthetic
  file; other client layouts will need adjustment (header words, unit spellings).
- Price-outlier detection is statistical and needs ≥ 6 comparable lines; on this sample it finds one candidate.
- Submittal extraction is phrase-based (Arabic/English); unusual wording will be missed.
- The AI layer needs an API key; without it the app still works with template recommendations.
- Findings are review aids; a qualified person must confirm each one.

## Scale path

Per-client "profile" for header words and units → batch upload of many BOQs → every reviewed BOQ feeds a firm
price database → turns the outlier check into real benchmarking → rule packs per project type → SSO + audit log.
