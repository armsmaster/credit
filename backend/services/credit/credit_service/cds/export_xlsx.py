"""Excel files of CDS pricing.

* build_curve / build_contract — calculation detail with live formulas: hazard curve -> survival,
  discount factors, payment schedule (ACT/ACT ISDA accruals), exact integrals of the protection leg
  and of accrual-on-default on every sub-interval, RPV01, par spread, PUF, upfront and NPV, each
  compared with the system;
* build_daily — results of the day for the position keeping system (plain values, one header row
  per sheet): discount factors, survival / hazard by month, CDS quotes by tenor.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from dateutil.relativedelta import relativedelta
from openpyxl import Workbook

from cds_common import xlsx as X

from .isda import DiscountCurve, HazardCurve, Period, aod_pieces, protection_pieces, yf_act365f

BP = 1e4


def _diy(y: str) -> str:
    return f"IF(OR(MOD({y},400)=0,AND(MOD({y},4)=0,MOD({y},100)<>0)),366,365)"


def _yf(day_count: str, s: str, e: str) -> str:
    """Accrual fraction formula: ACT/ACT (ISDA) splits by calendar years."""
    if day_count == "ACT/360":
        return f"({e}-{s})/360"
    if day_count == "ACT/365F":
        return f"({e}-{s})/365"
    ys, ye = f"YEAR({s})", f"YEAR({e})"
    return (f"IF({e}<={s},0,IF({ys}={ye},({e}-{s})/{_diy(ys)},(DATE({ys}+1,1,1)-{s})/{_diy(ys)}"
            f"+({ye}-{ys}-1)+({e}-DATE({ye},1,1))/{_diy(ye)}))")


def _kappa(day_count: str, d: str) -> str:
    if day_count == "ACT/360":
        return "365/360"
    if day_count == "ACT/365F":
        return "1"
    return f"365/{_diy(f'YEAR({d})')}"


def _q(t: str) -> str:
    """Q(t) on the CDS hazard curve: k = MATCH(t, segment starts)."""
    k = f"MATCH({t},HZ_START,1)"
    return f"EXP(-(INDEX(HZ_H,{k})+INDEX(HZ_L,{k})*({t}-INDEX(HZ_START,{k}))))"


def _df(t: str) -> str:
    """DF from the valuation date (log-linear in ln DF, ACT/365F time)."""
    return "EXP(" + X.loglin(t, X.segment(t, "DF_T"), "DF_T", "DF_LN") + ")"


class _Detail:
    def __init__(self, title: str, subtitle: str, valuation: date, recovery: float, hazard: list[dict[str, Any]],
                 discount_nodes: list[dict[str, Any]], day_count: str):
        self.wb = Workbook()
        self.valuation = valuation
        self.recovery = recovery
        self.day_count = day_count
        self.hc = HazardCurve(valuation, [h["t"] for h in hazard], [h["lambda"] for h in hazard])
        self.dc = DiscountCurve(valuation, [date.fromisoformat(n["date"]) for n in discount_nodes], [float(n["df"]) for n in discount_nodes])
        self.summary = self.wb.active
        self.summary.title = "Итог"
        X.title(self.summary, title, subtitle)
        self._curves_sheet(hazard, discount_nodes)
        self.sched = self.wb.create_sheet("График")
        self.pieces = self.wb.create_sheet("Интегралы")
        self.sched_row, self.piece_row = 6, 6
        self._init_sheets()

    # ------------------------------------------------------------------ curves
    def _curves_sheet(self, hazard: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> None:
        ws = self.wb.create_sheet("Кривые")
        X.title(ws, "Кривая интенсивности дефолта и дисконт-факторы",
                "λ кусочно-постоянна между узлами, после последнего — плоско; ln DF линеен по t; время t — ACT/365F от даты оценки")
        X.kv(ws, 5, "Дата оценки V", self.valuation, fmt=X.DATE, comment="t = (дата − V) / 365 для дисконтирования и интенсивности.")
        X.name(self.wb, "VAL", X.ref(ws, 2, 5))
        X.kv(ws, 6, "Recovery R", self.recovery, fmt="0.00%", comment="Ставка восстановления оценки (не Rq котировок QS).")
        X.name(self.wb, "REC", X.ref(ws, 2, 6))
        X.header(ws, 8, [("Отрезок", 8, None), ("Начало t", 11, "Начало отрезка (0 для первого)."),
                         ("Конец t", 11, "Узел кривой: срок котировки, на котором бутстрапилась λ."), ("Дата узла", 12, None),
                         ("λ (система)", 12, "Интенсивность дефолта на отрезке, доли в год."),
                         ("H в начале", 13, "Накопленная интенсивность: H(начало k) = H(начало k−1) + λₖ₋₁·(длина отрезка)."),
                         ("Q в узле", 13, "EXP(−(H + λ·(конец − начало)))")])
        for i, h in enumerate(hazard):
            r = 9 + i
            X.put(ws, r, 1, i + 1)
            X.put(ws, r, 2, 0 if i == 0 else f"=C{r - 1}", fmt=X.NUM6)
            X.put(ws, r, 3, h["t"], fmt=X.NUM6, fill=X.SYSTEM)
            X.put(ws, r, 4, date.fromisoformat(h["date"]), fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, 5, h["lambda"], fmt=X.DF9, fill=X.SYSTEM)
            X.put(ws, r, 6, 0 if i == 0 else f"=F{r - 1}+E{r - 1}*(B{r}-B{r - 1})", fmt=X.DF9)
            X.put(ws, r, 7, f"=EXP(-(F{r}+E{r}*(C{r}-B{r})))", fmt=X.DF9)
        last = 8 + len(hazard)
        X.name(self.wb, "HZ_START", X.rng(ws, 2, 9, last))
        X.name(self.wb, "HZ_L", X.rng(ws, 5, 9, last))
        X.name(self.wb, "HZ_H", X.rng(ws, 6, 9, last))

        c0 = 10
        X.header(ws, 8, [("Дата", 12, "Узлы процентной кривой после даты оценки, пересчитанные к ней: DF_V(d) = DF(d)/DF(V)."),
                         ("t", 10, "(дата − V)/365"), ("DF (система)", 14, None), ("ln DF", 14, "LN(DF)")], first_col=c0)
        X.put(ws, 9, c0, self.valuation, fmt=X.DATE, fill=X.SYSTEM)
        X.put(ws, 9, c0 + 1, "=(J9-VAL)/365", fmt=X.NUM6)
        X.put(ws, 9, c0 + 2, 1.0, fmt=X.DF9, fill=X.SYSTEM)
        X.put(ws, 9, c0 + 3, "=LN(L9)", fmt=X.DF9)
        for i, n in enumerate(nodes):
            r = 10 + i
            X.put(ws, r, c0, date.fromisoformat(n["date"]), fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, c0 + 1, f"=(J{r}-VAL)/365", fmt=X.NUM6)
            X.put(ws, r, c0 + 2, float(n["df"]), fmt=X.DF9, fill=X.SYSTEM)
            X.put(ws, r, c0 + 3, f"=LN(L{r})", fmt=X.DF9)
        dl = 9 + len(nodes)
        X.name(self.wb, "DF_T", X.rng(ws, c0 + 1, 9, dl))
        X.name(self.wb, "DF_LN", X.rng(ws, c0 + 3, 9, dl))
        ws.freeze_panes = "A9"

    # ------------------------------------------------------------------ schedule and integrals
    def _init_sheets(self) -> None:
        X.title(self.sched, "Графики платежей и купонная нога",
                "Купон к выплате = α·DF(платёж)·Q(последний день периода); α — доля года по конвенции купона")
        X.header(self.sched, 5, [
            ("Контракт", 10, None), ("№", 5, None), ("Начало периода", 12, None), ("Конец периода", 12, "Для последнего периода — Плановая дата прекращения (включительно)."),
            ("Дата платежа", 12, "Конец периода, перенесённый по «Следующий»."),
            ("Конец, искл.", 12, "Конец + 1 день для последнего периода (последний день входит в начисление)."),
            ("Дней", 6, None), ("α (Excel)", 11, "Доля года: ACT/ACT (ISDA) делит дни каждого календарного года на его длину."),
            ("α (система)", 11, None), ("Сверка α", 9, None),
            ("Наблюдение", 12, "Последний день периода: выживание до него."), ("t платежа", 9, None), ("DF платежа", 12, None),
            ("t наблюдения", 9, None), ("Q наблюдения", 12, None), ("α·DF·Q", 12, "Вклад купона в RPV01 (на единицу купона)."),
            ("AoD", 12, "Начисление до дефолта на этом периоде: SUMIFS по листу «Интегралы»."),
        ])
        X.title(self.pieces, "Точные интегралы ног на подотрезках",
                "На отрезке [a, b] λ и f постоянны: ∫PλQ = Q(a)P(a)λ/μ(1−e^{−μΔ}); ∫αPλQ = Q(a)P(a)[α_a·λ/μ(1−e^{−μΔ}) + κλ(1−e^{−μΔ}(1+μΔ))/μ²]")
        X.header(self.pieces, 5, [
            ("Контракт", 10, None), ("Нога", 10, "защита — нога защиты; AoD — начисление купона до момента дефолта."),
            ("Период", 7, "Для AoD — номер купонного периода."), ("a", 11, "Начало подотрезка, t (сетка: узлы обеих кривых, границы периодов, 1 января)."),
            ("b", 11, None), ("λ", 11, "Интенсивность на подотрезке (по середине)."), ("P(a)", 12, None), ("P(b)", 12, None),
            ("f", 11, "Форвард: −LN(P(b)/P(a))/(b − a)."), ("Q(a)", 12, None), ("μ = λ + f", 11, None), ("Δ", 10, None),
            ("Начало периода", 12, None), ("Дата a", 12, "V + ROUND(a·365) дней."), ("α_a", 11, "Доля года начисления к моменту a."),
            ("κ", 9, "dα/dt: 365/дней в году для ACT/ACT."), ("Значение Excel", 13, None), ("Значение (система)", 13, None), ("Сверка", 10, None),
        ])

    def add_contract(self, key: str, schedule: list[dict[str, Any]], protection_start: date, maturity: date) -> dict[str, Any]:
        """Schedule rows and integral pieces of one contract; returns the cell ranges for the summary formulas."""
        ws, wp, dc_name = self.sched, self.pieces, self.day_count
        first_s = self.sched_row
        periods = []
        for j, row in enumerate(schedule, start=1):
            r = self.sched_row
            start, end, pay = (date.fromisoformat(row[k]) for k in ("accrual_start", "accrual_end", "pay_date"))
            last = j == len(schedule)
            periods.append(Period(start, end, pay, row["year_frac"]))
            X.put(ws, r, 1, key, fill=X.SYSTEM)
            X.put(ws, r, 2, j)
            X.put(ws, r, 3, start, fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, 4, end, fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, 5, pay, fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, 6, f"=D{r}+1" if last else f"=D{r}", fmt=X.DATE)
            X.put(ws, r, 7, f"=F{r}-C{r}")
            X.put(ws, r, 8, "=" + _yf(dc_name, f"C{r}", f"F{r}"), fmt=X.NUM6)
            X.put(ws, r, 9, row["year_frac"], fmt=X.NUM6, fill=X.SYSTEM)
            X.put(ws, r, 10, f"=H{r}-I{r}", fmt=X.TINY, fill=X.CHECK)
            if row.get("observation"):
                X.put(ws, r, 11, f"=F{r}-1", fmt=X.DATE)
                X.put(ws, r, 12, f"=(E{r}-VAL)/365", fmt=X.NUM6)
                X.put(ws, r, 13, "=" + _df(f"L{r}"), fmt=X.DF9)
                X.put(ws, r, 14, f"=(K{r}-VAL)/365", fmt=X.NUM6)
                X.put(ws, r, 15, "=" + _q(f"N{r}"), fmt=X.DF9)
                X.put(ws, r, 16, f"=H{r}*M{r}*O{r}", fmt=X.DF9)
                X.put(ws, r, 17, f'=SUMIFS(P_VALUE,P_KEY,A{r},P_LEG,"AoD",P_PERIOD,B{r})', fmt=X.DF9)
            self.sched_row += 1
        last_s = self.sched_row - 1

        # integral pieces, generated with the engine's own grids
        t_ps = yf_act365f(self.valuation, protection_start)
        t_mat = yf_act365f(self.valuation, maturity)
        rows = [("защита", 0, None, p) for p in protection_pieces(t_ps, t_mat, self.dc, self.hc)]
        one = timedelta(days=1)
        for j, p in enumerate(periods, start=1):
            is_last = j == len(periods)
            end_excl = p.accrual_end + one if is_last else p.accrual_end
            if end_excl <= protection_start + one:
                continue
            obs = end_excl - one
            t_from = yf_act365f(self.valuation, max(p.accrual_start, protection_start))
            t_to = yf_act365f(self.valuation, obs)
            rows += [("AoD", j, p.accrual_start, x) for x in aod_pieces(self.valuation, p, t_from, t_to, dc_name, self.dc, self.hc)]
        first_p = self.piece_row
        for leg, j, start, x in rows:
            r = self.piece_row
            X.put(wp, r, 1, key, fill=X.SYSTEM)
            X.put(wp, r, 2, leg, fill=X.SYSTEM)
            X.put(wp, r, 3, j or None)
            X.put(wp, r, 4, x["a"], fmt=X.NUM6, fill=X.SYSTEM)
            X.put(wp, r, 5, x["b"], fmt=X.NUM6, fill=X.SYSTEM)
            X.put(wp, r, 6, f"=INDEX(HZ_L,MATCH((D{r}+E{r})/2,HZ_START,1))", fmt=X.DF9)
            X.put(wp, r, 7, "=" + _df(f"D{r}"), fmt=X.DF9)
            X.put(wp, r, 8, "=" + _df(f"E{r}"), fmt=X.DF9)
            X.put(wp, r, 9, f"=-LN(H{r}/G{r})/(E{r}-D{r})", fmt=X.DF9)
            X.put(wp, r, 10, "=" + _q(f"D{r}"), fmt=X.DF9)
            X.put(wp, r, 11, f"=F{r}+I{r}", fmt=X.DF9)
            X.put(wp, r, 12, f"=E{r}-D{r}", fmt=X.DF9)
            if leg == "защита":
                X.put(wp, r, 17, f"=J{r}*G{r}*IF(ABS(K{r})>1E-12,F{r}/K{r}*(1-EXP(-K{r}*L{r})),F{r}*L{r})", fmt=X.DF9)
            else:
                X.put(wp, r, 13, start, fmt=X.DATE, fill=X.SYSTEM)
                X.put(wp, r, 14, f"=VAL+ROUND(D{r}*365,0)", fmt=X.DATE)
                tsd = f"((M{r}-VAL)/365)"
                X.put(wp, r, 15, f"=IF(D{r}>{tsd},{_yf(dc_name, f'M{r}', f'N{r}')},0)+(D{r}-(N{r}-VAL)/365)*{_kappa(dc_name, f'N{r}')}", fmt=X.DF9)
                X.put(wp, r, 16, f"={_kappa(dc_name, f'N{r}')}", fmt=X.NUM6)
                X.put(wp, r, 17, f"=J{r}*G{r}*IF(ABS(K{r})<1E-10,O{r}*F{r}*L{r}+P{r}*F{r}*L{r}^2/2,"
                                 f"O{r}*F{r}/K{r}*(1-EXP(-K{r}*L{r}))+P{r}*F{r}*(1-EXP(-K{r}*L{r})*(1+K{r}*L{r}))/K{r}^2)", fmt=X.DF9)
            X.put(wp, r, 18, x["value"], fmt=X.DF9, fill=X.SYSTEM)
            X.put(wp, r, 19, f"=Q{r}-R{r}", fmt=X.TINY, fill=X.CHECK)
            self.piece_row += 1
        return {"sched": (first_s, last_s), "pieces": (first_p, self.piece_row - 1)}

    def finish(self) -> bytes:
        ws, wp = self.sched, self.pieces
        X.name(self.wb, "P_KEY", X.rng(wp, 1, 6, max(self.piece_row - 1, 6)))
        X.name(self.wb, "P_LEG", X.rng(wp, 2, 6, max(self.piece_row - 1, 6)))
        X.name(self.wb, "P_PERIOD", X.rng(wp, 3, 6, max(self.piece_row - 1, 6)))
        X.name(self.wb, "P_VALUE", X.rng(wp, 17, 6, max(self.piece_row - 1, 6)))
        X.name(self.wb, "S_KEY", X.rng(ws, 1, 6, max(self.sched_row - 1, 6)))
        X.name(self.wb, "S_PV", X.rng(ws, 16, 6, max(self.sched_row - 1, 6)))
        X.name(self.wb, "S_AOD", X.rng(ws, 17, 6, max(self.sched_row - 1, 6)))
        X.check_format(ws, f"J6:J{max(self.sched_row - 1, 6)}", 1e-12)
        X.check_format(wp, f"S6:S{max(self.piece_row - 1, 6)}", 1e-12)
        ws.freeze_panes, wp.freeze_panes = "C6", "D6"
        self.wb.move_sheet("Кривые", offset=-2)
        self.wb.move_sheet("Итог", offset=-3)
        return X.save(self.wb)


SUMMARY_COLUMNS = [
    ("key", "Контракт", 10, None),
    ("mat", "Плановая дата прекращения", 12, None),
    ("eff", "Дата начала срока E", 11, "T + 1 рабочий день (СПФИ) или T (Сбер)."),
    ("settle", "Дата первоначального платежа", 11, "На эту дату дисконтируется upfront."),
    ("prot", "Начало защиты", 11, "ISDA: step-in = T + 1 календарный день, защита с конца дня оценки."),
    ("t_prot", "t начала защиты", 9, None),
    ("t_mat", "t погашения", 9, None),
    ("first", "Начало первого периода", 11, None),
    ("reb_s", "Начало ребейта", 11, "СПФИ: начало первого периода (смещённая CDS-дата); Сбер — ребейта нет."),
    ("reb_e", "Конец ребейта (искл.)", 11, "Дата начала срока E (не включается)."),
    ("reb", "α ребейта", 10, "Доля года от начала ребейта (вкл.) до конца (искл.)."),
    ("vprot", "V_prot", 12, "(1 − R) · Σ интегралов ноги защиты (лист «Интегралы»)."),
    ("rpv", "RPV01 грязный", 12, "Σ α·DF·Q по купонам + Σ AoD (лист «График»)."),
    ("dfe", "DF(t_E)", 12, "DF на дату первоначального платежа."),
    ("rpvc", "RPV01 чистый", 12, "RPV01 грязный − α_reb·DF(t_E)."),
    ("par", "Par spread Excel, б.п.", 11, "V_prot / RPV01 чистый · 10 000."),
    ("par_s", "Par spread (система), б.п.", 11, None),
    ("par_c", "Сверка, б.п.", 9, None),
]


def _cols(coupons: list[float]) -> dict[str, int]:
    keys = [c[0] for c in SUMMARY_COLUMNS]
    for cp in coupons:
        keys += [f"puf{cp}", f"puf_s{cp}", f"puf_c{cp}"]
    keys += ["q", "q_s"]
    return {k: i + 1 for i, k in enumerate(keys)}


def _summary_header(ws, row: int, coupons: list[float]) -> dict[str, int]:
    cols = [c[1:] for c in SUMMARY_COLUMNS]
    for c in coupons:
        cols += [(f"PUF при {c * BP:.0f} б.п. Excel, %", 11, "PUF = (V_prot − c·RPV01 грязный)/DF(t_E) + c·α_reb, % номинала; плюс — платит покупатель."),
                 (f"PUF при {c * BP:.0f} б.п. (система), %", 11, None), ("Сверка", 9, None)]
    cols += [("Q(погашение) Excel", 12, None), ("Q (система)", 12, None)]
    X.header(ws, row, cols)
    return _cols(coupons)


def _summary_row(ws, r: int, key: str, c: dict[str, Any], coupons: list[float], day_count: str) -> dict[str, str]:
    m = _cols(coupons)
    a = {k: f"{X.col(v)}{r}" for k, v in m.items()}
    X.put(ws, r, m["key"], key, fill=X.SYSTEM)
    for k, field_ in (("mat", "maturity"), ("eff", "effective_date"), ("settle", "upfront_settle_date"), ("prot", "protection_start"),
                      ("first", "first_period_start")):
        X.put(ws, r, m[k], date.fromisoformat(c[field_]), fmt=X.DATE, fill=X.SYSTEM)
    X.put(ws, r, m["t_prot"], f"=({a['prot']}-VAL)/365", fmt=X.NUM6)
    X.put(ws, r, m["t_mat"], f"=({a['mat']}-VAL)/365", fmt=X.NUM6)
    if c.get("rebate_start"):
        X.put(ws, r, m["reb_s"], date.fromisoformat(c["rebate_start"]), fmt=X.DATE, fill=X.SYSTEM)
        X.put(ws, r, m["reb_e"], date.fromisoformat(c["rebate_end"]), fmt=X.DATE, fill=X.SYSTEM)
        X.put(ws, r, m["reb"], "=" + _yf(day_count, a["reb_s"], a["reb_e"]), fmt=X.NUM6)
    else:
        X.put(ws, r, m["reb"], 0, fmt=X.NUM6)
    X.put(ws, r, m["vprot"], f'=(1-REC)*SUMIFS(P_VALUE,P_KEY,{a["key"]},P_LEG,"защита")', fmt=X.DF9)
    X.put(ws, r, m["rpv"], f"=SUMIF(S_KEY,{a['key']},S_PV)+SUMIF(S_KEY,{a['key']},S_AOD)", fmt=X.DF9)
    X.put(ws, r, m["dfe"], "=" + _df(f"(({a['settle']}-VAL)/365)"), fmt=X.DF9)
    X.put(ws, r, m["rpvc"], f"={a['rpv']}-{a['reb']}*{a['dfe']}", fmt=X.DF9)
    X.put(ws, r, m["par"], f"={a['vprot']}/{a['rpvc']}*10000", fmt=X.BP)
    X.put(ws, r, m["par_s"], c["par_spread"] * BP, fmt=X.BP, fill=X.SYSTEM)
    X.put(ws, r, m["par_c"], f"={a['par']}-{a['par_s']}", fmt=X.TINY, fill=X.CHECK)
    for cp in coupons:
        X.put(ws, r, m[f"puf{cp}"], f"=(({a['vprot']}-{cp}*{a['rpv']})/{a['dfe']}+{cp}*{a['reb']})*100", fmt="0.0000")
        X.put(ws, r, m[f"puf_s{cp}"], c["puf"][f"{round(cp * BP)}"] * 100, fmt="0.0000", fill=X.SYSTEM)
        X.put(ws, r, m[f"puf_c{cp}"], f"={a[f'puf{cp}']}-{a[f'puf_s{cp}']}", fmt=X.TINY, fill=X.CHECK)
    X.put(ws, r, m["q"], "=" + _q(a["t_mat"]), fmt=X.DF9)
    X.put(ws, r, m["q_s"], c["survival"], fmt=X.DF9, fill=X.SYSTEM)
    return a


def build_curve(snap: dict[str, Any]) -> bytes:
    res, inputs = snap["result"], snap["inputs"]
    valuation = date.fromisoformat(res["valuation_date"])
    conv = inputs.get("conventions", {})
    coupons = list(res["coupons"])
    source = "текущая кривая" if snap["kind"] == "state" else f"история, версия {snap.get('version')}"
    book = _Detail(f"CDS-кривая {snap['entity_code']} на {valuation:%d.%m.%Y}: детализация расчёта",
                   f"{snap.get('entity_name', '')} · {source} · {snap.get('trigger_title', '')} · выгружено {datetime.now(timezone.utc):%d.%m.%Y %H:%M} UTC",
                   valuation, res["recovery"], res["hazard"], inputs["discount"]["nodes"], conv.get("day_count", "ACT/ACT.ISDA"))
    ws = book.summary
    X.kv(ws, 5, "Контрольное лицо", snap["entity_code"])
    X.kv(ws, 6, "Кредитная кривая (облигации)", f"{inputs['credit_curve']['code']} на {inputs['credit_curve']['as_of_date']}",
         comment="Hazard rate по облигациям эмитента: из неё считаются модельные par spread стандартных контрактов.")
    X.kv(ws, 7, "Кривая дисконтирования", f"{inputs['discount']['code']} от {inputs['discount']['as_of_date']}")
    X.kv(ws, 8, "Recovery R", res["recovery"], fmt="0.00%")
    X.kv(ws, 9, "Rq (конверсия QS)", res["recovery_quote"], fmt="0.00%", comment="Параметр конвенции котирования QS, не оценка восстановления.")
    X.kv(ws, 10, "Конвенции", f"{conv.get('name')}, купон {conv.get('day_count')}, календарь {conv.get('calendar')}, ребейт: {conv.get('rebate_mode')}")
    X.put(ws, 12, 1, "Как построена кривая: модельные par spread стандартных контрактов на кредитной кривой + базис b(T) → последовательный "
                     "бутстрап λ по возрастанию сроков (столбцы ниже). Excel проверяет, что найденная λ даёт эти par spread и PUF.", font=X.MUTED)
    h = 14
    cols = _summary_header(ws, h, coupons)
    n_cols = len(cols)
    X.put(ws, h, n_cols + 1, "Par по облигациям (система), б.п.", fill=X.HEADER, font=X.HEADER_FONT)
    X.put(ws, h, n_cols + 2, "Базис, б.п.", fill=X.HEADER, font=X.HEADER_FONT)
    X.put(ws, h, n_cols + 3, "QS (система), б.п.", fill=X.HEADER, font=X.HEADER_FONT,
          comment="QS: спред, при котором PUF = 0 на ПЛОСКОЙ кривой с Rq; ищется численно (Брент) — справочная величина.")
    X.put(ws, h, n_cols + 4, "Плоская λ для QS (система)", fill=X.HEADER, font=X.HEADER_FONT)
    for i, row in enumerate(res["table"]):
        book.add_contract(row["tenor"], row["schedule"], date.fromisoformat(row["protection_start"]), date.fromisoformat(row["maturity"]))
        r = h + 1 + i
        _summary_row(ws, r, row["tenor"], row, coupons, conv.get("day_count", "ACT/ACT.ISDA"))
        X.put(ws, r, n_cols + 1, row["par_bond_bp"], fmt=X.BP, fill=X.SYSTEM)
        X.put(ws, r, n_cols + 2, row["basis_bp"], fmt=X.BP, fill=X.SYSTEM)
        X.put(ws, r, n_cols + 3, row["qs_bp"], fmt=X.BP, fill=X.SYSTEM)
        X.put(ws, r, n_cols + 4, row["qs_flat_lambda"], fmt=X.DF9, fill=X.SYSTEM)
    last = h + len(res["table"])
    pc = X.col(cols["par_c"])
    X.check_format(ws, f"{pc}{h + 1}:{pc}{last}", 1e-6)
    for cp in coupons:
        cc = X.col(cols[f"puf_c{cp}"])
        X.check_format(ws, f"{cc}{h + 1}:{cc}{last}", 1e-8)
    ws.freeze_panes = ws.cell(row=h + 1, column=2)
    return book.finish()


def build_contract(res: dict[str, Any]) -> bytes:
    valuation = date.fromisoformat(res["curve_date"])
    conv = res.get("conventions", {})
    book = _Detail(f"CDS {res['entity_code']}: контракт до {date.fromisoformat(res['maturity']):%d.%m.%Y}",
                   f"Профиль {res['profile']} · кривая на {valuation:%d.%m.%Y} · выгружено {datetime.now(timezone.utc):%d.%m.%Y %H:%M} UTC",
                   valuation, res["recovery"], res["hazard"], res["discount_nodes"], conv.get("day_count", "ACT/ACT.ISDA"))
    ws = book.summary
    X.kv(ws, 5, "Номинал N", res["notional"], fmt="#,##0")
    X.name(book.wb, "NOTIONAL", X.ref(ws, 2, 5))
    X.kv(ws, 6, "Купон c", res["coupon_bp"] / BP, fmt="0.0000%")
    X.name(book.wb, "CPN", X.ref(ws, 2, 6))
    X.kv(ws, 7, "Сторона", "покупатель защиты" if res["side"] == "buyer" else "продавец защиты")
    X.kv(ws, 8, "Знак", 1 if res["side"] == "buyer" else -1, comment="+1 покупатель, −1 продавец: все величины продавца = величины покупателя с минусом.")
    X.name(book.wb, "SIGN", X.ref(ws, 2, 8))
    src = res.get("quote_input")
    derived = "выведен из QS по конвенции ISDA (плоская λ, Rq)"
    X.kv(ws, 9, "Upfront сделки, % (PUF)", res.get("upfront_pct"), fmt="0.0000",
         comment=(derived[0].upper() + derived[1:] + "." if src == "qs" else "Введён пользователем.") if src else "Цена сделки не задана.")
    X.kv(ws, 10, "Конвенции", f"{conv.get('name')}, купон {conv.get('day_count')}, календарь {conv.get('calendar')}")
    if res.get("trade_qs_bp") is not None:
        X.kv(ws, 11, "QS сделки, б.п.", res["trade_qs_bp"], fmt=X.BP,
             comment=("Введён пользователем; PUF " + derived.replace("выведен из QS", "выведен из него") if src == "qs" else
                      "Выведен из PUF по конвенции ISDA: плоская λ с Rq, при которой PUF при купоне c равен upfront сделки; QS — par spread этой кривой."))
        X.kv(ws, 12, "Плоская λ котировки (Rq)", res["trade_flat_lambda"], fmt=X.DF9,
             comment=f"Плоская интенсивность с Rq = {res.get('recovery_quote', 0.4):.0%}: на ней QS — par spread, а PUF — upfront при купоне c. "
                     "Ищется численно (Брент) — справочная величина.")
    h = 13
    cpn = res["coupon_bp"] / BP
    _summary_header(ws, h, [cpn])
    book.add_contract("контракт", res["schedule"], date.fromisoformat(res["protection_start"]), date.fromisoformat(res["maturity"]))
    a = _summary_row(ws, h + 1, "контракт", {**res, "puf": {f"{round(res['coupon_bp'])}": res["puf_model"]}}, [cpn],
                     conv.get("day_count", "ACT/ACT.ISDA"))
    r = h + 4
    X.put(ws, r, 1, "Итог сделки", bold=True)
    pv = f"({a['vprot']}-CPN*{a['rpv']})"
    items = [
        ("PV покупателя на ед. номинала", f"={pv}", res["pv_dirty"], "V_prot − c·RPV01 грязный (полный текущий купон и AoD)."),
        ("Ребейт, ₽", f"=-CPN*NOTIONAL*{a['reb']}", res["rebate_amount"], "−c·N·α_reb; платит продавец защиты (только СПФИ)."),
        ("Первоначальный платёж по модели, ₽", f"={pv}/{a['dfe']}*NOTIONAL", res["initial_payment_model"],
         "PUF·N + ребейт = PV / DF(t_E) · N; плюс — платит покупатель."),
        ("NPV стороны, ₽", (f"=SIGN*NOTIONAL*{a['dfe']}*({pv}/{a['dfe']}+CPN*{a['reb']}-B9/100)" if res.get("upfront_pct") is not None
                            else f"=SIGN*NOTIONAL*{pv}"), res["npv"],
         "С upfront: знак·N·DF(t_E)·(PUF модели − PUF сделки). Без upfront (Сбер): знак·N·PV."),
    ]
    for j, (label, formula, system, comment) in enumerate(items):
        rr = r + 1 + j
        money = "₽" in label
        X.put(ws, rr, 1, label, comment=comment)
        X.put(ws, rr, 2, formula, fmt="#,##0.00" if money else X.DF9)
        X.put(ws, rr, 3, system, fmt="#,##0.00" if money else X.DF9, fill=X.SYSTEM)
        X.put(ws, rr, 4, f"=B{rr}-C{rr}", fmt=X.TINY, fill=X.CHECK)
    X.check_format(ws, f"D{r + 1}:D{r + len(items)}", 1e-6)
    X.put(ws, r + len(items) + 2, 1, f"QS (система): {res['qs_bp']:.2f} б.п.; плоская λ для QS: {res['qs_flat_lambda']:.9f} "
                                    "(QS — спред плоской кривой с Rq, при котором PUF = 0; ищется численно).", font=X.MUTED)
    for j, w in enumerate(res.get("warnings", [])):
        X.put(ws, r + len(items) + 3 + j, 1, w, font=X.MUTED)
    return book.finish()


def build_daily(as_of: date, rows: list[tuple[Any, dict[str, Any]]], codes: dict) -> bytes:
    wb = Workbook()
    info = wb.active
    info.title = "Инфо"
    X.put(info, 1, 1, f"Итоги дня CDS на {as_of:%d.%m.%Y}", font=X.TITLE_FONT)
    X.put(info, 2, 1, f"Сформировано {datetime.now(timezone.utc):%d.%m.%Y %H:%M} UTC. Все величины — с позиции покупателя защиты. "
                      "Если на дату у контрольного лица нет CDS-кривой, берётся последняя более ранняя — её дата в столбце valuation_date.",
          font=X.MUTED)
    lines = [
        ("Дисконт-факторы", "DF от даты кривой до даты узла; ставка — непрерывная бескупонная ACT/365F."),
        ("Дожитие", "Помесячно до максимального срока + 1 год: вероятность дожития и hazard rate CDS-кривой (по ней считаются котировки) и кредитной кривой по облигациям; RR."),
        ("CDS-котировки", "По стандартным срокам: par spread, QS, PUF при стандартных купонах (% номинала, плюс — платит покупатель), выживание, RPV01."),
    ]
    for i, (sheet, text) in enumerate(lines):
        X.put(info, 4 + i, 1, sheet, bold=True)
        X.put(info, 4 + i, 2, text)
    info.column_dimensions["A"].width, info.column_dimensions["B"].width = 18, 120

    ws = wb.create_sheet("Дисконт-факторы")
    ws.append(["curve_code", "curve_as_of", "valuation_date", "node_date", "days", "df", "zero_rate_cc_pct"])
    seen = set()
    for entity, snap in rows:
        d = snap["inputs"]["discount"]
        key = (d["code"], snap["as_of_date"])
        if key in seen:
            continue
        seen.add(key)
        val = date.fromisoformat(snap["result"]["valuation_date"])
        for n in d["nodes"]:
            nd = date.fromisoformat(n["date"])
            days = (nd - val).days
            ws.append([d["code"], d["as_of_date"], val, nd, days, n["df"], -math.log(n["df"]) / (days / 365) * 100 if days > 0 else None])

    ws = wb.create_sheet("Дожитие")
    ws.append(["entity_code", "valuation_date", "node_date", "months", "t", "cds_survival", "cds_hazard", "cds_pd",
               "bond_survival", "bond_hazard", "recovery", "credit_curve"])
    for entity, snap in rows:
        res = snap["result"]
        for m in res["monthly"]:
            ws.append([entity.code, res["valuation_date"], m["date"], m["months"], m["t"], m["survival"], m["hazard"], 1 - m["survival"],
                       m["bond_survival"], m["bond_hazard"], res["recovery"], snap["inputs"]["credit_curve"]["code"]])

    ws = wb.create_sheet("CDS-котировки")
    ws.append(["entity_code", "entity_name", "valuation_date", "tenor", "maturity", "effective_date", "par_spread_bp", "qs_bp",
               "puf_100bp_pct", "puf_500bp_pct", "puf_standard_coupon_pct", "standard_coupon_bp", "survival", "rpv01_clean",
               "hazard_segment", "recovery", "recovery_quote", "basis_bp", "par_from_bonds_bp", "rebate_year_frac", "dropped",
               "calculated_at", "credit_curve", "discount_curve"])
    for entity, snap in rows:
        res, inp = snap["result"], snap["inputs"]
        std = str(entity.standard_coupon_bp)
        for t in res["table"]:
            ws.append([entity.code, entity.name, res["valuation_date"], t["tenor"], t["maturity"], t["effective_date"], t["par_spread_bp"],
                       t["qs_bp"], t["puf"].get("100", 0) * 100 if "100" in t["puf"] else None, t["puf"].get("500", 0) * 100 if "500" in t["puf"] else None,
                       t["puf"].get(std, 0) * 100 if std in t["puf"] else None, entity.standard_coupon_bp, t["survival"], t["rpv01_clean"],
                       t["lambda"], res["recovery"], res["recovery_quote"], t["basis_bp"], t["par_bond_bp"], t["rebate_year_frac"], t["dropped"],
                       str(snap["saved_at"]), f"{inp['credit_curve']['code']}@{inp['credit_curve']['as_of_date']}",
                       f"{inp['discount']['code']}@{inp['discount']['as_of_date']}"])
    for sheet in wb.worksheets[1:]:
        sheet.freeze_panes = "A2"
        for c in sheet[1]:
            c.font = X.BOLD
            sheet.column_dimensions[c.column_letter].width = max(12, len(str(c.value)) + 2)
    return X.save(wb)
