"""Build the sample-plan workbook (deliverable 02) from the current FestOS database.
   python3 tools/make_workbook.py [out.xlsx]"""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from festos import exports, verify, config
from festos.db import connect, get_setting

F = "Arial"
HEAD = PatternFill("solid", fgColor="272566")
INPUT = Font(name=F, color="0000FF")


def sheet_from_export(wb, con, name, title, widths=None, note=None):
    buf = io.StringIO()
    exports.write(con, name, csv.writer(buf))
    rows = list(csv.reader(io.StringIO(buf.getvalue())))
    ws = wb.create_sheet(title)
    r0 = 1
    if note:
        ws.cell(1, 1, note).font = Font(name=F, italic=True, color="4A5468")
        r0 = 3
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            if v != "" and v.lstrip("-").replace(".", "", 1).isdigit() and not (name == "accommodation_plan" and j in (0,)):
                v = float(v) if "." in v else int(v)
            c = ws.cell(r0 + i, j + 1, v)
            c.font = Font(name=F, bold=(i == 0), color="FFFFFF" if i == 0 else "1A2233", size=10)
            if i == 0:
                c.fill = HEAD
    ws.freeze_panes = ws.cell(r0 + 1, 1)
    ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(rows[0]))}{r0 + len(rows) - 1}"
    for j, w in enumerate(widths or []):
        ws.column_dimensions[get_column_letter(j + 1)].width = w
    return ws, r0, len(rows)


def main(out):
    con = connect()
    wb = Workbook()
    summ = wb.active
    summ.title = "Summary"
    acc, a0, an = sheet_from_export(wb, con, "accommodation_plan", "Accommodation",
                                    [9, 20, 11, 11, 10, 9, 8, 10, 8, 22, 12, 5, 26, 12, 90],
                                    "One row per person needing a bed. Explanation lists every rule and preference applied.")
    sch, s0, sn = sheet_from_export(wb, con, "schedule", "Schedule", [10, 8, 18, 9, 16, 17, 17, 12, 12],
                                    "114 heats. Semis/finals pools are filled from results, so 'competitors_assigned' is 0 for them.")
    sheet_from_export(wb, con, "heat_assignments", "Heat seats", [9, 20, 10, 8, 12, 10, 17, 16])
    sheet_from_export(wb, con, "equipment_moves", "Equipment plan", [10, 17, 15, 6, 18, 16, 50],
                      "Pick list generated from the schedule; SHORTAGE rows are kit that does not exist in usable condition.")
    mer, m0, mn = sheet_from_export(wb, con, "merch_plan", "Merchandise", [14, 8, 10, 10, 8, 10, 14, 10, 18])
    sheet_from_export(wb, con, "food_coverage", "Food coverage", [22, 18, 9, 9, 7, 40])
    sheet_from_export(wb, con, "data_issues", "Data findings", [6, 12, 12, 26, 9, 18, 110, 9, 30])

    ver = wb.create_sheet("Verification")
    ver.append(["Hard rule (re-checked independently from the stored plan)", "Result", "Violations", "Detail"])
    for c in ver[1]:
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = HEAD
    for r in verify.check(con):
        ver.append([r["rule"], "PASS" if r["ok"] else "FAIL", r["violations"], r["detail"]])
    for row in ver.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=F, size=10, color="0F7F78" if row[1].value == "PASS" else "C81D3A") if c.column == 2 else Font(name=F, size=10)
    ver.column_dimensions["A"].width = 90
    ver.column_dimensions["D"].width = 30

    asm = wb.create_sheet("Assumptions")
    asm.append(["Category", "Setting", "Value", "Why"])
    for c in asm[1]:
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = HEAD
    for k, (v, cat, why) in config.ASSUMPTIONS.items():
        val = get_setting(con, k)
        asm.append([cat, k, val if isinstance(val, (int, float, str)) else str(val), why])
    for row in asm.iter_rows(min_row=2):
        for c in row:
            c.font = INPUT if c.column == 3 else Font(name=F, size=10)
            c.alignment = Alignment(wrap_text=c.column == 4, vertical="top")
    asm.column_dimensions["A"].width = 14
    asm.column_dimensions["B"].width = 32
    asm.column_dimensions["C"].width = 22
    asm.column_dimensions["D"].width = 100

    # ---------------- summary with live formulas
    A = f"Accommodation!$H${a0+1}:$H${a0+an-1}"
    COST = f"Accommodation!$N${a0+1}:$N${a0+an-1}"
    KIND = f"Accommodation!$C${a0+1}:$C${a0+an-1}"
    NEED = f"Accommodation!$F${a0+1}:$F${a0+an-1}"
    rows = [
        ("FestOS — sample plan generated from the Aarohan data pack", None, None),
        ("Figures below are formulas over the other tabs; filter or edit a tab and they update.", None, None),
        (None, None, None),
        ("Accommodation", None, None),
        ("People needing a bed", f"=COUNTA(Accommodation!$A${a0+1}:$A${a0+an-1})", "rows in Accommodation"),
        ("Housed", f'=COUNTIF({A},"placed")', ""),
        ("Waiting for a bed", f'=COUNTIF({A},"unplaced")', "every row carries the reason"),
        ("Share housed", "=IFERROR(B6/B5,0)", ""),
        ("Waiting with a wheelchair need", f'=COUNTIFS({A},"unplaced",{NEED},"WC")', "hard rule: never placed in a non-accessible bed"),
        ("External spend (₹)", f"=SUM({COST})", ""),
        ("External budget (₹)", get_setting(con, "budget_external_inr"), "assumption — not in the pack"),
        ("Budget headroom (₹)", "=B11-B10", ""),
        ("Faculty escorts housed", f'=COUNTIFS({KIND},"escort",{A},"placed")', "of 38"),
        (None, None, None),
        ("Schedule", None, None),
        ("Heats", f"=COUNTA(Schedule!$A${s0+1}:$A${s0+sn-1})", ""),
        ("Finals scheduled", f'=COUNTIF(Schedule!$D${s0+1}:$D${s0+sn-1},"finals")', ""),
        ("Seats assigned in prelim / single heats", f"=SUM(Schedule!$H${s0+1}:$H${s0+sn-1})", "of 5,574 plannable registrations"),
        (None, None, None),
        ("Merchandise", None, None),
        ("ID cards on hand", f'=SUMIFS(Merchandise!$F$2:$F${mn},Merchandise!$A$2:$A${mn},"ID card")', ""),
        ("ID cards needed", f'=SUMIFS(Merchandise!$G$2:$G${mn},Merchandise!$A$2:$A${mn},"ID card")', ""),
        ("ID card shortfall", "=MAX(0,B22-B21)", "reprint before arrivals"),
        ("T-shirt shortfall (all sizes)", f'=-SUMIFS(Merchandise!$H$2:$H${mn},Merchandise!$A$2:$A${mn},"T-shirt",Merchandise!$H$2:$H${mn},"<0")', ""),
        (None, None, None),
        ("Verification", None, None),
        ("Hard rules checked", "=COUNTA(Verification!$A$2:$A$100)", ""),
        ("Hard rules holding", '=COUNTIF(Verification!$B$2:$B$100,"PASS")', ""),
    ]
    for i, (a, b, c) in enumerate(rows, start=1):
        summ.cell(i, 1, a)
        if b is not None:
            summ.cell(i, 2, b)
        if c:
            summ.cell(i, 3, c)
        for j in (1, 2, 3):
            summ.cell(i, j).font = Font(name=F, size=10, color="7D869A" if j == 3 else "1A2233")
    summ["A1"].font = Font(name=F, size=14, bold=True, color="272566")
    for r in (4, 15, 20, 26):
        summ.cell(r, 1).font = Font(name=F, size=11, bold=True, color="272566")
    summ["B11"].font = INPUT
    for r in (10, 11, 12):
        summ.cell(r, 2).number_format = "₹#,##0"
    summ["B8"].number_format = "0.0%"
    for r in (5, 6, 7, 9, 13, 16, 17, 18, 21, 22, 23, 24, 27, 28):
        summ.cell(r, 2).number_format = "#,##0"
    summ.column_dimensions["A"].width = 42
    summ.column_dimensions["B"].width = 16
    summ.column_dimensions["C"].width = 50
    wb.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports", "FestOS_Sample_Plans.xlsx"))
