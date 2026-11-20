from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from cds_common.auth import Principal, require_operator, require_viewer
from cds_common.db import get_session
from cds_common.errors import AppError
from cds_common.tasks import Trigger, enqueue
from cds_common.xlsx import XLSX_MEDIA_TYPE

from . import export_xlsx
from .application import CreditApp, effective_params
from .catalog import CatalogApp
from .domain.bonds import KIND_TITLES, RateIndex
from .domain.calibration import OBS_TITLES, CreditParams
from .models import CurveDefinition, CurveHistory, CurveState
from .rates_client import RatesClient
from .schemas import (
    BondDetailOut,
    CalcBondIn,
    ParamsIn,
    BondManual,
    BondOut,
    CalculationOut,
    CalibrateRequest,
    CalcPayload,
    CompareCurveOut,
    DefinitionBase,
    DefinitionIn,
    DefinitionOut,
    HistoryDateOut,
    IssSearchOut,
    IssuerAdd,
    IssuerOut,
    SaveHistoryRequest,
    SaveStateRequest,
    SettingsIn,
    SnapshotOut,
    StateInfo,
    TaskRunOut,
    VersionOut,
)

router = APIRouter()


def get_catalog(request: Request, session: Session = Depends(get_session)) -> CatalogApp:
    return CatalogApp(session, request.app.state.settings, request.app.state.iss)


def get_app(request: Request, session: Session = Depends(get_session)) -> CreditApp:
    return CreditApp(session, request.app.state.settings, request.app.state.market)


def get_rates(request: Request) -> Iterator[RatesClient]:
    """Interest-rate curves are read from the curves service with the caller's own token."""
    auth = request.headers.get("authorization", "")
    client = RatesClient(request.app.state.settings.curves_api_url, auth.split(" ", 1)[-1])
    try:
        yield client
    finally:
        client.close()


def _definition_out(app: CreditApp, d: CurveDefinition, states=None, last=None, comps=None, titles=None) -> DefinitionOut:
    states = states if states is not None else app.state_infos()
    last = last if last is not None else app.last_history_dates()
    comps = comps if comps is not None else app.compositions()
    titles = titles if titles is not None else app.issuer_titles()
    st = states.get(d.id)
    return DefinitionOut(
        id=str(d.id), code=d.code, name=d.name, issuer_id=d.issuer_id, issuer_title=titles.get(d.issuer_id, str(d.issuer_id)),
        discount_curve_code=d.discount_curve_code, description=d.description, default_params=d.default_params,
        effective_params=effective_params(d).to_dict(), bonds=comps.get(d.id, []), is_active=d.is_active,
        created_at=d.created_at, updated_at=d.updated_at,
        current_state=StateInfo(as_of_date=st.as_of_date, saved_by=st.saved_by, saved_at=st.saved_at) if st else None,
        last_history_date=last.get(d.id),
    )


def _snapshot_out(code: str, row: CurveState | CurveHistory) -> SnapshotOut:
    is_state = isinstance(row, CurveState)
    return SnapshotOut(
        curve_code=code, kind="state" if is_state else "history", as_of_date=row.as_of_date,
        version=None if is_state else row.version, source=None if is_state else row.source,
        bonds=row.bonds, params=row.params, inputs=row.inputs, result=row.result, comment=row.comment,
        author=row.saved_by if is_state else row.created_by, saved_at=row.saved_at if is_state else row.created_at,
    )


# ---------------------------------------------------------------- meta and settings
@router.get("/meta", tags=["meta"])
def meta(request: Request, catalog: CatalogApp = Depends(get_catalog), _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    return {
        "today": request.app.state.settings.today(),
        "default_params": CreditParams().to_dict(),
        "kinds": {k.value: v for k, v in KIND_TITLES.items()},
        "indices": [i.value for i in RateIndex],
        "observation_kinds": OBS_TITLES,
        "settings": catalog.get_settings(),
    }


@router.get("/settings", tags=["settings"])
def get_settings(catalog: CatalogApp = Depends(get_catalog), _: Principal = Depends(require_viewer)):
    return catalog.get_settings()


@router.put("/settings", tags=["settings"])
def update_settings(data: SettingsIn, catalog: CatalogApp = Depends(get_catalog), user: Principal = Depends(require_operator)):
    return catalog.update_settings(data, user)


# ---------------------------------------------------------------- issuers
@router.get("/iss/emitters", response_model=list[IssSearchOut], tags=["issuers"])
def search_iss(q: str, catalog: CatalogApp = Depends(get_catalog), _: Principal = Depends(require_viewer)):
    """Search issuers in the ISS directory (iss.moex.com)."""
    return catalog.search_iss(q)


@router.get("/issuers", response_model=list[IssuerOut], tags=["issuers"])
def list_issuers(catalog: CatalogApp = Depends(get_catalog), _: Principal = Depends(require_viewer)):
    return catalog.list_issuers()


@router.post("/issuers", status_code=201, tags=["issuers"])
def add_issuer(data: IssuerAdd, catalog: CatalogApp = Depends(get_catalog), user: Principal = Depends(require_operator)):
    """Track an issuer; the first scan of its bonds is queued immediately."""
    issuer, task_id = catalog.add_issuer(data.emitter_id, user)
    return {"emitter_id": issuer.emitter_id, "task_id": task_id}


@router.post("/issuers/{emitter_id}/scan", response_model=TaskRunOut, status_code=202, tags=["issuers"])
def scan_issuer(emitter_id: int, catalog: CatalogApp = Depends(get_catalog), user: Principal = Depends(require_operator)):
    return TaskRunOut(task_id=catalog.rescan(emitter_id, user))


@router.delete("/issuers/{emitter_id}", status_code=204, tags=["issuers"])
def delete_issuer(emitter_id: int, catalog: CatalogApp = Depends(get_catalog), user: Principal = Depends(require_operator)):
    catalog.delete_issuer(emitter_id, user)
    return Response(status_code=204)


# ---------------------------------------------------------------- bonds
@router.get("/bonds", response_model=list[BondOut], tags=["bonds"])
def list_bonds(
    issuer_id: int | None = None,
    short_years: float = 0.5,
    include_untraded: bool = False,
    catalog: CatalogApp = Depends(get_catalog),
    _: Principal = Depends(require_viewer),
):
    """Bonds of tracked issuers with classification, completeness, liquidity and recommendation."""
    return catalog.list_bonds(issuer_id, short_years, include_untraded)


@router.get("/bonds/{secid}", response_model=BondDetailOut, tags=["bonds"])
def get_bond(secid: str, catalog: CatalogApp = Depends(get_catalog), _: Principal = Depends(require_viewer)):
    return catalog.bond_detail(secid)


@router.put("/bonds/{secid}/manual", response_model=BondDetailOut, tags=["bonds"])
def update_bond_manual(secid: str, data: BondManual, catalog: CatalogApp = Depends(get_catalog), user: Principal = Depends(require_operator)):
    catalog.update_manual(secid, data, user)
    return catalog.bond_detail(secid)


@router.get("/bonds/{secid}/dema", tags=["bonds"])
def bond_dema(secid: str, request: Request, _: Principal = Depends(require_viewer)):
    """Hourly closes and DEMA (lookback window) for the chart."""
    return request.app.state.market.dema_series(secid)


# ---------------------------------------------------------------- curve definitions
@router.get("/definitions", response_model=list[DefinitionOut], tags=["definitions"])
def list_definitions(app: CreditApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    states, last, comps, titles = app.state_infos(), app.last_history_dates(), app.compositions(), app.issuer_titles()
    return [_definition_out(app, d, states, last, comps, titles) for d in app.list_definitions()]


@router.get("/definitions/{code}", response_model=DefinitionOut, tags=["definitions"])
def get_definition(code: str, app: CreditApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    return _definition_out(app, app.get_definition(code))


@router.post("/definitions", response_model=DefinitionOut, status_code=201, tags=["definitions"])
def create_definition(data: DefinitionIn, app: CreditApp = Depends(get_app), user: Principal = Depends(require_operator)):
    return _definition_out(app, app.create_definition(data, user))


@router.put("/definitions/{code}", response_model=DefinitionOut, tags=["definitions"])
def update_definition(code: str, data: DefinitionBase, app: CreditApp = Depends(get_app), user: Principal = Depends(require_operator)):
    return _definition_out(app, app.update_definition(code, data, user))


# ---------------------------------------------------------------- market inputs and calculation
@router.get("/curves/{code}/market", tags=["calculator"])
def market_inputs(code: str, as_of: date, app: CreditApp = Depends(get_app), _: Principal = Depends(require_viewer)) -> list[dict[str, Any]]:
    """Composition of the curve with market observations of the date (latest intraday for today, 19:00 for past dates)."""
    return app.bond_inputs(app.get_definition(code), as_of, None)


@router.post("/curves/{code}/market/refresh", tags=["calculator"])
def refresh_market(code: str, request: Request, app: CreditApp = Depends(get_app), _: Principal = Depends(require_operator)) -> dict[str, Any]:
    """Fetch fresh prices of the curve's bonds from the exchange now (normally every 15 minutes)."""
    defn = app.get_definition(code)
    secids = [b.secid for b in app.composition(defn)]
    if not secids:
        raise AppError("В составе кривой нет бумаг", code="empty_curve")
    return request.app.state.market.refresh(secids)


@router.post("/calibrate", response_model=CalculationOut, tags=["calculator"])
def calibrate(req: CalibrateRequest, app: CreditApp = Depends(get_app), rates: RatesClient = Depends(get_rates),
              _: Principal = Depends(require_operator)):
    """Bootstrap the curve from current market data without saving it."""
    return app.calculate(app.get_definition(req.curve_code), req.as_of_date, req, rates)


# ---------------------------------------------------------------- current state
@router.get("/curves/{code}/state", response_model=SnapshotOut, tags=["state"])
def get_state(code: str, app: CreditApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    defn, state = app.get_state(code)
    return _snapshot_out(defn.code, state)


@router.put("/curves/{code}/state", response_model=SnapshotOut, tags=["state"])
def save_state(code: str, req: SaveStateRequest, app: CreditApp = Depends(get_app), rates: RatesClient = Depends(get_rates),
               user: Principal = Depends(require_operator)):
    return _snapshot_out(code, app.save_state(code, req.as_of_date, req, req.comment, user, rates))


@router.post("/curves/{code}/archive", response_model=TaskRunOut, status_code=202, tags=["state"])
def archive_now(code: str, app: CreditApp = Depends(get_app), user: Principal = Depends(require_operator)):
    _, state = app.get_state(code)
    task_id = enqueue(app.s, "credit.archive_states", {"curve_code": code, "up_to_date": state.as_of_date.isoformat()},
                      trigger=Trigger.USER, created_by=user.username)
    app.s.commit()
    return TaskRunOut(task_id=str(task_id))


# ---------------------------------------------------------------- history
@router.get("/curves/{code}/history", response_model=list[HistoryDateOut], tags=["history"])
def list_history(code: str, date_from: date | None = None, date_to: date | None = None, app: CreditApp = Depends(get_app),
                 _: Principal = Depends(require_viewer)):
    return app.list_history(code, date_from, date_to)


@router.get("/curves/{code}/history/{as_of}/versions", response_model=list[VersionOut], tags=["history"])
def list_versions(code: str, as_of: date, app: CreditApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    return [VersionOut(version=v.version, source=v.source, author=v.created_by, saved_at=v.created_at, comment=v.comment)
            for v in app.list_versions(code, as_of)]


@router.get("/curves/{code}/history/{as_of}", response_model=SnapshotOut, tags=["history"])
def get_history(code: str, as_of: date, version: int | None = None, app: CreditApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    defn, row = app.get_history(code, as_of, version)
    return _snapshot_out(defn.code, row)


@router.post("/curves/{code}/history/{as_of}", response_model=SnapshotOut, status_code=201, tags=["history"])
def save_history(code: str, as_of: date, req: SaveHistoryRequest, app: CreditApp = Depends(get_app),
                 rates: RatesClient = Depends(get_rates), user: Principal = Depends(require_operator)):
    """Backdated change: a new version from the 19:00 market data of that date, journaled as 'backdated'."""
    return _snapshot_out(code, app.save_history(code, as_of, req, req.comment, user, rates))


@router.get("/curves/{code}/compare", response_model=list[CompareCurveOut], tags=["history"])
def compare(code: str, as_of: date, app: CreditApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    return app.compare(code, as_of)


# ---------------------------------------------------------------- Excel: calculation detail
def _xlsx(content: bytes, filename: str) -> Response:
    return Response(content, media_type=XLSX_MEDIA_TYPE, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _export_snapshot(app: CreditApp, defn: CurveDefinition, row: CurveState | CurveHistory, rates: RatesClient) -> dict[str, Any]:
    is_state = isinstance(row, CurveState)
    snap = {
        "curve_code": defn.code, "curve_name": defn.name, "issuer_title": app.issuer_titles().get(defn.issuer_id, ""),
        "kind": "state" if is_state else "history", "version": None if is_state else row.version,
        "author": row.saved_by if is_state else row.created_by, "saved_at": row.saved_at if is_state else row.created_at,
        "as_of_date": row.as_of_date, "bonds": row.bonds, "inputs": row.inputs, "result": row.result,
    }
    if export_xlsx.is_complete(snap):
        return snap
    # Saved before the calculation detail was stored: recompute with the saved composition and parameters
    payload = CalcPayload(
        bonds=[CalcBondIn(secid=b["secid"], weight=b.get("weight") or 1.0, enabled=b.get("enabled", True),
                          manual_price=b.get("manual_price"), excluded_obs=b.get("excluded_obs") or []) for b in row.bonds],
        params=ParamsIn(**{k: v for k, v in (row.params or {}).items() if k in ParamsIn.model_fields}),
    )
    calc = app.calculate(defn, row.as_of_date, payload, rates)
    if calc["result"] is None:
        raise AppError("Кривую не удалось пересчитать для детализации: " + "; ".join(calc["inputs"]["warnings"][-3:]),
                       code="export_failed")
    snap.update(kind="recalculated", bonds=calc["bonds"], inputs=calc["inputs"], result=calc["result"])
    return snap


@router.get("/curves/{code}/state/export.xlsx", tags=["export"])
def export_state(code: str, app: CreditApp = Depends(get_app), rates: RatesClient = Depends(get_rates),
                 _: Principal = Depends(require_viewer)):
    """Excel with the calculation detail of the current credit curve: survival, cash flows, recovery, model prices."""
    defn, state = app.get_state(code)
    return _xlsx(export_xlsx.build(_export_snapshot(app, defn, state, rates)), f"{defn.code}_{state.as_of_date}.xlsx")


@router.get("/curves/{code}/history/{as_of}/export.xlsx", tags=["export"])
def export_history(code: str, as_of: date, version: int | None = None, app: CreditApp = Depends(get_app),
                   rates: RatesClient = Depends(get_rates), _: Principal = Depends(require_viewer)):
    defn, row = app.get_history(code, as_of, version)
    return _xlsx(export_xlsx.build(_export_snapshot(app, defn, row, rates)), f"{defn.code}_{as_of}_v{row.version}.xlsx")


@router.post("/calibrate/export.xlsx", tags=["export"])
def export_calculation(req: CalibrateRequest, app: CreditApp = Depends(get_app), rates: RatesClient = Depends(get_rates),
                       _: Principal = Depends(require_operator)):
    """Excel for a calculation in the online editor (not saved yet): same inputs as POST /calibrate."""
    defn = app.get_definition(req.curve_code)
    calc = app.calculate(defn, req.as_of_date, req, rates)
    if calc["result"] is None:
        raise AppError("Кривая не построена: " + "; ".join(calc["inputs"]["warnings"][-3:]), code="calibration_failed")
    snap = {"curve_code": defn.code, "curve_name": defn.name, "issuer_title": app.issuer_titles().get(defn.issuer_id, ""),
            "kind": "calculation", "as_of_date": req.as_of_date, "bonds": calc["bonds"], "inputs": calc["inputs"],
            "result": calc["result"]}
    return _xlsx(export_xlsx.build(snap), f"{defn.code}_{req.as_of_date}_calc.xlsx")
