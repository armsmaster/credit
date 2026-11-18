export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public payload?: unknown,
  ) {
    super(message)
  }
}

type Query = Record<string, string | number | boolean | null | undefined | Array<string | number>>

export interface Http {
  get<T>(url: string, query?: Query): Promise<T>
  post<T>(url: string, body?: unknown): Promise<T>
  put<T>(url: string, body?: unknown): Promise<T>
  del<T>(url: string): Promise<T>
}

function withQuery(url: string, query?: Query): string {
  if (!query) return url
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v)) v.forEach((item) => params.append(k, String(item)))
    else params.append(k, String(v))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

function messageOf(status: number, data: any): string {
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors
    return detail.map((d: any) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ')
  }
  if (status === 401) return 'Сессия истекла, войдите заново'
  if (status === 403) return 'Недостаточно прав'
  if (status >= 500) return 'Ошибка сервера'
  return `Ошибка ${status}`
}

export function createHttp(getToken?: () => Promise<string | undefined>): Http {
  async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const token = getToken ? await getToken() : undefined
    if (token) headers.Authorization = `Bearer ${token}`
    const res = await fetch(url, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined })
    if (res.status === 204) return undefined as T
    const data = await res.json().catch(() => null)
    if (!res.ok) throw new ApiError(res.status, messageOf(res.status, data), data?.code, data)
    return data as T
  }
  return {
    get: (url, query) => request('GET', withQuery(url, query)),
    post: (url, body) => request('POST', url, body ?? {}),
    put: (url, body) => request('PUT', url, body ?? {}),
    del: (url) => request('DELETE', url),
  }
}
