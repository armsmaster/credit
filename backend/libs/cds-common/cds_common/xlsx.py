"""Excel workbooks with live formulas: building blocks of the calculation-detail exports.

Conventions of every export:
* grey cells hold values taken from the system (inputs and results of the calculation);
* white cells are Excel formulas that recompute the system's numbers from those inputs;
* green cells are checks: Excel result minus system result (should be ~0);
* yellow cells may be changed by the user (calculators);
* hover a cell with a red corner to read what it means and how it is computed.
Only functions available in Excel 2010+ are used (no LET/LAMBDA), so the files open everywhere.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.worksheet import Worksheet

SYSTEM = PatternFill("solid", fgColor="E7ECF3")
INPUT = PatternFill("solid", fgColor="FFF2CC")
CHECK = PatternFill("solid", fgColor="E2F0D9")
HEADER = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14)
BOLD = Font(bold=True)
MUTED = Font(color="666666", italic=True)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

DATE = "dd.mm.yyyy"
PCT4 = "0.0000"
NUM2 = "#,##0.00"
NUM6 = "0.000000"
DF9 = "0.000000000"
BP = "0.00"
TINY = "0.00E+00"
AUTHOR = "CDS"

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def col(n: int) -> str:
    return get_column_letter(n)


def ref(ws: Worksheet, column: int, row: int, *, absolute: bool = True) -> str:
    c = f"${col(column)}${row}" if absolute else f"{col(column)}{row}"
    return f"'{ws.title}'!{c}"


def rng(ws: Worksheet, column: int, first: int, last: int) -> str:
    return f"'{ws.title}'!${col(column)}${first}:${col(column)}${last}"


def note(cell, text: str, width: int = 380, height: int | None = None) -> None:
    lines = sum(max(1, len(part) // 55 + 1) for part in text.split("\n"))
    c = Comment(text, AUTHOR)
    c.width = width
    c.height = height or min(40 + 15 * lines, 520)
    cell.comment = c


def name(wb: Workbook, title: str, target: str) -> None:
    """Workbook-level defined name, e.g. name(wb, "AS_OF", "'Кривая'!$C$5")."""
    wb.defined_names[title] = DefinedName(title, attr_text=target)


def put(ws: Worksheet, row: int, column: int, value: Any, *, fmt: str | None = None, fill: PatternFill | None = None,
        bold: bool = False, comment: str | None = None, font: Font | None = None):
    cell = ws.cell(row=row, column=column, value=value)
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    if bold:
        cell.font = BOLD
    if font:
        cell.font = font
    if comment:
        note(cell, comment)
    return cell


def title(ws: Worksheet, text: str, subtitle: str = "") -> None:
    put(ws, 1, 1, text, font=TITLE_FONT)
    if subtitle:
        put(ws, 2, 1, subtitle, font=MUTED)
    legend = put(ws, 3, 1, "Легенда:", bold=True)
    note(legend, "Цвета ячеек: серые — из системы, белые — формулы Excel, зелёные — сверка с системой, жёлтые — можно "
                 "менять. Ячейки с красным уголком содержат пояснения — наведите курсор.", 300)
    put(ws, 3, 2, "из системы", fill=SYSTEM)
    put(ws, 3, 3, "формула Excel")
    put(ws, 3, 4, "сверка (≈ 0)", fill=CHECK)
    put(ws, 3, 5, "можно менять", fill=INPUT)
    note(ws.cell(row=3, column=2), "Серые ячейки — значения, взятые из системы: исходные данные и результаты расчёта.")
    note(ws.cell(row=3, column=3), "Белые ячейки — формулы Excel, которые заново считают результат системы из исходных данных.")
    note(ws.cell(row=3, column=4), "Зелёные ячейки — сверка: результат Excel минус результат системы. Должно быть ≈ 0 "
                                    "(расхождения порядка 1e-10 — машинная точность).")
    note(ws.cell(row=3, column=5), "Жёлтые ячейки можно менять: например, дату в калькуляторе интерполяции.")


def kv(ws: Worksheet, row: int, label: str, value: Any, *, column: int = 1, fmt: str | None = None, comment: str | None = None,
       fill: PatternFill | None = SYSTEM):
    put(ws, row, column, label)
    return put(ws, row, column + 1, value, fmt=fmt, fill=fill, comment=comment)


def header(ws: Worksheet, row: int, columns: list[tuple[str, int, str | None]], first_col: int = 1) -> None:
    """columns: (caption, width, comment)."""
    for i, (caption, width, comment) in enumerate(columns):
        c = ws.cell(row=row, column=first_col + i, value=caption)
        c.fill, c.font, c.alignment = HEADER, HEADER_FONT, CENTER
        if comment:
            note(c, comment)
        if width:
            letter = col(first_col + i)
            ws.column_dimensions[letter].width = max(ws.column_dimensions[letter].width or 0, width)
    ws.row_dimensions[row].height = 45


def check_format(ws: Worksheet, area: str, tolerance: float) -> None:
    """Green when |value| <= tolerance, red otherwise (conditional formatting)."""
    first = area.split(":")[0].replace("$", "")
    ws.conditional_formatting.add(area, FormulaRule(formula=[f"ABS({first})<={tolerance}"], fill=CHECK))
    ws.conditional_formatting.add(area, FormulaRule(formula=[f"ABS({first})>{tolerance}"],
                                                    fill=PatternFill("solid", fgColor="F8CBAD")))


def loglin(t_cell: str, k_cell: str, times: str, logs: str) -> str:
    """ln DF at time t by log-linear interpolation (k = segment index from MATCH)."""
    return (f"INDEX({logs},{k_cell})+({t_cell}-INDEX({times},{k_cell}))"
            f"*(INDEX({logs},{k_cell}+1)-INDEX({logs},{k_cell}))/(INDEX({times},{k_cell}+1)-INDEX({times},{k_cell}))")


def segment(t_cell: str, times: str) -> str:
    """Segment index for log-linear interpolation; beyond the last node the last segment continues."""
    return f"MIN(MATCH({t_cell},{times},1),COUNT({times})-1)"


def to_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def save(wb: Workbook) -> bytes:
    wb.calculation.fullCalcOnLoad = True  # Excel recalculates every formula on open
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
