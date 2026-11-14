"""Excel exports (openpyxl). Values are a snapshot computed at export time; this is stated in the file."""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

FONT = "Arial"
HEAD_FILL = PatternFill("solid", fgColor="1F2A44")
SEV_FILL = {"High": "F8CBAD", "Medium": "FFE699", "Low": "DDEBF7", "Normal": "E2EFDA"}
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _write_table(ws, df: pd.DataFrame, start_row: int = 1, widths: dict | None = None,
                 wrap_cols: tuple = (), sev_col: str | None = None, num_cols: tuple = ()):
    for j, col in enumerate(df.columns, 1):
        c = ws.cell(row=start_row, column=j, value=col)
        c.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        c.fill = HEAD_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    for i, row in enumerate(df.itertuples(index=False), start_row + 1):
        for j, v in enumerate(row, 1):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                v = None
            elif hasattr(v, "item"):
                v = v.item()
            c = ws.cell(row=i, column=j, value=v)
            name = df.columns[j - 1]
            c.font = Font(name=FONT, size=10)
            c.border = BORDER
            c.alignment = Alignment(vertical="top", wrap_text=name in wrap_cols)
            if name in num_cols and isinstance(v, (int, float)):
                c.number_format = "#,##0.00;(#,##0.00);-"
            if sev_col and name == sev_col and v in SEV_FILL:
                c.fill = PatternFill("solid", fgColor=SEV_FILL[v])
                c.font = Font(name=FONT, size=10, bold=True)
    for j, col in enumerate(df.columns, 1):
        ws.column_dimensions[get_column_letter(j)].width = (widths or {}).get(col, max(12, min(40, len(str(col)) + 4)))
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(df.columns))}{start_row + max(len(df), 1)}"
    ws.sheet_view.rightToLeft = False


def _title(ws, text: str, sub: str = ""):
    ws["A1"] = text
    ws["A1"].font = Font(name=FONT, bold=True, size=14, color="1F2A44")
    if sub:
        ws["A2"] = sub
        ws["A2"].font = Font(name=FONT, italic=True, size=9, color="595959")


def build_report(source_name: str, kpis: dict, recon: pd.DataFrame, findings: pd.DataFrame,
                 lines: pd.DataFrame, summary: str, register: pd.DataFrame | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    _title(ws, "CostGuard AI – BOQ QA report",
           f"Source: {source_name} · generated {date.today().isoformat()} · values are a snapshot computed at export time; "
           "amounts are in the BOQ's own currency (not stated in the file).")
    r = 4
    for k, v in kpis.items():
        ws.cell(row=r, column=1, value=k).font = Font(name=FONT, bold=True, size=10)
        c = ws.cell(row=r, column=2, value=v)
        c.font = Font(name=FONT, size=10)
        if isinstance(v, (int, float)):
            c.number_format = "#,##0.00;(#,##0.00);-" if isinstance(v, float) else "#,##0"
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Executive summary").font = Font(name=FONT, bold=True, size=11, color="1F2A44")
    ws.cell(row=r + 1, column=1, value=summary).alignment = Alignment(wrap_text=True, vertical="top")
    ws.cell(row=r + 1, column=1).font = Font(name=FONT, size=10)
    ws.merge_cells(start_row=r + 1, start_column=1, end_row=r + 1, end_column=6)
    ws.row_dimensions[r + 1].height = 70
    r += 3
    ws.cell(row=r, column=1, value="Sheet reconciliation").font = Font(name=FONT, bold=True, size=11, color="1F2A44")
    _write_table(ws, recon, start_row=r + 1, num_cols=tuple(recon.columns[2:]),
                 widths={"Sheet": 30, "Lines": 10, "Stated total": 18, "Sum of line totals": 20,
                         "Recomputed (qty × rate)": 24, "Stated − recomputed": 20})
    ws.freeze_panes = None
    ws.auto_filter.ref = None
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 20

    wf = wb.create_sheet("Findings")
    _title(wf, "Findings", "Each row is evidence-backed by a deterministic check; the recommendation columns are labelled with their source.")
    _write_table(wf, findings, start_row=4, sev_col="severity", num_cols=("amount",),
                 wrap_cols=("message", "evidence", "explanation", "recommendation_en", "recommendation_ar"),
                 widths={"id": 8, "severity": 10, "check": 18, "sheet": 20, "row": 7, "item": 12, "message": 60,
                         "evidence": 45, "amount": 14, "explanation": 40, "recommendation_en": 55,
                         "recommendation_ar": 55, "rec_source": 14})

    wl = wb.create_sheet("Clean BOQ")
    _title(wl, "Clean BOQ (re-linked lines)", "One row per priced line. 'realigned' = numbers were on a different row from the item and were re-linked; 'linked_row' shows where they came from.")
    _write_table(wl, lines, start_row=4, num_cols=("qty", "base", "markup", "rate", "total", "total_calc", "diff"),
                 wrap_cols=("description",), widths={"description": 70, "label": 30, "sheet": 20, "section": 24})

    if register is not None and not register.empty:
        add_register_sheet(wb, register, "Submittal Register")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def add_register_sheet(wb: Workbook, register: pd.DataFrame, title: str):
    ws = wb.create_sheet(title)
    _title(ws, "Submittal Register", "Pre-filled from BOQ descriptions. Edit the yellow columns (Status, Reviewer, Required By, Date Submitted, Revision).")
    _write_table(ws, register, start_row=4, sev_col="Priority",
                 wrap_cols=("Description", "Submittal Types", "Suggested Submittals", "AI Note"),
                 widths={"Submittal No": 12, "Sheet": 20, "BOQ Item": 9, "Section": 24, "Description": 60,
                         "Submittal Types": 34, "Brand / Manufacturer": 22, "Suggested Submittals": 40,
                         "Priority": 10, "Status": 12, "Responsible": 13, "Reviewer": 14, "Required By": 13,
                         "Date Submitted": 14, "Revision": 9, "AI Note": 40})
    cols = {c: i + 1 for i, c in enumerate(register.columns)}
    last = 4 + len(register)
    for name in ("Status", "Reviewer", "Required By", "Date Submitted", "Revision"):
        for rr in range(5, last + 1):
            ws.cell(row=rr, column=cols[name]).fill = PatternFill("solid", fgColor="FFF2CC")
    dv = DataValidation(type="list", formula1='"Pending,Submitted,Approved,Approved as noted,Revise & resubmit,Rejected"',
                        allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{get_column_letter(cols['Status'])}5:{get_column_letter(cols['Status'])}{last}")
    return ws


def build_register_workbook(register: pd.DataFrame) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    add_register_sheet(wb, register, "Submittal Register")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
