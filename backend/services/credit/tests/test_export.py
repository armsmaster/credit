"""The Excel calculation detail is built from a calculation and references only defined names."""
import io
import re
from datetime import timedelta

import numpy as np
import openpyxl

from credit_service import export_xlsx
from credit_service.domain.bonds import BondKind, RateIndex
from credit_service.domain.calibration import BondFitInput, CreditParams, calibrate
from credit_service.domain.cashflows import project
from credit_service.domain.pricing import BondPricer, HazardGrid
from credit_service.domain.rates import RateCurve

from .test_pricing import D, S, coupon_bond, zero_bond


def _ref(code: str, curve: RateCurve) -> dict:
    nodes = [{"date": (curve.as_of + timedelta(days=int(round(t * 365)))).isoformat(), "df": float(np.exp(ln))}
             for t, ln in zip(curve.times, curve.log_dfs)]
    return {"code": code, "as_of_date": curve.as_of.isoformat(), "kind": "state", "version": None, "day_count": "ACT/365F", "nodes": nodes}


def _snapshot() -> dict:
    ruonia = RateCurve.from_snapshot("RUONIA", D, "ACT/365F", [{"date": (D + timedelta(days=365 * k)).isoformat(), "df": float(np.exp(-0.14 * k))} for k in range(11)])
    curves = {RateIndex.RUONIA: ruonia}
    fixed = coupon_bond(3.0, 14.0)
    floater = coupon_bond(2.0, None, months=3)  # type: ignore[arg-type]
    floater.secid, floater.kind, floater.coupon_index, floater.spread_pct = "F2", BondKind.FLOATING, RateIndex.RUONIA, 1.3
    linker = zero_bond(2.5, kind=BondKind.LINKER, notional_index=RateIndex.RUONIA)
    grid = HazardGrid.monthly(D)
    params = CreditParams()
    bonds, fits = [], []
    for p, kind in ((fixed, "fixed"), (floater, "floating"), (linker, "linker")):
        proj = project(p, curve_date=D, settle_date=S, face_settle=1000.0, curves=curves)
        pricer = BondPricer(proj, grid, ruonia, params.recovery_rate, 5.0)
        price = pricer.clean_pct(np.full(120, 0.02))
        fits.append(BondFitInput(p.secid, p.secid, pricer, {"dema": price, "bid": price - 0.1, "ask": price + 0.1}, info={"kind": kind}))
        bonds.append({"secid": p.secid, "shortname": p.secid, "weight": 1.0, "enabled": True, "status": "used", "kind": kind,
                      "coupon_index": "RUONIA" if kind == "floating" else None, "spread_pct": p.spread_pct, "lag_days": 0,
                      "market": {"settle_date": S.isoformat(), "face": 1000.0, "accrued": 5.0},
                      "cashflows": [f.to_dict() for f in proj.flows]})
    out = calibrate(D, fits, params)
    ref = _ref("RUB-RUONIA-OIS", ruonia)
    return {"curve_code": "TEST", "curve_name": "test", "kind": "calculation", "as_of_date": D, "bonds": bonds,
            "inputs": {"discount": ref, "projection": {"RUONIA": ref}, "recovery_rate": params.recovery_rate},
            "result": {"nodes": out.nodes, "fitted": out.fitted, "stats": out.stats, "params": out.params}}


def test_workbook_has_the_sheets_and_formulas_use_defined_names():
    snap = _snapshot()
    assert export_xlsx.is_complete(snap)
    wb = openpyxl.load_workbook(io.BytesIO(export_xlsx.build(snap)))
    assert wb.sheetnames == ["Кривая", "Облигации", "Ставки", "Потоки", "Дефолт"]
    names = set(wb.defined_names)
    assert {"AS_OF", "REC", "HZ_T", "HZ_L", "HZ_H", "DISC_T", "DISC_LN", "DISC_LN0", "RUONIA_T", "F_PV", "G_PV", "B_SECID"} <= names
    used = set()
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    used |= set(re.findall(r"\b([A-Z][A-Z0-9]*_[A-Z0-9_]+|AS_OF|REC|W2)\b", c.value))
    assert used <= names, used - names
    flows = wb["Потоки"]
    sources = {flows.cell(r, 5).value for r in range(6, flows.max_row + 1)}
    assert any("форвард индекса" in (s or "") for s in sources) and any("индекс. номинал" in (s or "") for s in sources)
    assert wb["Облигации"]["S9"].value.startswith("=(Q9+R9)/(M9*O9)")


def test_old_snapshot_without_detail_is_detected():
    snap = _snapshot()
    for b in snap["bonds"]:
        b["cashflows"] = [{k: v for k, v in f.items() if k != "source"} for f in b["cashflows"]]
    assert not export_xlsx.is_complete(snap)
