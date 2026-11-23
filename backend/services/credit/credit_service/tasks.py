"""Background tasks of the credit service (executed by credit_service.worker).

Default schedules (business timezone, editable by admins):
  credit.scan_issuers    06:15 Mon-Fri     bonds of tracked issuers, static data, history, liquidity
  credit.refresh_prices  every 15 min 09:00-19:45 Mon-Fri   snapshots, candles, DEMA
  credit.close_day       19:50 Mon-Fri     values at 19:00 into the daily history
  credit.archive_states  00:35 every night current credit curves -> history
"""
from __future__ import annotations

from datetime import date, timedelta

from cds_common.tasks import TaskContext, TaskRegistry

from .application import CreditApp
from .cds.application import CdsApp
from .config import get_settings
from .iss import IssClient
from .market import MarketData
from .scanner import Scanner

registry = TaskRegistry(queue="credit")


def _iss() -> IssClient:
    s = get_settings()
    return IssClient(s.iss_base_url, requests_per_second=s.iss_requests_per_second, timeout=s.iss_timeout_seconds,
                     verify=s.iss_ca_bundle or True)


@registry.task(
    "credit.scan_issuers",
    title="Сканирование облигаций эмитентов (ISS)",
    description=(
        "Обновляет список облигаций отслеживаемых эмитентов, их статику, график купонов/амортизаций/оферт, "
        "историю торгов и метрики ликвидности. Параметр issuer_id — только один эмитент "
        "(так запускается первичный скан при добавлении эмитента)."
    ),
    default_schedule="15 6 * * 1-5",
)
def scan_issuers(ctx: TaskContext) -> dict:
    iss = _iss()
    try:
        raw = ctx.params.get("issuer_id")
        return Scanner(ctx.session_factory, get_settings(), iss).scan(int(raw) if raw else None)
    finally:
        iss.close()


@registry.task(
    "credit.refresh_prices",
    title="Обновление цен облигаций (каждые 15 минут)",
    description=(
        "Снимок стакана, последней сделки и VWAP по облигациям отслеживаемых эмитентов; "
        "часовые свечи и DEMA по бумагам, входящим в кредитные кривые. Данные ISS — с задержкой 15 минут."
    ),
    default_schedule="*/15 9-19 * * 1-5",
)
def refresh_prices(ctx: TaskContext) -> dict:
    s = get_settings()
    iss = _iss()
    try:
        return MarketData(ctx.session_factory, s, iss, s.iss_delay_minutes).refresh()
    finally:
        iss.close()


@registry.task(
    "credit.close_day",
    title="Итоги дня по облигациям (19:00)",
    description=(
        "Фиксирует значения на закрытие основной сессии (19:00 мск): BID, ASK, LAST, VWAP, DEMA, номинал, НКД; "
        "считает медианный спред дня; удаляет внутридневные снимки прошлых дней. Параметр trade_date — дата."
    ),
    default_schedule="50 19 * * 1-5",
)
def close_day(ctx: TaskContext) -> dict:
    s = get_settings()
    raw = ctx.params.get("trade_date")
    return MarketData(ctx.session_factory, s, None, s.iss_delay_minutes).close_day(date.fromisoformat(raw) if raw else None)


@registry.task(
    "credit.archive_states",
    title="Перенос текущих кредитных кривых в историю",
    description=(
        "Копирует текущие состояния кредитных кривых в историю (версия 1). Параметры: up_to_date — "
        "переносить состояния не позже даты (по умолчанию вчера); curve_code — одна кривая."
    ),
    default_schedule="35 0 * * *",
)
def archive_states(ctx: TaskContext) -> dict:
    settings = get_settings()
    raw = ctx.params.get("up_to_date")
    up_to = date.fromisoformat(raw) if raw else settings.today() - timedelta(days=1)
    with ctx.session_factory() as session:
        out = CreditApp(session, settings).archive_states(up_to, ctx.actor, ctx.params.get("curve_code"))
    if not ctx.params.get("curve_code"):
        with ctx.session_factory() as session:
            out.update(CdsApp(session, settings).archive_states(up_to, ctx.actor))
    return out


@registry.task(
    "credit.cds_recalculate",
    title="Пересчёт CDS-кривых после сохранения кредитной кривой",
    description=(
        "Ставится автоматически при сохранении кредитной кривой: пересчитывает CDS-кривые контрольных лиц, "
        "которые оцениваются по ней. Параметры: credit_curve, scope (state | history), as_of (для history)."
    ),
)
def cds_recalculate(ctx: TaskContext) -> dict:
    raw = ctx.params.get("as_of")
    with ctx.session_factory() as session:
        return CdsApp(session, get_settings()).recalc_for_credit_curve(
            ctx.params["credit_curve"], ctx.params.get("scope", "state"), date.fromisoformat(raw) if raw else None, ctx.actor)


@registry.task(
    "credit.cds_rates_changed",
    title="Пересчёт CDS-кривых после сохранения процентной кривой",
    description=(
        "Ставится сервисом процентных кривых при сохранении кривой (вместе с её узлами): CDS-кривые, кредитные кривые "
        "которых дисконтируются по ней, пересчитываются на новых дисконт-факторах."
    ),
)
def cds_rates_changed(ctx: TaskContext) -> dict:
    with ctx.session_factory() as session:
        return CdsApp(session, get_settings()).on_rates(ctx.params, ctx.actor)
