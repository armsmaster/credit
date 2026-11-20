import { downloadFile, getPlatform } from '@cds/platform'

import type {
  CalibrationResult,
  CompareCurve,
  Currency,
  Definition,
  EvaluateResult,
  HistoryDate,
  InputMode,
  Params,
  Snapshot,
  Version,
} from './types'

const BASE = '/api/curves'
const http = () => getPlatform().http

export interface QuotesPayload {
  input_mode: InputMode
  quotes: Record<string, unknown>[]
  params: Partial<Params>
}

export const api = {
  meta: () => http().get<{ today: string; default_params: Params }>(`${BASE}/meta`),
  currencies: {
    list: () => http().get<Currency[]>(`${BASE}/currencies`),
    create: (c: Partial<Currency>) => http().post<Currency>(`${BASE}/currencies`, c),
    update: (code: string, c: Partial<Currency>) => http().put<Currency>(`${BASE}/currencies/${code}`, c),
  },
  definitions: {
    list: () => http().get<Definition[]>(`${BASE}/definitions`),
    get: (code: string) => http().get<Definition>(`${BASE}/definitions/${encodeURIComponent(code)}`),
    create: (d: Record<string, unknown>) => http().post<Definition>(`${BASE}/definitions`, d),
    update: (code: string, d: Record<string, unknown>) => http().put<Definition>(`${BASE}/definitions/${encodeURIComponent(code)}`, d),
  },
  calibrate: (curve_code: string, as_of_date: string, p: QuotesPayload) =>
    http().post<CalibrationResult>(`${BASE}/calibrate`, { curve_code, as_of_date, ...p }),
  evaluate: (body: { as_of_date: string; day_count: string; nodes: { date: string; df: number }[]; date: string; start_date?: string | null }) =>
    http().post<EvaluateResult>(`${BASE}/evaluate`, body),
  state: {
    get: (code: string) => http().get<Snapshot>(`${BASE}/curves/${encodeURIComponent(code)}/state`),
    save: (code: string, as_of_date: string, comment: string, p: QuotesPayload) =>
      http().put<Snapshot>(`${BASE}/curves/${encodeURIComponent(code)}/state`, { as_of_date, comment, ...p }),
    archive: (code: string) => http().post<{ task_id: string }>(`${BASE}/curves/${encodeURIComponent(code)}/archive`),
  },
  history: {
    list: (code: string, date_from?: string, date_to?: string) =>
      http().get<HistoryDate[]>(`${BASE}/curves/${encodeURIComponent(code)}/history`, { date_from, date_to }),
    versions: (code: string, date: string) => http().get<Version[]>(`${BASE}/curves/${encodeURIComponent(code)}/history/${date}/versions`),
    get: (code: string, date: string, version?: number | null) =>
      http().get<Snapshot>(`${BASE}/curves/${encodeURIComponent(code)}/history/${date}`, { version: version ?? undefined }),
    save: (code: string, date: string, comment: string, p: QuotesPayload) =>
      http().post<Snapshot>(`${BASE}/curves/${encodeURIComponent(code)}/history/${date}`, { comment, ...p }),
  },
  compare: (code: string, as_of: string) => http().get<CompareCurve[]>(`${BASE}/curves/${encodeURIComponent(code)}/compare`, { as_of }),
  // Excel with the calculation detail (nodes, interpolation, swap schedules, par rates)
  excel: {
    state: (code: string) => downloadFile(`${BASE}/curves/${encodeURIComponent(code)}/state/export.xlsx`),
    history: (code: string, date: string, version?: number | null) =>
      downloadFile(`${BASE}/curves/${encodeURIComponent(code)}/history/${date}/export.xlsx${version ? `?version=${version}` : ''}`),
    calculation: (curve_code: string, as_of_date: string, p: QuotesPayload) =>
      downloadFile(`${BASE}/calibrate/export.xlsx`, { body: { curve_code, as_of_date, ...p } }),
  },
}
