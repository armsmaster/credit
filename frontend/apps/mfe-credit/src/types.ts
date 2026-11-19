export type ObsKind = 'dema' | 'bid' | 'ask' | 'last' | 'vwap'
export type BondKind = 'fixed' | 'floating' | 'linker'
export type RateIndex = 'KEYRATE' | 'RUONIA'

export interface Params {
  recovery_rate: number
  smoothness: number
  long_end_flatness: number
  long_end_flat_from_months: number | null
  secondary_weight: number
  short_bond_years: number
}

export interface Settings {
  index_curves: Record<RateIndex, string>
  liquidity: { window_days: number; min_avg_turnover: number; min_avg_trades: number }
  dema: { half_life_hours: number | null; lookback_days: number }
  dema_auto?: {
    half_life_hours: number
    relative_mse: number | null
    computed_at: string
    bonds: Record<string, { half_life: number; acf1: number | null; candles: number; pooled: boolean }>
  }
}

export interface Meta {
  today: string
  default_params: Params
  kinds: Record<BondKind, string>
  indices: RateIndex[]
  observation_kinds: Record<string, string>
  settings: Settings
}

export interface Issuer {
  emitter_id: number
  title: string
  short_title: string
  inn: string | null
  url: string | null
  added_by: string
  added_at: string
  scan_status: string | null
  scan_error: string | null
  scanned_at: string | null
  bonds_total: number
  bonds_eligible: number
  bonds_recommended: number
  bonds_incomplete: number
  curves: string[]
}

export interface IssSearchItem {
  emitter_id: number
  title: string
  short_title: string | null
  inn: string | null
  tracked: boolean
}

export interface Profile {
  kind: BondKind | null
  coupon_index: RateIndex | null
  spread_pct: number | null
  lag_days: number
  notional_index: RateIndex | null
  fixed_rate_pct: number | null
  mat_date: string | null
  offer_date: string | null
  offer_price: number
  call_date: string | null
  effective_maturity: string | null
  features: string[]
  eligible: boolean
  ineligible: string[]
  complete: boolean
  missing: string[]
  warnings: string[]
}

export interface Liquidity {
  window_days?: number
  trading_days?: number
  days_with_trades?: number
  avg_turnover?: number | null
  p20_turnover?: number | null
  p80_turnover?: number | null
  avg_trades?: number | null
  last_trade_date?: string | null
  avg_spread_pct?: number | null
  spread_days?: number
}

export interface BondManual {
  kind?: BondKind | null
  coupon_index?: RateIndex | null
  spread_pct?: number | null
  lag_days?: number | null
  fixed_rate_pct?: number | null
  notional_index?: RateIndex | null
  offer_date?: string | null
  offer_price?: number | null
  ignore_offer?: boolean
  force_eligible?: boolean
  excluded?: boolean
  dema_half_life_hours?: number | null
  comment?: string
}

export interface Bond {
  secid: string
  isin: string | null
  shortname: string
  name: string
  issuer_id: number
  board: string | null
  is_traded: boolean
  face_unit: string | null
  face_value: number | null
  initial_face: number | null
  coupon_pct: number | null
  list_level: number | null
  profile: Profile
  years_to_maturity: number | null
  liquidity: Liquidity
  liquidity_at: string | null
  recommended: boolean
  notes: string[]
  short: boolean
  manual: BondManual
  manual_updated_by: string | null
  manual_updated_at: string | null
  curves: string[]
  updated_at: string | null
}

export interface BondDetail extends Bond {
  desc: Record<string, string>
  schedule: {
    coupons?: { date: string; start: string | null; record_date: string | null; value: number | null; valueprc: number | null; facevalue: number | null }[]
    amortizations?: { date: string; value: number | null; valueprc: number | null; source: string }[]
    offers?: { date: string; price: number | null; type: string }[]
  }
  daily: Record<string, any>[]
  intraday: Record<string, any>[]
}

export interface Definition {
  id: string
  code: string
  name: string
  issuer_id: number
  issuer_title: string
  discount_curve_code: string
  description: string
  default_params: Partial<Params>
  effective_params: Params
  bonds: { secid: string; weight: number; shortname: string; mat_date: string | null }[]
  is_active: boolean
  created_at: string
  updated_at: string
  current_state: { as_of_date: string; saved_by: string; saved_at: string } | null
  last_history_date: string | null
}

export interface MarketObs {
  secid: string
  prices: Partial<Record<ObsKind, number>>
  face: number | null
  accrued: number | null
  settle_date: string | null
  as_of: string | null
  dema_half_life: number | null
  last_candle: string | null
  source: string
  accrued_estimated?: boolean
}

export interface CalcBond {
  secid: string
  shortname: string
  weight: number
  enabled: boolean
  manual_price: number | null
  excluded_obs: ObsKind[]
  in_composition?: boolean
  kind: BondKind | null
  kind_title?: string
  coupon_index?: RateIndex | null
  spread_pct?: number | null
  features?: string[]
  eligible?: boolean
  complete?: boolean
  problems?: string[]
  maturity: string | null
  to_offer?: boolean
  years: number | null
  short?: boolean
  market: MarketObs
  status?: string
  cashflows?: { date: string; kind: string; amount: number; rate_pct: number | null; projected: boolean }[]
}

export interface Node {
  date: string
  t: number
  months: number
  hazard: number
  avg_hazard: number
  survival: number
  pd: number
  spread_bp: number
}

export interface FittedObs {
  kind: string
  price: number | null
  error_bp: number | null
  weight: number
}

export interface Fitted {
  secid: string
  label: string
  weight: number
  maturity: string
  years: number
  face: number
  accrued: number
  used: boolean
  kind?: string
  short?: boolean
  to_offer?: boolean
  model_price?: number
  target_price?: number
  error_bp?: number
  dv01?: number
  z_spread_model_bp?: number | null
  z_spread_market_bp?: number | null
  observations: FittedObs[]
}

export interface CalcResult {
  as_of_date: string
  params: Params & { long_end_flat_from_months_resolved: number }
  nodes: Node[]
  fitted: Fitted[]
  stats: Record<string, any>
  warnings: string[]
}

export interface CurveRef {
  code: string
  as_of_date: string
  kind: string
  version: number | null
}

export interface CalcInputs {
  discount?: CurveRef
  projection?: Record<string, CurveRef>
  recovery_rate?: number
  warnings: string[]
}

export interface Calculation {
  as_of_date: string
  bonds: CalcBond[]
  inputs: CalcInputs
  result: CalcResult | null
}

export interface Snapshot {
  curve_code: string
  kind: 'state' | 'history'
  as_of_date: string
  version: number | null
  source: string | null
  bonds: CalcBond[]
  params: Partial<Params>
  inputs: CalcInputs
  result: CalcResult
  comment: string
  author: string
  saved_at: string
}

export interface HistoryDate {
  as_of_date: string
  latest_version: number
  versions: number
  source: string
  author: string
  saved_at: string
  edited: boolean
}

export interface Version {
  version: number
  source: string
  author: string
  saved_at: string
  comment: string
}

export interface CompareCurve {
  label: 'day' | 'week' | 'month'
  target_date: string
  as_of_date: string
  kind: string
  version: number | null
  nodes: Node[]
}

export interface RateCurveDef {
  code: string
  name: string
  currency_code: string
}
