"""Excel file with the calculation detail of an interest-rate (OIS) curve.

Two sheets, both with live formulas:
* «Кривая»  — nodes of the built curve (dates, DF) and what Excel derives from them: t, ln DF,
  zero and forward rates (compared with the system); a calculator of log-linear interpolation;
* «Свопы»   — every quoted swap: the modelled fixed-leg payment schedule, DF at each payment
  interpolated from «Кривая», the annuity and the par rate (1 − DF(T)) / Σ τ·DF, compared with the
  model par rate of the system and with the market mid.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from dateutil.relativedelta import relativedelta
from openpyxl import Workbook

from cds_common import xlsx as X

from .domain.conventions import DayCount, SwapConventions, Tenor

PARAM_TITLES = {
    "smoothness": ("Гладкость λ", "Вес штрафа за кривизну форвардов: больше λ — глаже кривая и дальше скорректированные миды от рынка."),
    "bid_ask_penalty": ("Штраф за выход за бид-аск κ", None),
    "max_avg_deviation_bp": ("Предельное среднее отклонение, б.п.", None),
    "max_point_deviation_bp": ("Предельное отклонение точки, б.п.", None),
    "long_end_flat_from_months": ("Плоский конец с, мес.", "Фактическое значение (если в параметрах «авто» — срок предпоследней котировки)."),
    "long_end_flatness": ("Вес плоского дальнего конца μ", None),
    "short_end_months": ("Короткий конец, мес.", None),
    "short_end_weight": ("Вес гладкости короткого конца", None),
}
SOURCE_TITLES = {"state": "текущая кривая", "history": "историческая версия", "calculation": "несохранённый расчёт"}


def build(snap: dict[str, Any]) -> bytes:
    as_of = X.to_date(snap["as_of_date"])
    conv_d = snap["conventions"]
    conv = SwapConventions(DayCount(conv_d["day_count"]), int(conv_d["fixed_frequency_months"]), int(conv_d["single_payment_max_months"]))
    basis = 360 if conv.day_count is DayCount.ACT_360 else 365
    result = snap["result"]
    nodes, fitted, stats = result["nodes"], result["fitted"], result.get("stats", {})
    params = result.get("params", {})

    wb = Workbook()
    ws = wb.active
    ws.title = "Кривая"
    sw = wb.create_sheet("Свопы")

    source = SOURCE_TITLES.get(snap.get("kind", ""), snap.get("kind", ""))
    if snap.get("version"):
        source += f" {snap['version']}"
    who = f", сохранил {snap['author']} {snap['saved_at']:%d.%m.%Y %H:%M}" if snap.get("author") and isinstance(snap.get("saved_at"), datetime) else ""
    subtitle = (f"{snap.get('curve_name', '')} · {source}{who} · выгружено {datetime.now(timezone.utc):%d.%m.%Y %H:%M} UTC")
    X.title(ws, f"Процентная кривая {snap['curve_code']} на {as_of:%d.%m.%Y}: детализация расчёта", subtitle)

    # ------------------------------------------------------------------ parameters
    r = 5
    c = X.kv(ws, r, "Дата кривой D", as_of, fmt=X.DATE, comment="Дата построения кривой; t = (дата − D) / база дней.")
    X.name(wb, "AS_OF", X.ref(ws, 2, r))
    X.kv(ws, r + 1, "Конвенция дней", conv.day_count.value)
    X.kv(ws, r + 2, "База дней", basis, comment="ACT/365F → 365, ACT/360 → 360. Используется в t и в длительности периодов τ.")
    X.name(wb, "BASIS", X.ref(ws, 2, r + 2))
    X.kv(ws, r + 3, "Одна выплата до, мес.", conv.single_payment_max_months,
         comment="Свопы со сроком до этого значения включительно — одна выплата фиксированной ноги в дату погашения "
                 "(как инструменты денежного рынка): ставка = (1/DF(T) − 1) / τ.")
    X.kv(ws, r + 4, "Фиксированная нога, каждые N мес.", conv.fixed_frequency_months,
         comment="Длиннее — выплаты каждые N месяцев, отсчитанные назад от даты погашения; первый период короткий (stub). "
                 "Плавающая нога (накопленная овернайт-ставка) стоит 1 − DF(T), поэтому пар-ставка = (1 − DF(T)) / Σ τᵢ·DF(tᵢ).")
    row = r + 5
    for key, (label, comment) in PARAM_TITLES.items():
        if key in params:
            X.kv(ws, row, label, params[key], comment=comment)
            row += 1
    X.put(ws, row + 1, 1, "Кривая не подбирается в Excel: оптимизатор системы находит форварды (см. docs/curves-math.md). "
                          "Файл проверяет, что найденная кривая даёт те модельные ставки, которые показывает система.",
          font=X.MUTED)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 14

    # ------------------------------------------------------------------ calculator
    cc = 4  # column D
    X.put(ws, 5, cc, "Калькулятор интерполяции", bold=True,
          comment="Меняйте жёлтые даты: Excel найдёт DF, бескупонную и форвардную ставки по узлам кривой так же, как система.")
    X.put(ws, 6, cc, "Дата")
    X.put(ws, 6, cc + 1, as_of + relativedelta(years=1), fmt=X.DATE, fill=X.INPUT)
    X.put(ws, 7, cc, "t, лет")
    X.put(ws, 7, cc + 1, "=(E6-AS_OF)/BASIS", fmt=X.NUM6)
    X.put(ws, 8, cc, "Отрезок k")
    X.put(ws, 8, cc + 1, "=" + X.segment("E7", "T_NODES"),
          comment="Номер узла, после которого лежит t (MATCH по столбцу t). После последнего узла продолжается последний отрезок.")
    X.put(ws, 9, cc, "ln DF")
    X.put(ws, 9, cc + 1, "=" + X.loglin("E7", "E8", "T_NODES", "LNDF"), fmt=X.DF9,
          comment="Лог-линейная интерполяция: ln DF линеен по t между узлами k и k+1 ⇔ мгновенный форвард постоянен внутри отрезка.")
    X.put(ws, 10, cc, "DF")
    X.put(ws, 10, cc + 1, "=EXP(E9)", fmt=X.DF9)
    X.put(ws, 11, cc, "ZC непрерывная, %")
    X.put(ws, 11, cc + 1, '=IF(E7<=0,"",-E9/E7*100)', fmt=X.PCT4)
    X.put(ws, 12, cc, "ZC годовая капитализация, %")
    X.put(ws, 12, cc + 1, '=IF(E7<=0,"",(E10^(-1/E7)-1)*100)', fmt=X.PCT4)
    X.put(ws, 13, cc, "Начало форварда")
    X.put(ws, 13, cc + 1, as_of + relativedelta(months=6), fmt=X.DATE, fill=X.INPUT)
    X.put(ws, 14, cc, "t начала")
    X.put(ws, 14, cc + 1, "=(E13-AS_OF)/BASIS", fmt=X.NUM6)
    X.put(ws, 15, cc, "k начала")
    X.put(ws, 15, cc + 1, "=" + X.segment("E14", "T_NODES"))
    X.put(ws, 16, cc, "ln DF начала")
    X.put(ws, 16, cc + 1, "=" + X.loglin("E14", "E15", "T_NODES", "LNDF"), fmt=X.DF9)
    X.put(ws, 17, cc, "Форвард простой, %")
    X.put(ws, 17, cc + 1, '=IF(E7<=E14,"",(EXP(E16-E9)-1)/(E7-E14)*100)', fmt=X.PCT4,
          comment="Простая ставка между датой начала и датой выше: (DF(начало)/DF(конец) − 1) / τ.")
    X.put(ws, 18, cc, "Форвард непрерывный, %")
    X.put(ws, 18, cc + 1, '=IF(E7<=E14,"",(E16-E9)/(E7-E14)*100)', fmt=X.PCT4)
    ws.column_dimensions["D"].width = 30
    ws.column_dimensions["E"].width = 16

    # ------------------------------------------------------------------ nodes
    h = 21
    X.header(ws, h, [
        ("№", 6, None),
        ("Дата узла", 12, "Узлы кривой: помесячно от даты кривой и дополнительно — сроки котировок между месяцами (например, 1W)."),
        ("Мес.", 6, "Пусто — дополнительный (не помесячный) узел."),
        ("t, лет", 12, "Формула: (дата − D) / база дней."),
        ("DF (система)", 15, "Фактор дисконтирования в узле — результат построения кривой. Всё остальное считается из этого столбца."),
        ("ln DF", 15, "Формула: LN(DF). По этому столбцу идёт интерполяция."),
        ("ZC непр., %", 12, "Формула: −ln DF / t · 100."),
        ("ZC год., %", 12, "Формула: (DF^(−1/t) − 1) · 100 — годовая капитализация."),
        ("Форвард на отрезке, %", 13, "Формула: −(ln DFᵢ − ln DFᵢ₋₁) / (tᵢ − tᵢ₋₁) · 100 — постоянный непрерывный форвард на отрезке, заканчивающемся в узле."),
        ("ZC (система), %", 12, None),
        ("Форвард (система), %", 13, None),
        ("Пар-кривая (система), %", 13, "Пар-кривая на экране — скорректированные миды, линейно интерполированные между тенорами (не выводится из DF)."),
        ("Сверка ZC, б.п.", 11, "(ZC Excel − ZC системы) · 100."),
        ("Сверка форварда, б.п.", 11, "(форвард Excel − форвард системы) · 100."),
    ])
    first = h + 1
    for i, n in enumerate(nodes):
        rr = first + i
        X.put(ws, rr, 1, i)
        X.put(ws, rr, 2, X.to_date(n["date"]), fmt=X.DATE, fill=X.SYSTEM)
        X.put(ws, rr, 3, n.get("months"), fill=X.SYSTEM)
        X.put(ws, rr, 4, f"=(B{rr}-AS_OF)/BASIS", fmt=X.NUM6)
        X.put(ws, rr, 5, float(n["df"]), fmt=X.DF9, fill=X.SYSTEM)
        X.put(ws, rr, 6, f"=LN(E{rr})", fmt=X.DF9)
        X.put(ws, rr, 7, f'=IF(D{rr}=0,"",-F{rr}/D{rr}*100)', fmt=X.PCT4)
        X.put(ws, rr, 8, f'=IF(D{rr}=0,"",(E{rr}^(-1/D{rr})-1)*100)', fmt=X.PCT4)
        X.put(ws, rr, 9, "" if i == 0 else f"=-(F{rr}-F{rr - 1})/(D{rr}-D{rr - 1})*100", fmt=X.PCT4)
        X.put(ws, rr, 10, n.get("zero"), fmt=X.PCT4, fill=X.SYSTEM)
        X.put(ws, rr, 11, n.get("forward"), fmt=X.PCT4, fill=X.SYSTEM)
        X.put(ws, rr, 12, n.get("par"), fmt=X.PCT4, fill=X.SYSTEM)
        X.put(ws, rr, 13, 0 if i == 0 else f"=(G{rr}-J{rr})*100", fmt=X.TINY, fill=X.CHECK)
        X.put(ws, rr, 14, 0 if i == 0 else f"=(I{rr}-K{rr})*100", fmt=X.TINY, fill=X.CHECK)
    last = first + len(nodes) - 1
    X.name(wb, "T_NODES", X.rng(ws, 4, first, last))
    X.name(wb, "LNDF", X.rng(ws, 6, first, last))
    X.check_format(ws, f"M{first}:N{last}", 1e-6)
    ws.freeze_panes = ws.cell(row=first, column=3)

    _swaps_sheet(wb, sw, snap, as_of, conv, fitted, stats)
    return X.save(wb)


def _swaps_sheet(wb: Workbook, ws, snap: dict[str, Any], as_of: date, conv: SwapConventions, fitted: list[dict[str, Any]],
                 stats: dict[str, Any]) -> None:
    X.title(ws, f"Свопы {snap['curve_code']}: графики выплат и модельные пар-ставки",
            "DF в датах выплат интерполируются из листа «Кривая»; пар-ставка = (1 − DF(T)) / Σ τᵢ·DF(tᵢ)")
    schedules = []
    for f in fitted:
        tenor = Tenor.parse(f["tenor"])
        pays = conv.payment_dates(as_of, tenor)
        schedules.append((f["tenor"], [as_of] + pays[:-1], pays))
    n = len(fitted)
    h = 6
    sched_header = h + n + 8
    sched_first = sched_header + 1
    sched_last = sched_first + sum(len(p) for _, _, p in schedules) - 1
    tenors = X.rng(ws, 1, sched_first, sched_last)
    tau_df = X.rng(ws, 10, sched_first, sched_last)

    X.header(ws, h, [
        ("Тенор", 8, None),
        ("Дата погашения T", 12, "Дата погашения = дата кривой + тенор (без переноса выходных)."),
        ("Бид, %", 9, "Котировки, введённые оператором."),
        ("Аск, %", 9, None),
        ("Мид рынка, %", 10, None),
        ("Выплат фикс. ноги", 9, "Формула: COUNTIF по графику ниже."),
        ("t(T), лет", 10, "Формула: (T − D) / база дней. Для свопа с одной выплатой это и есть τ."),
        ("k", 5, "Отрезок кривой для интерполяции DF(T)."),
        ("ln DF(T)", 13, "Лог-линейная интерполяция по листу «Кривая»."),
        ("DF(T)", 13, "Формула: EXP(ln DF(T))."),
        ("Аннуитет Σ τ·DF", 13, "Формула: SUMIF по графику выплат ниже — сумма τᵢ·DF(tᵢ) по выплатам фиксированной ноги."),
        ("Пар-ставка Excel, %", 12, "Одна выплата: (1/DF(T) − 1)/τ · 100. Несколько: (1 − DF(T)) / аннуитет · 100. "
                                    "1 − DF(T) — стоимость плавающей ноги (накопленная овернайт-ставка)."),
        ("Пар-ставка системы, %", 12, "Модельная ставка свопа на построенной кривой = скорректированный мид (на графике — ромб)."),
        ("Сверка, б.п.", 10, "(Excel − система) · 100."),
        ("Отклонение от мида, б.п.", 11, "(модель − мид рынка) · 100: насколько кривая отошла от котировки ради гладкости."),
        ("Полуширина бид-аск, б.п.", 11, "(аск − бид) / 2 · 100."),
        ("Внутри бид-аск", 9, None),
    ])
    for i, f in enumerate(fitted):
        r = h + 1 + i
        X.put(ws, r, 1, f["tenor"], fill=X.SYSTEM)
        X.put(ws, r, 2, X.to_date(f["maturity"]), fmt=X.DATE, fill=X.SYSTEM)
        X.put(ws, r, 3, f["bid"], fmt=X.PCT4, fill=X.SYSTEM)
        X.put(ws, r, 4, f["ask"], fmt=X.PCT4, fill=X.SYSTEM)
        X.put(ws, r, 5, f["mid"], fmt=X.PCT4, fill=X.SYSTEM)
        X.put(ws, r, 6, f"=COUNTIF({tenors},A{r})")
        X.put(ws, r, 7, f"=(B{r}-AS_OF)/BASIS", fmt=X.NUM6)
        X.put(ws, r, 8, "=" + X.segment(f"G{r}", "T_NODES"))
        X.put(ws, r, 9, "=" + X.loglin(f"G{r}", f"H{r}", "T_NODES", "LNDF"), fmt=X.DF9)
        X.put(ws, r, 10, f"=EXP(I{r})", fmt=X.DF9)
        X.put(ws, r, 11, f"=SUMIF({tenors},A{r},{tau_df})", fmt=X.DF9)
        X.put(ws, r, 12, f"=IF(F{r}=1,(1/J{r}-1)/G{r},(1-J{r})/K{r})*100", fmt=X.PCT4)
        X.put(ws, r, 13, f["model"], fmt=X.PCT4, fill=X.SYSTEM)
        X.put(ws, r, 14, f"=(L{r}-M{r})*100", fmt=X.TINY, fill=X.CHECK)
        X.put(ws, r, 15, f"=(M{r}-E{r})*100", fmt=X.BP)
        X.put(ws, r, 16, f"=(D{r}-C{r})/2*100", fmt=X.BP)
        X.put(ws, r, 17, f'=IF(AND(M{r}>=C{r}-1E-9,M{r}<=D{r}+1E-9),"да","нет")')
    last = h + n
    X.check_format(ws, f"N{h + 1}:N{last}", 1e-6)

    # statistics of the fit (as on the screen)
    s = last + 2
    X.put(ws, s, 1, "Статистика подгонки", bold=True)
    X.put(ws, s, 6, "Excel", bold=True)
    X.put(ws, s, 7, "Система", bold=True)
    X.put(ws, s, 8, "Сверка", bold=True)
    rows = [
        ("Среднее |отклонение от мида|, б.п.", f'=(SUMIF(O{h + 1}:O{last},">0")-SUMIF(O{h + 1}:O{last},"<0"))/{n}', stats.get("mean_abs_deviation_bp")),
        ("Максимальное |отклонение|, б.п.", f"=MAX(MAX(O{h + 1}:O{last}),-MIN(O{h + 1}:O{last}))", stats.get("max_abs_deviation_bp")),
        ("Среднеквадратичное отклонение, б.п.", f"=SQRT(SUMPRODUCT(O{h + 1}:O{last},O{h + 1}:O{last})/{n})", stats.get("rms_deviation_bp")),
    ]
    for j, (label, formula, system) in enumerate(rows):
        r = s + 1 + j
        X.put(ws, r, 1, label)
        X.put(ws, r, 6, formula, fmt=X.BP)
        X.put(ws, r, 7, system, fmt=X.BP, fill=X.SYSTEM)
        X.put(ws, r, 8, f"=F{r}-G{r}", fmt=X.TINY, fill=X.CHECK)
    X.check_format(ws, f"H{s + 1}:H{s + len(rows)}", 1e-6)

    # payment schedules
    X.put(ws, sched_header - 1, 1, "Графики выплат фиксированной ноги", bold=True,
          comment="Для каждого свопа: периоды от даты кривой. До «одна выплата до» — одна выплата в дату погашения; "
                  "дальше — каждые N месяцев назад от даты погашения, первый период короткий.")
    X.header(ws, sched_header, [
        ("Тенор", 8, None),
        ("№ выплаты", 8, None),
        ("Начало периода", 12, "Дата кривой для первого периода, далее — предыдущая дата выплаты."),
        ("Дата выплаты", 12, None),
        ("τ периода", 10, "Формула: (дата выплаты − начало) / база дней."),
        ("t выплаты, лет", 10, "Формула: (дата выплаты − D) / база дней."),
        ("k", 5, None),
        ("ln DF", 13, "Лог-линейная интерполяция по листу «Кривая»."),
        ("DF выплаты", 13, "Формула: EXP(ln DF)."),
        ("τ·DF", 13, "Вклад выплаты в аннуитет."),
    ])
    r = sched_first
    for tenor, starts, pays in schedules:
        for j, (start, pay) in enumerate(zip(starts, pays), start=1):
            X.put(ws, r, 1, tenor, fill=X.SYSTEM)
            X.put(ws, r, 2, j)
            X.put(ws, r, 3, start, fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, 4, pay, fmt=X.DATE, fill=X.SYSTEM)
            X.put(ws, r, 5, f"=(D{r}-C{r})/BASIS", fmt=X.NUM6)
            X.put(ws, r, 6, f"=(D{r}-AS_OF)/BASIS", fmt=X.NUM6)
            X.put(ws, r, 7, "=" + X.segment(f"F{r}", "T_NODES"))
            X.put(ws, r, 8, "=" + X.loglin(f"F{r}", f"G{r}", "T_NODES", "LNDF"), fmt=X.DF9)
            X.put(ws, r, 9, f"=EXP(H{r})", fmt=X.DF9)
            X.put(ws, r, 10, f"=E{r}*I{r}", fmt=X.DF9)
            r += 1
    ws.freeze_panes = ws.cell(row=h + 1, column=2)
