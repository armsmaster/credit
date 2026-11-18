const dtf = new Intl.DateTimeFormat('ru-RU', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})
const df = new Intl.DateTimeFormat('ru-RU', { year: 'numeric', month: '2-digit', day: '2-digit' })

export function formatDateTime(value?: string | null): string {
  return value ? dtf.format(new Date(value)) : '—'
}

export function formatDate(value?: string | null): string {
  if (!value) return '—'
  const d = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(value + 'T00:00:00') : new Date(value)
  return df.format(d)
}

export function formatNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function formatDuration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 1) return `${Math.round(seconds * 1000)} мс`
  if (seconds < 60) return `${seconds.toFixed(1)} с`
  return `${Math.floor(seconds / 60)} мин ${Math.round(seconds % 60)} с`
}

/** ISO date (YYYY-MM-DD) in local time. */
export function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
