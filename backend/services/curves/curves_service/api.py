from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from cds_common.auth import Principal, require_operator, require_viewer
from cds_common.db import get_session
from cds_common.tasks import Trigger, enqueue
from cds_common.xlsx import XLSX_MEDIA_TYPE

from . import export_xlsx
from .application import CurvesApp, effective_params, with_linear_par
from .domain.calibration import CalibrationParams
from .domain.curve import Compounding
from .domain.quotes import InputMode
from .models import CurveDefinition, CurveHistory, CurveState
from .schemas import (
    CalibrateRequest,
    CalibrationOut,
    CompareCurveOut,
    CurrencyIn,
    CurrencyOut,
    CurrencyUpdate,
    DefinitionBase,
    DefinitionIn,
    DefinitionOut,
    EvaluateOut,
    EvaluateRequest,
    HistoryDateOut,
    SaveHistoryRequest,
    SaveStateRequest,
    SnapshotOut,
    StateInfo,
    TaskRunOut,
    VersionOut,
)

router = APIRouter()


def get_app(request: Request, session: Session = Depends(get_session)) -> CurvesApp:
    return CurvesApp(session, request.app.state.settings)


def _definition_out(defn: CurveDefinition, state: CurveState | None, last_hist: date | None) -> DefinitionOut:
    return DefinitionOut(
        id=str(defn.id),
        code=defn.code,
        name=defn.name,
        currency_code=defn.currency_code,
        index_name=defn.index_name,
        description=defn.description,
        day_count=defn.day_count,
        fixed_frequency_months=defn.fixed_frequency_months,
        single_payment_max_months=defn.single_payment_max_months,
        default_params=defn.default_params,
        is_active=defn.is_active,
        created_at=defn.created_at,
        updated_at=defn.updated_at,
        effective_params=effective_params(defn).to_dict(),
        current_state=StateInfo(as_of_date=state.as_of_date, saved_by=state.saved_by, saved_at=state.saved_at) if state else None,
        last_history_date=last_hist,
    )


def _snapshot_out(code: str, row: CurveState | CurveHistory) -> SnapshotOut:
    is_state = isinstance(row, CurveState)
    return SnapshotOut(
        curve_code=code,
        kind="state" if is_state else "history",
        as_of_date=row.as_of_date,
        version=None if is_state else row.version,
        source=None if is_state else row.source,
        input_mode=row.input_mode,
        quotes=row.quotes,
        params=row.params,
        conventions=row.conventions,
        result=with_linear_par(row.result),
        comment=row.comment,
        author=row.saved_by if is_state else row.created_by,
        saved_at=row.saved_at if is_state else row.created_at,
    )


# ---------------------------------------------------------------- meta
@router.get("/meta", tags=["meta"])
def meta(request: Request, _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    return {
        "today": request.app.state.settings.today(),
        "input_modes": [m.value for m in InputMode],
        "compoundings": [c.value for c in Compounding],
        "day_counts": ["ACT/365F", "ACT/360"],
        "default_params": CalibrationParams().to_dict(),
    }


# ---------------------------------------------------------------- currencies
@router.get("/currencies", response_model=list[CurrencyOut], tags=["currencies"])
def list_currencies(app: CurvesApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    return app.list_currencies()


@router.post("/currencies", response_model=CurrencyOut, status_code=201, tags=["currencies"])
def create_currency(data: CurrencyIn, app: CurvesApp = Depends(get_app), user: Principal = Depends(require_operator)):
    return app.create_currency(data, user)


@router.put("/currencies/{code}", response_model=CurrencyOut, tags=["currencies"])
def update_currency(
    code: str, data: CurrencyUpdate, app: CurvesApp = Depends(get_app), user: Principal = Depends(require_operator)
):
    return app.update_currency(code, data, user)


# ---------------------------------------------------------------- definitions
@router.get("/definitions", response_model=list[DefinitionOut], tags=["definitions"])
def list_definitions(app: CurvesApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    states, last = app.state_infos(), app.last_history_dates()
    return [_definition_out(d, states.get(d.id), last.get(d.id)) for d in app.list_definitions()]


@router.get("/definitions/{code}", response_model=DefinitionOut, tags=["definitions"])
def get_definition(code: str, app: CurvesApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    d = app.get_definition(code)
    return _definition_out(d, app.state_infos().get(d.id), app.last_history_dates().get(d.id))


@router.post("/definitions", response_model=DefinitionOut, status_code=201, tags=["definitions"])
def create_definition(data: DefinitionIn, app: CurvesApp = Depends(get_app), user: Principal = Depends(require_operator)):
    return _definition_out(app.create_definition(data, user), None, None)


@router.put("/definitions/{code}", response_model=DefinitionOut, tags=["definitions"])
def update_definition(
    code: str, data: DefinitionBase, app: CurvesApp = Depends(get_app), user: Principal = Depends(require_operator)
):
    d = app.update_definition(code, data, user)
    return _definition_out(d, app.state_infos().get(d.id), app.last_history_dates().get(d.id))


# ---------------------------------------------------------------- calculator
@router.post("/calibrate", response_model=CalibrationOut, tags=["calculator"])
def calibrate(req: CalibrateRequest, app: CurvesApp = Depends(get_app), _: Principal = Depends(require_operator)):
    """Stateless: build the curve from quotes without saving it."""
    result, _quotes, _params = app.calibrate(app.get_definition(req.curve_code), req.as_of_date, req)
    return result


@router.post("/evaluate", response_model=EvaluateOut, tags=["calculator"])
def evaluate(req: EvaluateRequest, _: Principal = Depends(require_viewer)):
    """Discount factor, zero and forward rates for a future date (log-linear DF interpolation)."""
    return CurvesApp.evaluate(req)


# ---------------------------------------------------------------- current state
@router.get("/curves/{code}/state", response_model=SnapshotOut, tags=["state"])
def get_state(code: str, app: CurvesApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    defn, state = app.get_state(code)
    return _snapshot_out(defn.code, state)


@router.put("/curves/{code}/state", response_model=SnapshotOut, tags=["state"])
def save_state(
    code: str, req: SaveStateRequest, app: CurvesApp = Depends(get_app), user: Principal = Depends(require_operator)
):
    return _snapshot_out(code, app.save_state(code, req.as_of_date, req, req.comment, user))


@router.post("/curves/{code}/archive", response_model=TaskRunOut, status_code=202, tags=["state"])
def archive_now(code: str, app: CurvesApp = Depends(get_app), user: Principal = Depends(require_operator)):
    """Move the current state into history now (background task)."""
    _, state = app.get_state(code)
    task_id = enqueue(
        app.s,
        "curves.archive_states",
        {"curve_code": code, "up_to_date": state.as_of_date.isoformat()},
        trigger=Trigger.USER,
        created_by=user.username,
    )
    app.s.commit()
    return TaskRunOut(task_id=str(task_id))


# ---------------------------------------------------------------- history
@router.get("/curves/{code}/history", response_model=list[HistoryDateOut], tags=["history"])
def list_history(
    code: str,
    date_from: date | None = None,
    date_to: date | None = None,
    app: CurvesApp = Depends(get_app),
    _: Principal = Depends(require_viewer),
):
    return app.list_history(code, date_from, date_to)


@router.get("/curves/{code}/history/{as_of}/versions", response_model=list[VersionOut], tags=["history"])
def list_versions(code: str, as_of: date, app: CurvesApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    return [
        VersionOut(version=v.version, source=v.source, author=v.created_by, saved_at=v.created_at, comment=v.comment)
        for v in app.list_versions(code, as_of)
    ]


@router.get("/curves/{code}/history/{as_of}", response_model=SnapshotOut, tags=["history"])
def get_history(
    code: str,
    as_of: date,
    version: int | None = None,
    app: CurvesApp = Depends(get_app),
    _: Principal = Depends(require_viewer),
):
    defn, row = app.get_history(code, as_of, version)
    return _snapshot_out(defn.code, row)


@router.post("/curves/{code}/history/{as_of}", response_model=SnapshotOut, status_code=201, tags=["history"])
def save_history(
    code: str,
    as_of: date,
    req: SaveHistoryRequest,
    app: CurvesApp = Depends(get_app),
    user: Principal = Depends(require_operator),
):
    """Backdated change: stores a new version and tags the journal entry 'backdated'."""
    return _snapshot_out(code, app.save_history(code, as_of, req, req.comment, user))


@router.get("/curves/{code}/effective", response_model=SnapshotOut, tags=["history"])
def effective(code: str, date: date, app: CurvesApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    """Curve in force on ``date``: the current state or the latest history version on or before it
    (used by other services, e.g. credit curves take their discount curve from here)."""
    defn, row = app.effective(code, date)
    return _snapshot_out(defn.code, row)


@router.get("/curves/{code}/compare", response_model=list[CompareCurveOut], tags=["history"])
def compare(code: str, as_of: date, app: CurvesApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    """The same curve one day / week / month before ``as_of`` (latest version on or before the date)."""
    return app.compare(code, as_of)




# ---------------------------------------------------------------- Excel: calculation detail
def _xlsx(content: bytes, filename: str) -> Response:
    return Response(content, media_type=XLSX_MEDIA_TYPE, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _export_snapshot(defn: CurveDefinition, row: CurveState | CurveHistory) -> dict[str, Any]:
    is_state = isinstance(row, CurveState)
    return {
        "curve_code": defn.code,
        "curve_name": defn.name,
        "kind": "state" if is_state else "history",
        "version": None if is_state else row.version,
        "author": row.saved_by if is_state else row.created_by,
        "saved_at": row.saved_at if is_state else row.created_at,
        "as_of_date": row.as_of_date,
        "conventions": row.conventions,
        "result": with_linear_par(row.result),
    }


@router.get("/curves/{code}/state/export.xlsx", tags=["export"])
def export_state(code: str, app: CurvesApp = Depends(get_app), _: Principal = Depends(require_viewer)):
    """Excel with the calculation detail of the current curve: nodes, interpolation, swap schedules, par rates."""
    defn, state = app.get_state(code)
    return _xlsx(export_xlsx.build(_export_snapshot(defn, state)), f"{defn.code}_{state.as_of_date}.xlsx")


@router.get("/curves/{code}/history/{as_of}/export.xlsx", tags=["export"])
def export_history(code: str, as_of: date, version: int | None = None, app: CurvesApp = Depends(get_app),
                   _: Principal = Depends(require_viewer)):
    defn, row = app.get_history(code, as_of, version)
    return _xlsx(export_xlsx.build(_export_snapshot(defn, row)), f"{defn.code}_{as_of}_v{row.version}.xlsx")


@router.post("/calibrate/export.xlsx", tags=["export"])
def export_calculation(req: CalibrateRequest, app: CurvesApp = Depends(get_app), _: Principal = Depends(require_operator)):
    """Excel for a calculation in the editor (not saved yet): same inputs as POST /calibrate."""
    defn = app.get_definition(req.curve_code)
    result, _quotes, _params = app.calibrate(defn, req.as_of_date, req)
    snap = {"curve_code": defn.code, "curve_name": defn.name, "kind": "calculation", "as_of_date": req.as_of_date,
            "conventions": result["conventions"], "result": with_linear_par(result)}
    return _xlsx(export_xlsx.build(snap), f"{defn.code}_{req.as_of_date}_calc.xlsx")
