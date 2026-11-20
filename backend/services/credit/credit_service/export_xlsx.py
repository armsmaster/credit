"""Excel file with the calculation detail of a credit curve.

Five sheets with live formulas — how the curve on the screen prices the benchmark bonds:
* «Кривая»     — forward hazard rates (from the system) -> cumulative hazard, survival Q(t), PD,
                 spread; calculator Q(t) for any date; roughness of the curve;
* «Ставки»     — discount and index curves used (nodes from the curves service) with log-linear
                 interpolation of ln DF;
* «Облигации»  — per bond: settlement, notional, accrued, PV of promised cash flows and of the
                 recovery leg, model dirty and clean price (compared with the system), the price
                 cloud (DEMA, BID, ASK, LAST, VWAP) with priorities, errors in bp, RMSE;
* «Потоки»     — every promised cash flow: how the amount is built (fixed rate, ISS amount,
                 floating = index forward + margin, linker = notional indexed by RUONIA), DF, Q, PV;
* «Дефолт»     — the recovery leg: R · N(u_{j−1}) · DF(mid) · (Q(u_{j−1}) − Q(u_j)) on the grid.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import numpy as np
from dateutil.relativedelta import relativedelta
from openpyxl import Workbook

from cds_common import xlsx as X

from .domain.pricing import HazardGrid, default_grid_days

KIND_TITLES = {"fixed": "фикс. купон", "floating": "флоатер", "linker": "индекс. номинал"}
FLOW_TITLES = {"coupon": "купон", "amortization": "амортизация", "redemption": "погашение"}
SOURCE_TITLES = {
    "iss_amount": "сумма купона из ISS (зафиксирована)",
    "fixed_rate": "номинал × ставка × дни/365",
    "floating": "номинал × (форвард индекса + маржа) × дни/365",
    "linker_coupon": "индекс. номинал × ставка × дни/365",
    "iss_amortization": "амортизация по графику ISS",
    "redemption": "остаток номинала × цена погашения",
    "linker_redemption": "индекс. номинал × цена погашения",
}
OBS = ("dema", "bid", "ask", "last", "vwap", "manual")
OBS_TITLES = {"dema": "DEMA", "bid": "BID", "ask": "ASK", "last": "LAST", "vwap": "VWAP", "manual": "Ручная"}
SNAP_TITLES = {"state": "текущая кривая", "history": "историческая версия", "calculation": "несохранённый расчёт",
               "recalculated": "пересчёт сохранённой кривой"}
CURVE_ROLES = (("DISC", "discount", "Кривая дисконтирования"), ("RUONIA", "RUONIA", "Кривая RUONIA (флоатеры, индексация номинала)"),
               ("KEYRATE", "KEYRATE", "Кривая ключевой ставки (флоатеры)"))


def _q(t_cell: str, k_cell: str) -> str:
    """Survival probability at t: exp(−(H_k + λ_k·(t − t_k)))."""
    return f"EXP(-(INDEX(HZ_H,{k_cell})+INDEX(HZ_L,{k_cell})*({t_cell}-INDEX(HZ_T,{k_cell}))))"


def _ln(prefix: str, t_cell: str, k_cell: str) -> str:
    return X.loglin(t_cell, k_cell, f"{prefix}_T", f"{prefix}_LN")


class _Book:
    def __init__(self, snap: dict[str, Any]):
        self.snap = snap
        self.as_of = X.to_date(snap["as_of_date"])
        self.result = snap["result"]
        self.inputs = snap.get("inputs") or {}
        self.params = self.result.get("params", {})
        self.fitted = {f["secid"]: f for f in self.result.get("fitted", []) if f.get("used")}
        self.bonds = [b for b in snap.get("bonds", []) if b.get("secid") in self.fitted and b.get("cashflows")]
        self.wb = Workbook()
        self.curves: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ «Кривая»
    def curve_sheet(self) -> None:
        ws = self.wb.active
        ws.title = "Кривая"
        snap, R = self.snap, self.params.get("recovery_rate", self.inputs.get("recovery_rate", 0.4))
        source = SNAP_TITLES.get(snap.get("kind", ""), snap.get("kind", ""))
        if snap.get("version"):
            source += f" {snap['version']}"
        who = f", сохранил {snap['author']} {snap['saved_at']:%d.%m.%Y %H:%M}" if snap.get("author") and isinstance(snap.get("saved_at"), datetime) else ""
        X.title(ws, f"Кредитная кривая {snap['curve_code']} на {self.as_of:%d.%m.%Y}: детализация расчёта",
                f"{snap.get('curve_name', '')} · {snap.get('issuer_title', '')} · {source}{who} · выгружено {datetime.now(timezone.utc):%d.%m.%Y %H:%M} UTC")
        X.kv(ws, 5, "Дата кривой D", self.as_of, fmt=X.DATE, comment="Время для hazard rate: t = (дата − D) / 365.")
        X.name(self.wb, "AS_OF", X.ref(ws, 2, 5))
        X.kv(ws, 6, "Recovery rate R", R, fmt="0.00%", comment="Доля непогашенного номинала, выплачиваемая в момент дефолта (recovery of face).")
        X.name(self.wb, "REC", X.ref(ws, 2, 6))
        X.kv(ws, 7, "Вес LAST и VWAP", self.params.get("secondary_weight", 0.25),
             comment="Приоритет 1: DEMA, BID, ASK, ручная цена — вес 1. Приоритет 2: LAST, VWAP — этот вес.")
        X.name(self.wb, "W2", X.ref(ws, 2, 7))
        X.kv(ws, 8, "Гладкость α", self.params.get("smoothness"), comment="Вес штрафа ROUGH за кривизну форвардных hazard rates (см. ниже).")
        X.kv(ws, 9, "Плоский дальний конец с, мес.", self.params.get("long_end_flat_from_months_resolved"),
             comment="После последнего погашения/оферты в составе hazard rate продолжается плоско (штраф β за наклон).")
        X.kv(ws, 10, "Вес плоского конца β", self.params.get("long_end_flatness"))
        X.kv(ws, 11, "Короткая бумага, лет", self.params.get("short_bond_years"))
        X.put(ws, 13, 1, "Кривая не подбирается в Excel: hazard rates находит оптимизатор (docs/credit-math.md). Файл проверяет, "
                         "как найденная кривая вместе с кривыми ставок даёт модельные цены облигаций на листе «Облигации».",
              font=X.MUTED)
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 13

        # calculator
        X.put(ws, 5, 4, "Калькулятор дожития", bold=True, comment="Меняйте жёлтую дату: Q(t), PD и рисковый DF по построенной кривой.")
        X.put(ws, 6, 4, "Дата")
        X.put(ws, 6, 5, self.as_of + relativedelta(years=3), fmt=X.DATE, fill=X.INPUT)
        X.put(ws, 7, 4, "t, лет")
        X.put(ws, 7, 5, "=(E6-AS_OF)/365", fmt=X.NUM6)
        X.put(ws, 8, 4, "Месяц k")
        X.put(ws, 8, 5, "=MATCH(E7,HZ_T,1)", comment="Номер месячного отрезка, в котором лежит t. После 10 лет — последний отрезок продолжается.")
        X.put(ws, 9, 4, "Q(t)")
        X.put(ws, 9, 5, "=" + _q("E7", "E8"), fmt=X.DF9, comment="Q(t) = exp(−H(t)), H(t) = H(t_k) + λ_k·(t − t_k): hazard постоянен внутри месяца.")
        X.put(ws, 10, 4, "PD до даты")
        X.put(ws, 10, 5, "=1-E9", fmt="0.0000%")
        X.put(ws, 11, 4, "DF безрисковый")
        X.put(ws, 11, 5, "=EXP(" + _ln("DISC", "E12", "E13") + "-DISC_LN0)", fmt=X.DF9,
              comment="DF от даты кривой D до выбранной даты по кривой дисконтирования (лист «Ставки»).")
        X.put(ws, 12, 4, "t по кривой ставок")
        X.put(ws, 12, 5, "=(E6-DISC_ASOF)/DISC_BASIS", fmt=X.NUM6)
        X.put(ws, 13, 4, "k по кривой ставок")
        X.put(ws, 13, 5, "=" + X.segment("E12", "DISC_T"))
        X.put(ws, 14, 4, "Рисковый DF = DF·Q")
        X.put(ws, 14, 5, "=E11*E9", fmt=X.DF9)
        ws.column_dimensions["D"].width = 24
        ws.column_dimensions["E"].width = 15

        h = 17
        X.header(ws, h, [
            ("k", 5, "Номер месячного узла: D, D+1M, …, D+120M."),
            ("Дата узла", 12, None),
            ("t, лет", 11, "Формула: (дата − D) / 365."),
            ("Hazard λ, % (система)", 12, "Форвардный hazard rate на отрезке от этого узла до следующего — результат калибровки. "
                                          "Последний узел — продолжение плоского конца."),
            ("λ, доли", 11, "Формула: λ% / 100."),
            ("H(t) накопл.", 12, "Формула: H(tₖ) = H(tₖ₋₁) + λₖ₋₁·(tₖ − tₖ₋₁)."),
            ("Q(t) Excel", 13, "Формула: EXP(−H)."),
            ("Q(t) система", 13, None),
            ("Сверка Q", 10, "Q Excel − Q системы."),
            ("PD накопл.", 11, "Формула: 1 − Q."),
            ("Средний λ̄, %", 11, "Формула: H/t · 100."),
            ("Спред λ̄(1−R), б.п.", 12, "Формула: H/t · (1 − R) · 10 000 — «кредитный треугольник», как на графике спреда."),
            ("Спред (система), б.п.", 12, None),
            ("Δ²λ, б.п.", 10, "Вторая разность hazard rates: (λₖ₊₁ − 2λₖ + λₖ₋₁)·100 — из неё штраф за кривизну ROUGH."),
        ])
        nodes = self.result["nodes"]
        first = h + 1
        for i, n in enumerate(nodes):
            r = first + i
            X.put(ws, r, 1, n["months"])
            X.put(ws, r, 2, X.to_date(n["date"]), fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, 3, f"=(B{r}-AS_OF)/365", fmt=X.NUM6)
            X.put(ws, r, 4, n["hazard"], fmt=X.PCT4, fill=X.SYSTEM)
            X.put(ws, r, 5, f"=D{r}/100", fmt=X.DF9)
            X.put(ws, r, 6, 0 if i == 0 else f"=F{r - 1}+E{r - 1}*(C{r}-C{r - 1})", fmt=X.DF9)
            X.put(ws, r, 7, f"=EXP(-F{r})", fmt=X.DF9)
            X.put(ws, r, 8, n["survival"], fmt=X.DF9, fill=X.SYSTEM)
            X.put(ws, r, 9, f"=G{r}-H{r}", fmt=X.TINY, fill=X.CHECK)
            X.put(ws, r, 10, f"=1-G{r}", fmt="0.0000%")
            X.put(ws, r, 11, "" if i == 0 else f"=F{r}/C{r}*100", fmt=X.PCT4)
            X.put(ws, r, 12, "" if i == 0 else f"=F{r}/C{r}*(1-REC)*10000", fmt=X.BP)
            X.put(ws, r, 13, None if i == 0 else n.get("spread_bp"), fmt=X.BP, fill=X.SYSTEM)
            if 0 < i < len(nodes) - 2:
                X.put(ws, r, 14, f"=(D{r + 1}-2*D{r}+D{r - 1})*100", fmt=X.BP)
        last = first + len(nodes) - 1
        X.name(self.wb, "HZ_T", X.rng(ws, 3, first, last))
        X.name(self.wb, "HZ_L", X.rng(ws, 5, first, last))
        X.name(self.wb, "HZ_H", X.rng(ws, 6, first, last))
        X.check_format(ws, f"I{first}:I{last}", 1e-9)
        stats = self.result.get("stats", {})
        X.put(ws, 15, 7, "ROUGH Excel", bold=True)
        X.put(ws, 15, 8, f"=SUMPRODUCT(N{first + 1}:N{last - 2},N{first + 1}:N{last - 2})/{len(nodes) - 1}", fmt=X.NUM6,
              comment="ROUGH = Σ(Δ²λ)² / 120 — штраф за кривизну, который минимизирует оптимизатор вместе с ошибками цен.")
        X.put(ws, 15, 9, "система", bold=True)
        X.put(ws, 15, 10, stats.get("roughness"), fmt=X.NUM6, fill=X.SYSTEM)
        X.put(ws, 15, 11, "=H15-J15", fmt=X.TINY, fill=X.CHECK)
        ws.freeze_panes = ws.cell(row=first, column=3)

    # ------------------------------------------------------------------ «Ставки»
    def rates_sheet(self) -> None:
        ws = self.wb.create_sheet("Ставки")
        X.title(ws, "Кривые ставок, использованные в расчёте",
                "Узлы из сервиса процентных кривых на дату кривой; DF между узлами — лог-линейная интерполяция ln DF по t")
        refs = {"DISC": self.inputs.get("discount")}
        for idx, ref in (self.inputs.get("projection") or {}).items():
            refs[idx] = ref
        column = 1
        for prefix, _key, caption in CURVE_ROLES:
            ref = refs.get(prefix)
            if not ref or not ref.get("nodes"):
                continue
            c0 = column
            basis = 360 if str(ref.get("day_count", "")).upper().endswith("360") else 365
            X.put(ws, 5, c0, caption, bold=True)
            X.put(ws, 6, c0, "Код")
            X.put(ws, 6, c0 + 1, ref["code"], fill=X.SYSTEM)
            X.put(ws, 7, c0, "Дата кривой")
            X.put(ws, 7, c0 + 1, X.to_date(ref["as_of_date"]), fmt=X.DATE, fill=X.SYSTEM,
                  comment="Если раньше даты кредитной кривой — кривая переносится на дату D по своим форвардам: DF_D(d) = DF(d)/DF(D).")
            X.name(self.wb, f"{prefix}_ASOF", X.ref(ws, c0 + 1, 7))
            X.put(ws, 8, c0, "База дней")
            X.put(ws, 8, c0 + 1, basis, fill=X.SYSTEM)
            X.name(self.wb, f"{prefix}_BASIS", X.ref(ws, c0 + 1, 8))
            X.put(ws, 9, c0, "Версия")
            X.put(ws, 9, c0 + 1, ("текущая" if ref.get("kind") == "state" else f"история v{ref.get('version')}"), fill=X.SYSTEM)
            X.put(ws, 10, c0, "t(D)")
            X.put(ws, 10, c0 + 1, f"=(AS_OF-{prefix}_ASOF)/{prefix}_BASIS", fmt=X.NUM6)
            X.put(ws, 11, c0, "k(D)")
            X.put(ws, 11, c0 + 1, "=" + X.segment(X.ref(ws, c0 + 1, 10), f"{prefix}_T"))
            X.put(ws, 12, c0, "ln DF(D)")
            X.put(ws, 12, c0 + 1, "=" + _ln(prefix, X.ref(ws, c0 + 1, 10), X.ref(ws, c0 + 1, 11)), fmt=X.DF9,
                  comment="ln DF в дату кредитной кривой: DF от D до даты d = EXP(ln DF(d) − ln DF(D)).")
            X.name(self.wb, f"{prefix}_LN0", X.ref(ws, c0 + 1, 12))
            X.header(ws, 14, [("Дата", 11, None), ("t, лет", 10, f"Формула: (дата − дата кривой) / база дней."),
                              ("DF", 13, "Фактор дисконтирования узла (из сервиса кривых)."), ("ln DF", 13, "Формула: LN(DF).")],
                     first_col=c0)
            nodes = sorted(ref["nodes"], key=lambda n: str(n["date"]))
            for i, n in enumerate(nodes):
                r = 15 + i
                X.put(ws, r, c0, X.to_date(n["date"]), fmt=X.DATE, fill=X.SYSTEM)
                X.put(ws, r, c0 + 1, f"=({X.col(c0)}{r}-{prefix}_ASOF)/{prefix}_BASIS", fmt=X.NUM6)
                X.put(ws, r, c0 + 2, float(n["df"]), fmt=X.DF9, fill=X.SYSTEM)
                X.put(ws, r, c0 + 3, f"=LN({X.col(c0 + 2)}{r})", fmt=X.DF9)
            last = 15 + len(nodes) - 1
            X.name(self.wb, f"{prefix}_T", X.rng(ws, c0 + 1, 15, last))
            X.name(self.wb, f"{prefix}_LN", X.rng(ws, c0 + 3, 15, last))
            self.curves[prefix] = ref
            column += 5
        ws.freeze_panes = "A15"

    # ------------------------------------------------------------------ «Облигации»
    def bonds_sheet(self) -> None:
        ws = self.wb.create_sheet("Облигации")
        X.title(ws, "Облигации: модельные цены и облако рыночных цен",
                "Цена модели = (PV обещанных потоков + PV дефолтной ноги) / (DF(S)·Q(S)); ошибки — в б.п. спреда через DV01")
        cols = [
            ("SECID", 15, None),
            ("Бумага", 13, None),
            ("Тип", 11, None),
            ("Индекс / маржа", 12, "Для флоатера: индекс и маржа, % годовых; лаг индекса — в листе «Потоки»."),
            ("Дата расчётов S", 11, "T+1 из ISS (SETTLEDATE); для прошлых дат — следующий рабочий день."),
            ("t(S)", 9, "Формула: (S − D)/365."),
            ("Номинал N(S), ₽", 11, "Номинал на дату расчётов из ISS: после амортизаций, для линкера — индексированный."),
            ("НКД, ₽", 9, "Накопленный купонный доход на дату расчётов из ISS."),
            ("Погашение / оферта", 11, "Эффективное погашение: ближайшая put-оферта или дата погашения."),
            ("Вес бумаги", 7, None),
            ("t_r(S)", 9, "t даты расчётов по кривой дисконтирования (лист «Ставки»)."),
            ("k_r(S)", 6, None),
            ("DF(S)", 12, "Формула: EXP(ln DF(S) − ln DF(D)) по кривой дисконтирования."),
            ("k(S)", 6, None),
            ("Q(S)", 12, "Вероятность дожития до даты расчётов (лист «Кривая»)."),
            ("ln DF RUONIA(S)", 12, "Только для линкеров: база индексации номинала I(t) = DF_RUONIA(S)/DF_RUONIA(t)."),
            ("PV обещанных потоков, ₽", 13, "Формула: SUMIF по листу «Потоки» — Σ сумма·DF·Q."),
            ("PV дефолтной ноги, ₽", 13, "Формула: SUMIF по листу «Дефолт» — Σ R·N·DF·ΔQ."),
            ("Грязная цена модели, ₽", 13, "Формула: (PV потоков + PV дефолтной ноги) / (DF(S)·Q(S)) — цена на дату расчётов при условии дожития до S."),
            ("Чистая цена модели, %", 11, "Формула: (грязная − НКД) / N(S) · 100."),
            ("Чистая цена (система), %", 11, None),
            ("Сверка, %", 10, "Excel − система."),
            ("DV01 (система)", 10, "Изменение чистой цены (% номинала) при сдвиге ставки дисконтирования на 1 б.п. Считается один раз на стартовой "
                                   "плоской кривой и переводит ошибки цен в б.п. спреда."),
        ]
        obs_first = len(cols) + 1
        for k in OBS:
            cols.append((OBS_TITLES[k], 9, "Цена из облака, использованная в расчёте (% номинала). Пусто — цены нет или оператор её исключил."))
        err_first = len(cols) + 1
        for k in OBS:
            cols.append((f"Ошибка {OBS_TITLES[k]}, б.п.", 9, "Формула: (модель − цена) / DV01."))
        tail = len(cols) + 1
        cols += [
            ("Цель (взвеш. облако), %", 11, "Взвешенное среднее облака: Σ π·цена / Σ π (веса приоритетов — строка 6). Модель стремится к ней."),
            ("Ошибка к цели, б.п.", 10, "Формула: (модель − цель) / DV01."),
            ("Ошибка (система), б.п.", 10, None),
            ("Σ w·π·e²", 11, "Вклад бумаги в FIT: вес бумаги · Σ π·ошибка²."),
            ("Σ w·π", 9, None),
            ("Z-спред рынка / модели (система), б.п.", 13, "Справочно: постоянная добавка к безрисковой ставке, при которой обещанные потоки "
                                                           "дают цену цели / модели (без дефолта)."),
        ]
        h = 8
        X.header(ws, h, cols)
        X.put(ws, 6, obs_first - 1, "Вес π:", bold=True, comment="Приоритеты наблюдений: DEMA, BID, ASK, ручная — 1; LAST, VWAP — W2 с листа «Кривая».")
        for i, k in enumerate(OBS):
            X.put(ws, 6, obs_first + i, "=W2" if k in ("last", "vwap") else 1)
        L = X.col
        first = h + 1
        for i, b in enumerate(self.bonds):
            r = first + i
            f = self.fitted[b["secid"]]
            m = b["market"]
            idx = f"{'КС' if b.get('coupon_index') == 'KEYRATE' else b.get('coupon_index')} + {b.get('spread_pct')}%" if b.get("coupon_index") else ""
            X.put(ws, r, 1, b["secid"], fill=X.SYSTEM)
            X.put(ws, r, 2, b.get("shortname"), fill=X.SYSTEM)
            X.put(ws, r, 3, KIND_TITLES.get(b.get("kind"), b.get("kind")), fill=X.SYSTEM)
            X.put(ws, r, 4, idx, fill=X.SYSTEM)
            X.put(ws, r, 5, X.to_date(m["settle_date"]), fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, 6, f"=(E{r}-AS_OF)/365", fmt=X.NUM6)
            X.put(ws, r, 7, m["face"], fmt=X.NUM2, fill=X.SYSTEM)
            X.put(ws, r, 8, f["accrued"], fmt=X.NUM2, fill=X.SYSTEM,
                  comment="НКД оценён системой по графику купонов (в ISS не было значения)." if m.get("accrued_estimated") else None)
            X.put(ws, r, 9, X.to_date(f["maturity"]), fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, 10, f["weight"], fill=X.SYSTEM)
            X.put(ws, r, 11, f"=(E{r}-DISC_ASOF)/DISC_BASIS", fmt=X.NUM6)
            X.put(ws, r, 12, "=" + X.segment(f"K{r}", "DISC_T"))
            X.put(ws, r, 13, f"=EXP({_ln('DISC', f'K{r}', f'L{r}')}-DISC_LN0)", fmt=X.DF9)
            X.put(ws, r, 14, f"=MATCH(F{r},HZ_T,1)")
            X.put(ws, r, 15, "=" + _q(f"F{r}", f"N{r}"), fmt=X.DF9)
            if b.get("kind") == "linker" and "RUONIA" in self.curves:
                t = f"((E{r}-RUONIA_ASOF)/RUONIA_BASIS)"
                k = X.segment(t, "RUONIA_T")
                X.put(ws, r, 16, "=" + _ln("RUONIA", t, k), fmt=X.DF9)
            X.put(ws, r, 17, f"=SUMIF(F_SECID,A{r},F_PV)", fmt=X.NUM6)
            X.put(ws, r, 18, f"=SUMIF(G_SECID,A{r},G_PV)", fmt=X.NUM6)
            X.put(ws, r, 19, f"=(Q{r}+R{r})/(M{r}*O{r})", fmt=X.NUM6)
            X.put(ws, r, 20, f"=(S{r}-H{r})/G{r}*100", fmt=X.NUM6)
            X.put(ws, r, 21, f.get("model_price"), fmt=X.NUM6, fill=X.SYSTEM)
            X.put(ws, r, 22, f"=T{r}-U{r}", fmt=X.TINY, fill=X.CHECK)
            X.put(ws, r, 23, f.get("dv01"), fmt="0.000000", fill=X.SYSTEM)
            prices = {o["kind"]: o["price"] for o in f.get("observations", []) if o.get("price") is not None and (o.get("weight") or 0) > 0}
            for j, k in enumerate(OBS):
                c = obs_first + j
                X.put(ws, r, c, prices.get(k), fmt="0.0000", fill=X.SYSTEM)
                e = err_first + j
                X.put(ws, r, e, f'=IF({L(c)}{r}="","",(T{r}-{L(c)}{r})/W{r})', fmt=X.BP)
            o1, o2 = L(obs_first), L(obs_first + len(OBS) - 1)
            e1, e2 = L(err_first), L(err_first + len(OBS) - 1)
            w1, w2 = f"${o1}$6", f"${o2}$6"
            X.put(ws, r, tail, f"=SUMPRODUCT({o1}{r}:{o2}{r},{w1}:{w2})/SUMIF({o1}{r}:{o2}{r},\">0\",{w1}:{w2})", fmt="0.0000")
            X.put(ws, r, tail + 1, f"=(T{r}-{L(tail)}{r})/W{r}", fmt=X.BP)
            X.put(ws, r, tail + 2, f.get("error_bp"), fmt=X.BP, fill=X.SYSTEM)
            X.put(ws, r, tail + 3, f"=J{r}*SUMPRODUCT({e1}{r}:{e2}{r},{e1}{r}:{e2}{r},{w1}:{w2})", fmt=X.NUM6)
            X.put(ws, r, tail + 4, f"=J{r}*SUMIF({o1}{r}:{o2}{r},\">0\",{w1}:{w2})", fmt="0.00")
            zm, zo = f.get("z_spread_market_bp"), f.get("z_spread_model_bp")
            X.put(ws, r, tail + 5, f"{zm:.1f} / {zo:.1f}" if zm is not None and zo is not None else "", fill=X.SYSTEM)
        last = first + len(self.bonds) - 1
        X.name(self.wb, "B_SECID", X.rng(ws, 1, first, last))
        X.name(self.wb, "B_FACE", X.rng(ws, 7, first, last))
        X.name(self.wb, "B_LNR", X.rng(ws, 16, first, last))
        X.check_format(ws, f"V{first}:V{last}", 1e-8)

        stats = self.result.get("stats", {})
        s = last + 2
        X.put(ws, s, 1, "Статистика подгонки", bold=True)
        X.put(ws, s, 5, "Excel", bold=True)
        X.put(ws, s, 6, "Система", bold=True)
        X.put(ws, s, 7, "Сверка", bold=True)
        e1, e2 = L(err_first), L(err_first + len(OBS) - 1)
        area = f"{e1}{first}:{e2}{last}"
        rows = [
            ("RMSE, б.п.", f"=SQRT(SUM({L(tail + 3)}{first}:{L(tail + 3)}{last})/SUM({L(tail + 4)}{first}:{L(tail + 4)}{last}))",
             stats.get("rmse_bp"), "√(Σ w·π·e² / Σ w·π) по всем ценам облака — FIT, который минимизирует оптимизатор вместе со штрафами гладкости."),
            ("Макс. |ошибка|, б.п.", f"=MAX(MAX({area}),-MIN({area}))", stats.get("max_abs_bp"), None),
            ("Бумаг в расчёте", f"=COUNT(B_FACE)", stats.get("n_bonds"), None),
            ("Цен в расчёте", f"=COUNT({L(obs_first)}{first}:{L(obs_first + len(OBS) - 1)}{last})", stats.get("n_observations"), None),
        ]
        for j, (label, formula, system, comment) in enumerate(rows):
            r = s + 1 + j
            X.put(ws, r, 1, label, comment=comment)
            X.put(ws, r, 5, formula, fmt=X.BP)
            X.put(ws, r, 6, system, fmt=X.BP, fill=X.SYSTEM)
            X.put(ws, r, 7, f"=E{r}-F{r}", fmt=X.TINY, fill=X.CHECK)
        X.check_format(ws, f"G{s + 1}:G{s + len(rows)}", 1e-6)
        skipped = [b for b in self.snap.get("bonds", []) if b.get("secid") not in self.fitted]
        if skipped:
            r = s + len(rows) + 2
            X.put(ws, r, 1, "Не участвуют в расчёте:", bold=True)
            for j, b in enumerate(skipped):
                X.put(ws, r + 1 + j, 1, b.get("secid"))
                X.put(ws, r + 1 + j, 2, b.get("shortname"))
                X.put(ws, r + 1 + j, 3, {"excluded": "выключена оператором", "skipped": "не подходит / нет данных",
                                         "no_prices": "нет цен"}.get(b.get("status"), b.get("status")))
        ws.freeze_panes = ws.cell(row=first, column=3)

    # ------------------------------------------------------------------ «Потоки»
    def flows_sheet(self) -> None:
        ws = self.wb.create_sheet("Потоки")
        X.title(ws, "Обещанные денежные потоки облигаций", "PV = сумма · DF · Q: дисконтирование безрисковой кривой и вероятность дожития до даты выплаты")
        X.header(ws, 5, [
            ("SECID", 15, None),
            ("Бумага", 12, None),
            ("№", 5, None),
            ("Поток", 11, None),
            ("Как получена сумма", 26, "Источник суммы: зафиксированный купон из ISS, расчёт по ставке, прогноз флоатера, индексация линкера."),
            ("Начало периода", 11, None),
            ("Дата выплаты", 11, "Купон принадлежит покупателю, если дата фиксации реестра (или день до выплаты) не раньше даты расчётов."),
            ("Дней", 6, "Формула: выплата − начало периода."),
            ("N(S), ₽", 10, "Номинал на дату расчётов (лист «Облигации»)."),
            ("Номинал периода, ₽", 11, "Не линкер: N(S) − амортизации до начала периода (SUMIFS). Линкер: N(S)·I(t). Погашение: остаток номинала."),
            ("I(t) индексация", 11, "Линкер: I(t) = DF_RUONIA(S)/DF_RUONIA(t) = EXP(ln DF_R(S) − ln DF_R(t)) — ожидаемая накопленная RUONIA "
                                    "с ежедневной капитализацией."),
            ("Лаг индекса, дн.", 7, None),
            ("Форвард индекса, %", 10, "Флоатер: простая ставка ACT/365 по кривой индекса между (начало − лаг) и (выплата − лаг): "
                                       "(DF(a)/DF(b) − 1)·365/(b − a)·100. Начавшаяся часть периода — по форварду от даты кривой."),
            ("Маржа, %", 8, None),
            ("Ставка купона, %", 9, "Флоатер: форвард + маржа. Фикс и линкер: ставка купона (из ISS или ручная)."),
            ("Цена погашения, %", 8, "Для погашения: цена оферты или 100%."),
            ("Сумма Excel, ₽", 12, "Купон: номинал × ставка/100 × дни/365; погашение: номинал × цена/100. Серые ячейки — суммы из ISS "
                                   "(купон уже зафиксирован эмитентом, амортизация по графику): используются как есть."),
            ("Сумма (система), ₽", 12, None),
            ("Сверка, ₽", 10, None),
            ("t, лет", 9, "Формула: (дата − D)/365."),
            ("t_r", 9, "t по кривой дисконтирования."),
            ("k_r", 5, None),
            ("DF", 12, "EXP(ln DF(t) − ln DF(D)) по кривой дисконтирования."),
            ("k", 5, "Месяц кредитной кривой."),
            ("Q(t)", 12, "Вероятность дожития до даты выплаты."),
            ("PV, ₽", 12, "Формула: сумма Excel · DF · Q."),
            ("t RUONIA", 9, "Вспомогательные столбцы интерполяции кривой RUONIA (линкер)."),
            ("k", 5, None),
            ("ln DF_R(t)", 12, None),
            ("a, дн.", 7, "Флоатер: начало окна индекса в днях от D = MAX(начало − D − лаг, 0)."),
            ("b, дн.", 7, "Конец окна = MAX(выплата − D − лаг, a + 1)."),
            ("t(a)", 9, None),
            ("k(a)", 5, None),
            ("ln DF(a)", 12, None),
            ("t(b)", 9, None),
            ("k(b)", 5, None),
            ("ln DF(b)", 12, None),
        ])
        r = 6
        for b in self.bonds:
            idx_prefix = b.get("coupon_index") if b.get("coupon_index") in self.curves else None
            lag = b.get("lag_days") or 0
            for j, cf in enumerate(b["cashflows"], start=1):
                src = cf.get("source", "")
                X.put(ws, r, 1, b["secid"], fill=X.SYSTEM)
                X.put(ws, r, 2, b.get("shortname"), fill=X.SYSTEM)
                X.put(ws, r, 3, j)
                X.put(ws, r, 4, FLOW_TITLES.get(cf["kind"], cf["kind"]), fill=X.SYSTEM)
                X.put(ws, r, 5, SOURCE_TITLES.get(src, src), fill=X.SYSTEM)
                X.put(ws, r, 6, X.to_date(cf.get("start")), fmt=X.DATE, fill=X.SYSTEM)
                X.put(ws, r, 7, X.to_date(cf["date"]), fmt=X.DATE, fill=X.SYSTEM)
                X.put(ws, r, 9, f"=INDEX(B_FACE,MATCH(A{r},B_SECID,0))", fmt=X.NUM2)
                X.put(ws, r, 20, f"=(G{r}-AS_OF)/365", fmt=X.NUM6)
                X.put(ws, r, 21, f"=(G{r}-DISC_ASOF)/DISC_BASIS", fmt=X.NUM6)
                X.put(ws, r, 22, "=" + X.segment(f"U{r}", "DISC_T"))
                X.put(ws, r, 23, f"=EXP({_ln('DISC', f'U{r}', f'V{r}')}-DISC_LN0)", fmt=X.DF9)
                X.put(ws, r, 24, f"=MATCH(T{r},HZ_T,1)")
                X.put(ws, r, 25, "=" + _q(f"T{r}", f"X{r}"), fmt=X.DF9)
                X.put(ws, r, 26, f"=Q{r}*W{r}*Y{r}", fmt=X.NUM6)
                X.put(ws, r, 18, cf["amount"], fmt=X.NUM6, fill=X.SYSTEM)
                X.put(ws, r, 19, f"=Q{r}-R{r}", fmt=X.TINY, fill=X.CHECK)
                if cf["kind"] == "coupon":
                    X.put(ws, r, 8, f"=G{r}-F{r}")
                if src in ("linker_coupon", "linker_redemption") and "RUONIA" in self.curves:
                    X.put(ws, r, 27, f"=(G{r}-RUONIA_ASOF)/RUONIA_BASIS", fmt=X.NUM6)
                    X.put(ws, r, 28, "=" + X.segment(f"AA{r}", "RUONIA_T"))
                    X.put(ws, r, 29, "=" + _ln("RUONIA", f"AA{r}", f"AB{r}"), fmt=X.DF9)
                    X.put(ws, r, 11, f"=EXP(INDEX(B_LNR,MATCH(A{r},B_SECID,0))-AC{r})", fmt=X.DF9)
                    X.put(ws, r, 10, f"=I{r}*K{r}", fmt=X.NUM6)
                elif src == "redemption":
                    X.put(ws, r, 10, f'=I{r}-SUMIFS(F_AMOUNT,F_SECID,A{r},F_KIND,"амортизация")', fmt=X.NUM6)
                elif cf["kind"] == "coupon":
                    X.put(ws, r, 10, f'=I{r}-SUMIFS(F_AMOUNT,F_SECID,A{r},F_KIND,"амортизация",F_DATE,"<="&F{r})', fmt=X.NUM6)
                if src == "floating" and idx_prefix:
                    X.put(ws, r, 12, lag, fill=X.SYSTEM)
                    X.put(ws, r, 30, f"=MAX(F{r}-AS_OF-L{r},0)")
                    X.put(ws, r, 31, f"=MAX(G{r}-AS_OF-L{r},AD{r}+1)")
                    X.put(ws, r, 32, f"=(AS_OF+AD{r}-{idx_prefix}_ASOF)/{idx_prefix}_BASIS", fmt=X.NUM6)
                    X.put(ws, r, 33, "=" + X.segment(f"AF{r}", f"{idx_prefix}_T"))
                    X.put(ws, r, 34, "=" + _ln(idx_prefix, f"AF{r}", f"AG{r}"), fmt=X.DF9)
                    X.put(ws, r, 35, f"=(AS_OF+AE{r}-{idx_prefix}_ASOF)/{idx_prefix}_BASIS", fmt=X.NUM6)
                    X.put(ws, r, 36, "=" + X.segment(f"AI{r}", f"{idx_prefix}_T"))
                    X.put(ws, r, 37, "=" + _ln(idx_prefix, f"AI{r}", f"AJ{r}"), fmt=X.DF9)
                    X.put(ws, r, 13, f"=(EXP(AH{r}-AK{r})-1)*365/(AE{r}-AD{r})*100", fmt=X.PCT4)
                    X.put(ws, r, 14, cf.get("spread_pct"), fmt=X.PCT4, fill=X.SYSTEM)
                    X.put(ws, r, 15, f"=M{r}+N{r}", fmt=X.PCT4)
                    X.put(ws, r, 17, f"=J{r}*O{r}/100*H{r}/365", fmt=X.NUM6)
                elif src in ("fixed_rate", "linker_coupon"):
                    X.put(ws, r, 15, cf.get("rate_pct"), fmt=X.PCT4, fill=X.SYSTEM)
                    X.put(ws, r, 17, f"=J{r}*O{r}/100*H{r}/365", fmt=X.NUM6)
                elif src in ("redemption", "linker_redemption"):
                    X.put(ws, r, 16, cf.get("price_pct"), fmt="0.00", fill=X.SYSTEM)
                    X.put(ws, r, 17, f"=J{r}*P{r}/100", fmt=X.NUM6)
                else:  # amounts taken from ISS as they are
                    if cf.get("rate_pct") is not None:
                        X.put(ws, r, 15, cf.get("rate_pct"), fmt=X.PCT4, fill=X.SYSTEM)
                    X.put(ws, r, 17, cf["amount"], fmt=X.NUM6, fill=X.SYSTEM)  # see «Как получена сумма»
                r += 1
        last = max(r - 1, 6)
        X.name(self.wb, "F_SECID", X.rng(ws, 1, 6, last))
        X.name(self.wb, "F_KIND", X.rng(ws, 4, 6, last))
        X.name(self.wb, "F_DATE", X.rng(ws, 7, 6, last))
        X.name(self.wb, "F_AMOUNT", X.rng(ws, 18, 6, last))  # ISS amounts (constants): no circular reference
        X.name(self.wb, "F_PV", X.rng(ws, 26, 6, last))
        X.check_format(ws, f"S6:S{last}", 1e-6)
        ws.freeze_panes = "C6"

    # ------------------------------------------------------------------ «Дефолт»
    def default_sheet(self) -> None:
        ws = self.wb.create_sheet("Дефолт")
        X.title(ws, "Дефолтная нога: выплата recovery при дефолте",
                "PV = R · N(u_{j−1}) · DF(середина) · (Q(u_{j−1}) − Q(u_j)) на сетке: дата расчётов, концы месяцев, даты потоков, погашение")
        X.header(ws, 5, [
            ("SECID", 15, None),
            ("Бумага", 12, None),
            ("j", 5, None),
            ("u_{j−1}", 11, "Начало интервала сетки: дата расчётов, концы месяцев кредитной кривой, даты потоков."),
            ("u_j", 11, None),
            ("N(u_{j−1}), ₽", 12, "Непогашенный номинал на начало интервала — требование к эмитенту при дефолте. Не линкер: N(S) − амортизации "
                                  "с датой ≤ u. Линкер: N(S)·I(u)."),
            ("R", 7, None),
            ("Середина интервала", 11, "(u_{j−1} + u_j)/2 — дефолт в среднем в середине интервала."),
            ("t_r середины", 9, None),
            ("k_r", 5, None),
            ("DF(середина)", 12, "По кривой дисконтирования."),
            ("t(u_{j−1})", 9, None),
            ("k", 5, None),
            ("Q(u_{j−1})", 12, None),
            ("t(u_j)", 9, None),
            ("k", 5, None),
            ("Q(u_j)", 12, None),
            ("ΔQ = вероятность дефолта на интервале", 13, "Q(u_{j−1}) − Q(u_j)."),
            ("PV, ₽", 12, "Формула: R · N · DF · ΔQ."),
            ("t RUONIA", 9, "Линкер: интерполяция кривой RUONIA в u_{j−1}."),
            ("k", 5, None),
            ("ln DF_R", 12, None),
            ("I(u)", 11, "EXP(ln DF_R(S) − ln DF_R(u))."),
        ])
        grid = HazardGrid.monthly(self.as_of)
        r = 6
        for b in self.bonds:
            m = b["market"]
            settle = X.to_date(m["settle_date"])
            maturity = X.to_date(self.fitted[b["secid"]]["maturity"])
            flow_days = np.array([(X.to_date(cf["date"]) - self.as_of).days for cf in b["cashflows"]], dtype=float)
            u = default_grid_days(self.as_of, grid, (settle - self.as_of).days, (maturity - self.as_of).days, flow_days)
            dates = [self.as_of + relativedelta(days=int(d)) for d in u]
            linker = b.get("kind") == "linker" and "RUONIA" in self.curves
            for j in range(1, len(dates)):
                X.put(ws, r, 1, b["secid"], fill=X.SYSTEM)
                X.put(ws, r, 2, b.get("shortname"), fill=X.SYSTEM)
                X.put(ws, r, 3, j)
                X.put(ws, r, 4, dates[j - 1], fmt=X.DATE, fill=X.SYSTEM)
                X.put(ws, r, 5, dates[j], fmt=X.DATE, fill=X.SYSTEM)
                if linker:
                    X.put(ws, r, 20, f"=(D{r}-RUONIA_ASOF)/RUONIA_BASIS", fmt=X.NUM6)
                    X.put(ws, r, 21, "=" + X.segment(f"T{r}", "RUONIA_T"))
                    X.put(ws, r, 22, "=" + _ln("RUONIA", f"T{r}", f"U{r}"), fmt=X.DF9)
                    X.put(ws, r, 23, f"=EXP(INDEX(B_LNR,MATCH(A{r},B_SECID,0))-V{r})", fmt=X.DF9)
                    X.put(ws, r, 6, f"=INDEX(B_FACE,MATCH(A{r},B_SECID,0))*W{r}", fmt=X.NUM6)
                else:
                    X.put(ws, r, 6, f'=INDEX(B_FACE,MATCH(A{r},B_SECID,0))-SUMIFS(F_AMOUNT,F_SECID,A{r},F_KIND,"амортизация",F_DATE,"<="&D{r})',
                          fmt=X.NUM6)
                X.put(ws, r, 7, "=REC", fmt="0.00%")
                X.put(ws, r, 8, f"=(D{r}+E{r})/2", fmt="dd.mm.yyyy hh:mm")
                X.put(ws, r, 9, f"=(H{r}-DISC_ASOF)/DISC_BASIS", fmt=X.NUM6)
                X.put(ws, r, 10, "=" + X.segment(f"I{r}", "DISC_T"))
                X.put(ws, r, 11, f"=EXP({_ln('DISC', f'I{r}', f'J{r}')}-DISC_LN0)", fmt=X.DF9)
                X.put(ws, r, 12, f"=(D{r}-AS_OF)/365", fmt=X.NUM6)
                X.put(ws, r, 13, f"=MATCH(L{r},HZ_T,1)")
                X.put(ws, r, 14, "=" + _q(f"L{r}", f"M{r}"), fmt=X.DF9)
                X.put(ws, r, 15, f"=(E{r}-AS_OF)/365", fmt=X.NUM6)
                X.put(ws, r, 16, f"=MATCH(O{r},HZ_T,1)")
                X.put(ws, r, 17, "=" + _q(f"O{r}", f"P{r}"), fmt=X.DF9)
                X.put(ws, r, 18, f"=N{r}-Q{r}", fmt=X.DF9)
                X.put(ws, r, 19, f"=G{r}*F{r}*K{r}*R{r}", fmt=X.NUM6)
                r += 1
        last = max(r - 1, 6)
        X.name(self.wb, "G_SECID", X.rng(ws, 1, 6, last))
        X.name(self.wb, "G_PV", X.rng(ws, 19, 6, last))
        ws.freeze_panes = "D6"


def build(snap: dict[str, Any]) -> bytes:
    book = _Book(snap)
    book.curve_sheet()
    book.rates_sheet()
    book.bonds_sheet()
    book.flows_sheet()
    book.default_sheet()
    # «Облигации» first after the curve: it is where the checks are
    book.wb.move_sheet("Облигации", offset=-1)
    return X.save(book.wb)


def is_complete(snap: dict[str, Any]) -> bool:
    """A snapshot saved before the calculation detail was stored lacks cash-flow derivations and curve nodes."""
    inputs = snap.get("inputs") or {}
    if not (inputs.get("discount") or {}).get("nodes"):
        return False
    for b in snap.get("bonds", []):
        if b.get("status") == "used":
            flows = b.get("cashflows") or []
            if not flows or "source" not in flows[0]:
                return False
    return True
