/** Presentation of a discount curve in different compounding conventions (rates in percent). */
import type { Compounding, CurveNode, RateFormat } from './types'

const PERIODS: Record<Exclude<Compounding, 'continuous'>, number> = { annual: 1, semiannual: 2, quarterly: 4, monthly: 12 }

export const FORMAT_LABELS: Record<RateFormat, string> = {
  continuous: 'Непрерывная капитализация',
  annual: 'Годовая капитализация',
  semiannual: 'Полугодовая',
  quarterly: 'Ежеквартальная',
  monthly: 'Ежемесячная',
  df: 'Фактор дисконтирования',
}

export const COMPOUNDINGS: Compounding[] = ['continuous', 'annual', 'semiannual', 'quarterly', 'monthly']

/** Rate (percent) that discounts 1 to df over t years. */
export function rateFromDf(df: number, t: number, comp: Compounding): number {
  if (t <= 0) return NaN
  if (comp === 'continuous') return (-Math.log(df) / t) * 100
  const m = PERIODS[comp]
  return m * (Math.pow(df, -1 / (m * t)) - 1) * 100
}

/** Zero curve points [date, value]; value is DF for the 'df' format. */
export function zeroSeries(nodes: CurveNode[], fmt: RateFormat): [string, number][] {
  if (fmt === 'df') return nodes.map((n) => [n.date, n.df])
  return nodes.filter((n) => n.t > 0).map((n) => [n.date, rateFromDf(n.df, n.t, fmt)])
}

/**
 * Forward rates of the curve segments as step points: the value of segment (t_{j-1}, t_j]
 * is placed at t_{j-1} and drawn with step:'end'. For the 'df' format forwards are continuous.
 */
export function forwardSeries(nodes: CurveNode[], fmt: RateFormat): [string, number][] {
  const comp: Compounding = fmt === 'df' ? 'continuous' : fmt
  const out: [string, number][] = []
  for (let j = 1; j < nodes.length; j++) {
    const a = nodes[j - 1]
    const b = nodes[j]
    out.push([a.date, rateFromDf(b.df / a.df, b.t - a.t, comp)])
  }
  if (out.length) out.push([nodes[nodes.length - 1].date, out[out.length - 1][1]])
  return out
}

export function forwardOfNode(nodes: CurveNode[], j: number, fmt: RateFormat): number | null {
  if (j === 0) return null
  const comp: Compounding = fmt === 'df' ? 'continuous' : fmt
  return rateFromDf(nodes[j].df / nodes[j - 1].df, nodes[j].t - nodes[j - 1].t, comp)
}

export function zeroOfNode(n: CurveNode, fmt: RateFormat): number | null {
  if (fmt === 'df') return n.df
  return n.t > 0 ? rateFromDf(n.df, n.t, fmt) : null
}
