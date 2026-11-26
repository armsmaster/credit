import type { Bond } from './types'

export function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)} млрд`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)} млн`
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)} тыс`
  return v.toFixed(0)
}

const AMOUNT_UNITS: [number, string][] = [
  [1e12, 'трлн'],
  [1e9, 'млрд'],
  [1e6, 'млн'],
  [1e3, 'тыс.'],
]

/** A ruble amount in words of its order: 100 млн ₽, 2,5 млрд ₽, ≈ 1,235 млрд ₽ (≈ when digits are rounded off). */
export function amountShort(v: number | null | undefined, unit = '₽'): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return ''
  for (const [scale, name] of AMOUNT_UNITS) {
    if (Math.abs(v) < scale) continue
    const x = v / scale
    const r = Math.round(x * 1000) / 1000
    const approx = Math.abs(r - x) > 1e-9 * Math.max(1, Math.abs(x))
    return `${approx ? '≈ ' : ''}${r.toLocaleString('ru-RU', { maximumFractionDigits: 3 })} ${name} ${unit}`
  }
  return `${v.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ${unit}`
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function signed(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '—'
  return (v > 0 ? '+' : '') + v.toFixed(digits)
}

export const FEATURE_LABELS: Record<string, { label: string; type: '' | 'success' | 'warning' | 'info' | 'danger' }> = {
  fixed: { label: 'фикс', type: 'info' },
  floating: { label: 'флоатер', type: '' },
  linker: { label: 'индекс. номинал', type: 'success' },
  amortizing: { label: 'амортизация', type: 'warning' },
  put: { label: 'оферта put', type: 'warning' },
  call: { label: 'колл', type: 'danger' },
  qualified: { label: 'квал', type: 'info' },
}

export function couponText(b: Pick<Bond, 'profile' | 'coupon_pct'>): string {
  const p = b.profile
  if (p.kind === 'floating') {
    const idx = p.coupon_index === 'KEYRATE' ? 'КС' : p.coupon_index ?? '?'
    return `${idx} + ${p.spread_pct ?? '?'}%`
  }
  if (p.kind === 'linker') return `${b.coupon_pct ?? p.fixed_rate_pct ?? '?'}% · RUONIA`
  return b.coupon_pct !== null && b.coupon_pct !== undefined ? `${b.coupon_pct}%` : '—'
}

export const SOURCE_LABELS: Record<string, string> = { snapshot: 'ночной перенос', auto_archive: 'автоперенос', manual_edit: 'ручное изменение' }
export const OBS_KINDS = ['dema', 'bid', 'ask', 'last', 'vwap'] as const
export const OBS_TITLES: Record<string, string> = { dema: 'DEMA', bid: 'BID', ask: 'ASK', last: 'LAST', vwap: 'VWAP', manual: 'Ручная' }
