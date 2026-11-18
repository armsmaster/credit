export type InputMode = 'bid_ask' | 'mid_spread'
export type RateFormat = 'continuous' | 'annual' | 'semiannual' | 'quarterly' | 'monthly' | 'df'
export type Compounding = Exclude<RateFormat, 'df'>

export interface Params {
  max_avg_deviation_bp: number
  max_point_deviation_bp: number
  smoothness: number
  bid_ask_penalty: number
  long_end_flat_from_months: number | null
  long_end_flatness: number
  short_end_months: number
  short_end_weight: number
}

export interface Currency {
  code: string
  name: string
  numeric_code: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface Definition {
  id: string
  code: string
  name: string
  currency_code: string
  index_name: string
  description: string
  day_count: 'ACT/365F' | 'ACT/360'
  fixed_frequency_months: 1 | 3 | 6 | 12
  single_payment_max_months: number
  default_params: Partial<Params>
  effective_params: Params
  is_active: boolean
  created_at: string
  updated_at: string
  current_state: { as_of_date: string; saved_by: string; saved_at: string } | null
  last_history_date: string | null
}

export interface CurveNode {
  months: number | null
  date: string
  t: number
  df: number
  zero: number
  forward: number | null
  par: number | null
}

export interface FittedQuote {
  tenor: string
  maturity: string
  t: number
  bid: number
  ask: number
  mid: number
  model: number
  deviation_bp: number
  half_spread_bp: number
  in_spread: boolean
}

export interface CalibrationResult {
  as_of_date: string
  params: Params
  conventions: { day_count: 'ACT/365F' | 'ACT/360'; fixed_frequency_months: number; single_payment_max_months: number }
  nodes: CurveNode[]
  fitted: FittedQuote[]
  stats: Record<string, any>
  warnings: string[]
}

export interface Snapshot {
  curve_code: string
  kind: 'state' | 'history'
  as_of_date: string
  version: number | null
  source: string | null
  input_mode: InputMode
  quotes: Record<string, unknown>[]
  params: Params
  conventions: CalibrationResult['conventions']
  result: CalibrationResult
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
  kind: 'state' | 'history'
  version: number | null
  nodes: CurveNode[]
}

export interface EvaluateResult {
  date: string
  t: number
  df: number
  zero_rates: Record<Compounding, number>
  instantaneous_forward: number
  forward_rates: Record<Compounding, number> | null
  extrapolated: boolean
}
