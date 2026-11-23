import { downloadFile, getPlatform } from '@cds/platform'

import type {
  Bond,
  BondDetail,
  CalendarDay,
  CalendarInfo,
  CdsContractRequest,
  CdsContractResult,
  CdsEntity,
  CdsMeta,
  CdsSettings,
  CdsSnapshot,
  BondManual,
  Calculation,
  CalcBond,
  CompareCurve,
  Definition,
  HistoryDate,
  IssSearchItem,
  Issuer,
  Meta,
  Params,
  RateCurveDef,
  Settings,
  Snapshot,
  Version,
} from './types'

const BASE = '/api/credit'
const http = () => getPlatform().http
const enc = encodeURIComponent

export interface CalcPayload {
  bonds: Pick<CalcBond, 'secid' | 'weight' | 'enabled' | 'manual_price' | 'excluded_obs'>[] | null
  params: Partial<Params>
}

export const api = {
  meta: () => http().get<Meta>(`${BASE}/meta`),
  settings: {
    get: () => http().get<Settings>(`${BASE}/settings`),
    save: (s: Pick<Settings, 'index_curves' | 'liquidity' | 'dema'>) => http().put<Settings>(`${BASE}/settings`, s),
  },
  rateCurves: () => http().get<RateCurveDef[]>('/api/curves/definitions'),
  issuers: {
    list: () => http().get<Issuer[]>(`${BASE}/issuers`),
    search: (q: string) => http().get<IssSearchItem[]>(`${BASE}/iss/emitters`, { q }),
    add: (emitter_id: number) => http().post<{ emitter_id: number; task_id: string }>(`${BASE}/issuers`, { emitter_id }),
    scan: (id: number) => http().post<{ task_id: string }>(`${BASE}/issuers/${id}/scan`),
    remove: (id: number) => http().del<void>(`${BASE}/issuers/${id}`),
  },
  bonds: {
    list: (q: { issuer_id?: number | null; short_years?: number }) => http().get<Bond[]>(`${BASE}/bonds`, q),
    get: (secid: string) => http().get<BondDetail>(`${BASE}/bonds/${enc(secid)}`),
    saveManual: (secid: string, m: BondManual) => http().put<BondDetail>(`${BASE}/bonds/${enc(secid)}/manual`, m),
    dema: (secid: string) =>
      http().get<{ half_life: number | null; auto: any; candles: { begin: string; close: number; dema: number }[] }>(`${BASE}/bonds/${enc(secid)}/dema`),
  },
  definitions: {
    list: () => http().get<Definition[]>(`${BASE}/definitions`),
    get: (code: string) => http().get<Definition>(`${BASE}/definitions/${enc(code)}`),
    create: (d: Record<string, unknown>) => http().post<Definition>(`${BASE}/definitions`, d),
    update: (code: string, d: Record<string, unknown>) => http().put<Definition>(`${BASE}/definitions/${enc(code)}`, d),
  },
  market: (code: string, as_of: string) => http().get<CalcBond[]>(`${BASE}/curves/${enc(code)}/market`, { as_of }),
  refreshMarket: (code: string) => http().post<Record<string, any>>(`${BASE}/curves/${enc(code)}/market/refresh`),
  calibrate: (curve_code: string, as_of_date: string, p: CalcPayload) =>
    http().post<Calculation>(`${BASE}/calibrate`, { curve_code, as_of_date, ...p }),
  state: {
    get: (code: string) => http().get<Snapshot>(`${BASE}/curves/${enc(code)}/state`),
    save: (code: string, as_of_date: string, comment: string, p: CalcPayload) =>
      http().put<Snapshot>(`${BASE}/curves/${enc(code)}/state`, { as_of_date, comment, ...p }),
    archive: (code: string) => http().post<{ task_id: string }>(`${BASE}/curves/${enc(code)}/archive`),
  },
  history: {
    list: (code: string, date_from?: string, date_to?: string) =>
      http().get<HistoryDate[]>(`${BASE}/curves/${enc(code)}/history`, { date_from, date_to }),
    versions: (code: string, date: string) => http().get<Version[]>(`${BASE}/curves/${enc(code)}/history/${date}/versions`),
    get: (code: string, date: string, version?: number | null) =>
      http().get<Snapshot>(`${BASE}/curves/${enc(code)}/history/${date}`, { version: version ?? undefined }),
    save: (code: string, date: string, comment: string, p: CalcPayload) =>
      http().post<Snapshot>(`${BASE}/curves/${enc(code)}/history/${date}`, { comment, ...p }),
  },
  compare: (code: string, as_of: string) => http().get<CompareCurve[]>(`${BASE}/curves/${enc(code)}/compare`, { as_of }),
  // Excel with the calculation detail (survival, cash flows, recovery, discounting, model prices)
  excel: {
    state: (code: string) => downloadFile(`${BASE}/curves/${enc(code)}/state/export.xlsx`),
    history: (code: string, date: string, version?: number | null) =>
      downloadFile(`${BASE}/curves/${enc(code)}/history/${date}/export.xlsx${version ? `?version=${version}` : ''}`),
    calculation: (curve_code: string, as_of_date: string, p: CalcPayload) =>
      downloadFile(`${BASE}/calibrate/export.xlsx`, { body: { curve_code, as_of_date, ...p } }),
  },
  cds: {
    meta: () => http().get<CdsMeta>(`${BASE}/cds/meta`),
    saveSettings: (s: Partial<CdsSettings>) => http().put<CdsSettings>(`${BASE}/cds/settings`, s),
    entities: () => http().get<CdsEntity[]>(`${BASE}/cds/entities`),
    entity: (code: string) => http().get<CdsEntity>(`${BASE}/cds/entities/${enc(code)}`),
    createEntity: (e: Record<string, unknown>) => http().post<CdsEntity>(`${BASE}/cds/entities`, e),
    updateEntity: (code: string, e: Record<string, unknown>) => http().put<CdsEntity>(`${BASE}/cds/entities/${enc(code)}`, e),
    recalculate: (code: string) => http().post<CdsSnapshot>(`${BASE}/cds/entities/${enc(code)}/recalculate`),
    state: (code: string) => http().get<CdsSnapshot>(`${BASE}/cds/entities/${enc(code)}/state`),
    history: (code: string) => http().get<{ as_of_date: string; latest_version: number; versions: number; source: string; author: string; saved_at: string; edited: boolean }[]>(`${BASE}/cds/entities/${enc(code)}/history`),
    historyItem: (code: string, date: string, version?: number | null) =>
      http().get<CdsSnapshot>(`${BASE}/cds/entities/${enc(code)}/history/${date}`, { version: version ?? undefined }),
    calculate: (req: CdsContractRequest) => http().post<CdsContractResult>(`${BASE}/cds/calculator`, req),
    excel: {
      state: (code: string) => downloadFile(`${BASE}/cds/entities/${enc(code)}/state/export.xlsx`),
      history: (code: string, date: string, version?: number | null) =>
        downloadFile(`${BASE}/cds/entities/${enc(code)}/history/${date}/export.xlsx${version ? `?version=${version}` : ''}`),
      contract: (req: CdsContractRequest) => downloadFile(`${BASE}/cds/calculator/export.xlsx`, { body: req }),
      daily: (date: string) => downloadFile(`${BASE}/cds/daily.xlsx?date=${date}`),
    },
  },
  calendars: {
    list: () => http().get<CalendarInfo[]>(`${BASE}/calendars`),
    days: (code: string, year: number) => http().get<CalendarDay[]>(`${BASE}/calendars/${enc(code)}/days`, { year }),
    setDay: (code: string, day: string, is_business: boolean, comment: string) =>
      http().put<void>(`${BASE}/calendars/${enc(code)}/days/${day}`, { is_business, comment }),
    deleteDay: (code: string, day: string) => http().del<void>(`${BASE}/calendars/${enc(code)}/days/${day}`),
    shift: (code: string, date: string, days: number) =>
      http().get<{ date: string; days: number; result: string; following: string; is_business: boolean }>(`${BASE}/calendars/${enc(code)}/shift`, { date, days }),
  },
}
