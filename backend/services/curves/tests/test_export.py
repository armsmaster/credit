"""The Excel calculation detail of an OIS curve: schedules of every swap, formulas on defined names."""
import io
from datetime import date

import openpyxl

from curves_service import export_xlsx
from curves_service.application import result_to_dict
from curves_service.domain.calibration import CalibrationParams, calibrate
from curves_service.domain.conventions import SwapConventions, Tenor
from curves_service.domain.quotes import InputMode, parse_quotes


def test_workbook_lists_the_payment_schedule_of_every_swap():
    as_of = date(2026, 11, 20)
    quotes = [{"tenor": t, "mid": m, "spread": 0.2} for t, m in [("1W", 13.9), ("6M", 13.0), ("1Y", 12.5), ("18M", 12.2), ("3Y", 11.8)]]
    conv = SwapConventions()
    res = result_to_dict(calibrate(as_of, parse_quotes(quotes, InputMode.MID_SPREAD), conv, CalibrationParams()), conv)
    wb = openpyxl.load_workbook(io.BytesIO(export_xlsx.build({"curve_code": "T", "kind": "calculation", "as_of_date": as_of,
                                                              "conventions": res["conventions"], "result": res})))
    assert wb.sheetnames == ["Кривая", "Свопы"]
    assert {"AS_OF", "BASIS", "T_NODES", "LNDF"} <= set(wb.defined_names)
    ws = wb["Свопы"]
    schedule_rows = [r for r in range(1, ws.max_row + 1) if isinstance(ws.cell(r, 2).value, int) and isinstance(ws.cell(r, 10).value, str)]
    expected = sum(len(conv.payment_dates(as_of, Tenor.parse(q["tenor"]))) for q in quotes)
    assert len(schedule_rows) == expected  # 1W, 6M, 1Y: one payment; 18M: 2; 3Y: 3
    assert ws["L7"].value == "=IF(F7=1,(1/J7-1)/G7,(1-J7)/K7)*100"
