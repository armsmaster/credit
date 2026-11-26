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

// ------------------------------------------------------------------ CDS
export interface CdsSettings {
  recovery_quote: number
  tenors: string[]
  coupons_bp: number[]
  day_count: 'ACT/ACT.ISDA' | 'ACT/365F' | 'ACT/360'
  protection_from_effective: boolean
  rebate_mode: 'spec' | 'unadjusted_start' | 'inclusive_end'
  sber_adjust_period_ends: boolean
  sber_last_period_inclusive: boolean
  spfi_calendar: string
  sber_calendar: string
}

export interface CalendarInfo {
  code: string
  name: string
  base_code: string | null
  description: string
  exceptions: number
}

export interface CalendarDay {
  day: string
  is_business: boolean
  comment: string
  updated_by: string
  updated_at: string
}

export interface CdsMeta {
  today: string
  settings: CdsSettings
  tenors: string[]
  calendars: CalendarInfo[]
  profiles: Record<string, string>
}

export interface CdsScheduleRow {
  accrual_start: string
  accrual_end: string
  pay_date: string
  days: number
  year_frac: number
  coupon: number
  observation: string | null
  df_pay: number | null
  survival: number | null
  pv_coupon: number | null
  pv_aod: number | null
  amount?: number
  pv_amount?: number | null
}

export interface CdsContract {
  maturity: string
  effective_date: string
  first_period_start: string
  upfront_settle_date: string
  rebate_start: string | null
  rebate_end: string | null
  rebate_year_frac: number
  protection_start: string
  protection: number
  rpv01_dirty: number
  rpv01_clean: number
  df_settle: number
  par_spread: number
  qs: number
  qs_flat_lambda: number
  puf: Record<string, number>
  survival: number
  schedule: CdsScheduleRow[]
}

export interface CdsTenorRow extends CdsContract {
  tenor: string
  par_bond_bp: number
  basis_bp: number
  target_par_bp: number
  lambda: number | null
  dropped: boolean
  par_spread_bp: number
  qs_bp: number
}

export interface CdsResult {
  valuation_date: string
  effective_date: string
  recovery: number
  recovery_quote: number
  standard_coupon: number
  coupons: number[]
  hazard: { t: number; date: string; lambda: number }[]
  table: CdsTenorRow[]
  monthly: { date: string; months: number; t: number; survival: number; hazard: number; bond_survival: number; bond_hazard: number }[]
  warnings: string[]
}

export interface CdsSnapshot {
  entity_code: string
  entity_name: string
  kind: 'state' | 'history'
  as_of_date: string
  version: number | null
  source: string | null
  trigger: string
  trigger_title: string
  author: string
  saved_at: string
  inputs: Record<string, any>
  result: CdsResult
}

export interface CdsEntity {
  code: string
  name: string
  inn: string | null
  ogrn: string | null
  lei: string | null
  reference_obligation: string | null
  credit_curve_code: string
  standard_coupon_bp: number
  max_tenor: string
  recovery_override: number | null
  doc_basis_bp: number
  basis_bp: Record<string, number>
  is_active: boolean
  created_at: string
  updated_at: string
  state: {
    as_of_date: string
    calculated_at: string
    calculated_by: string
    trigger: string
    trigger_title: string
    recovery: number
    tenors: { tenor: string; par_spread_bp: number; qs_bp: number; puf: Record<string, number> }[]
    warnings: string[]
  } | null
  warning?: string | null
}

export interface CdsContractRequest {
  entity_code: string
  as_of_date?: string | null
  profile: 'SPFI_STD' | 'SBER_OTC'
  trade_date?: string | null
  maturity?: string | null
  tenor?: string | null
  coupon_bp?: number | null
  notional?: number | null
  side: 'buyer' | 'seller'
  upfront_pct?: number | null
  qs_bp?: number | null
  quote_input?: 'qs' | 'puf' | null
  recovery?: number | null
}

export interface CdsQuoteConversion {
  quote_input: 'qs' | 'puf'
  qs_bp: number
  upfront_pct: number
  flat_lambda: number
  recovery_quote: number
  coupon_bp: number
  curve_date: string
  maturity: string
  effective_date: string
  upfront_settle_date: string
  rebate_year_frac: number
  upfront_amount: number
  rebate_amount: number
  initial_payment: number
}

export interface CdsContractResult extends CdsContract {
  entity_code: string
  curve_date: string
  profile: string
  trade_date: string
  coupon_bp: number
  notional: number
  side: string
  recovery: number
  puf_model: number
  par_spread_bp: number
  qs_bp: number
  rebate_amount: number
  initial_payment_model: number
  initial_payment_trade: number | null
  pv_dirty: number
  npv: number
  upfront_pct: number | null
  quote_input: 'qs' | 'puf' | null
  trade_qs_bp: number | null
  trade_flat_lambda: number | null
  recovery_quote: number
  pd: number
  jtd_buyer: number
  doc_basis_bp: number
  warnings: string[]
  conventions: Record<string, string>
}

// ------------------------------------------------------------------ CDS trades
export type Bdc = 'following' | 'modified_following' | 'preceding' | 'unadjusted'

export interface TradeScheduleParams {
  mode: 'standard' | 'custom'
  frequency_months: 1 | 3 | 6 | 12
  direction: 'backward' | 'forward'
  stub: 'short' | 'long'
  roll_day: number | null
  bdc: Bdc
  adjust_accrual: boolean
  calendar: string | null
  day_count: 'ACT/ACT.ISDA' | 'ACT/365F' | 'ACT/360'
  last_period_inclusive: boolean
}

export interface TradeRow {
  accrual_start: string
  accrual_end: string
  pay_date: string
  days?: number
  year_frac?: number
  amount: number | null
  manual: boolean
}

export interface TradeTerms {
  side: 'buyer' | 'seller'
  notional: number | null
  coupon_bp: number | null
  trade_date: string | null
  effective_date?: string | null
  maturity: string | null
  rebate: boolean
  schedule_params: TradeScheduleParams
  rows?: TradeRow[] | null
}

export interface TradeInput extends TradeTerms {
  external_id: string
  entity_code: string
  tags: string[]
  comment: string
}

export interface TradeSchedule {
  rows: TradeRow[]
  edited: boolean
  effective_date: string
  settle_date: string
  first_period_start: string | null
  rebate_start: string | null
  rebate_end: string | null
  rebate_year_frac: number
  rebate_amount: number
  total_amount: number
  schedule_params: TradeScheduleParams
}

export interface TradeValuation {
  id: number
  valuation_date: string
  kind: 'intraday' | 'eod'
  trigger: string
  trigger_title: string
  calculated_at: string
  calculated_by: string
  trade_version: number
  curve_date: string
  npv: number
  clean: number
  accrued: number
  par_spread_bp: number
  rpv01: number
  cs01: number
  details: {
    protection_pv: number
    premium_pv: number
    survival_to_maturity: number
    pd: number
    recovery: number
    default_payment: number
    protection_start: string
    curve_date: string
    curve_kind: string
    stale_days: number
    discount: { code: string; as_of_date: string }
    next_payment: { date: string; amount: number } | null
  }
}

export interface TradeSummary {
  id: string
  external_id: string
  status: 'active' | 'cancelled'
  version: number
  entity_code: string
  entity_name: string
  side: 'buyer' | 'seller'
  notional: number
  coupon_bp: number
  trade_date: string
  effective_date: string
  maturity: string
  rebate: boolean
  schedule_mode: 'standard' | 'custom'
  schedule_edited: boolean
  comment: string
  updated_at: string
  updated_by: string
  tags: string[]
  life?: 'alive' | 'matured' | 'not_started'
  valuation?: TradeValuation | null
}

export interface TradeSearch {
  date: string
  items: TradeSummary[]
  totals: { trades: number; valued: number; npv: number; cs01: number; notional_bought: number; notional_sold: number }
}

export interface TradeVersionInfo {
  version: number
  created_by: string
  created_at: string
  notional: number
  coupon_bp: number
  maturity: string
  side: string
  schedule_edited: boolean
  tags: string[]
  comment: string
}

export interface TradeDetail extends TradeSummary {
  created_at: string
  created_by: string
  schedule_params: TradeScheduleParams
  schedule: {
    rows: TradeRow[]
    settle_date: string
    rebate_start: string | null
    rebate_end: string | null
    rebate_year_frac: number
    rebate_amount: number
    total_amount: number
  }
  versions: TradeVersionInfo[]
  valuations: TradeValuation[]
  valuation_records: number
  latest_valuation: TradeValuation | null
}

export interface TradesMeta {
  today: string
  previous_business_day: string
  business_calendar: string
  calendars: CalendarInfo[]
  defaults: { coupon_bp: number; calendar: string; day_count: TradeScheduleParams['day_count'] }
  entities: { code: string; name: string; standard_coupon_bp: number }[]
  triggers: Record<string, string>
}

export interface TradeMaturities {
  effective_date: string
  mode: 'standard' | 'custom'
  tenors: { tenor: string; maturity: string }[]
}
