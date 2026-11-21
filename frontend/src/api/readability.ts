import { request } from './client'

export type ReadingState = '确认零行' | '可读有行' | '未实测'

export type SourceRow = {
  role: 'order' | 'region'
  role_label: string
  table: string
  partition: string
  reading_state: ReadingState
  rows: number | null
  measured: boolean
  revision: string
  measured_at: string | null
  source?: string
  note?: string
  out_of_scope?: boolean
  stale_reading?: { state: ReadingState; rows: number; measured_at: string | null } | null
  other_revision_evidence?: {
    revision: string
    state: ReadingState
    rows: number
    measured_at: string | null
    note: string
  }[]
  partition_date?: string
}

export type Blocker = {
  role: string
  role_label: string
  table: string
  partition: string
  state: ReadingState
  reason: string
}

export type OpenReading = SourceRow & { display: string }

export type ReadabilityOption = {
  key: string
  label: string
  can_submit: boolean
  dates: string[]
  open_readings: OpenReading[]
  missing: Blocker[]
  note: string
}

export type Readability = {
  revision: { revision: string; scanned_at: string; source: string; scope: string[]; note: string }
  revision_name: string
  dates: string[]
  rows: SourceRow[]
  summary: {
    确认零行: number
    可读有行: number
    未实测: number
    total: number
    coverage_complete: boolean
    ready_dates: string[]
    waiting_dates: string[]
  }
  decision: {
    can_submit: boolean
    ready_dates: string[]
    waiting_dates: string[]
    coverage_complete: boolean
    missing: Blocker[]
    text: string
  }
  options: ReadabilityOption[]
}

export type Revision = {
  revision: string
  scanned_at: string
  source: string
  scope: string[]
  note: string
  status: string
  reading_count: number
  coverage_complete: boolean
  ready_dates: string[]
  waiting_dates: string[]
}

export type Batch = {
  id: number
  batch_id: string
  pipeline: string
  partition_date: string
  status: string
  revision: string
  order_partition: string
  region_partition: string
  order_source: string
  region_source: string
  sources: SourceRow[]
  planned_sources: SourceRow[]
  opened_readings: OpenReading[]
  blockers: Blocker[]
  planned_at: string
  submitted_at: string | null
  started_at: string | null
  completed_at: string | null
  recalculated_at: string | null
  scope_change_seen_at: string | null
  request_id: string
}

export type BatchResult = {
  ok: boolean
  message: string
  submitted_batches?: Batch[]
  waiting_batches?: Batch[]
  batches?: Batch[]
  released_batches?: Batch[]
  decision?: Readability['decision']
  readability?: Readability
}

async function post(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = await response.json()
  if (!response.ok) {
    const detail = payload && typeof payload.detail === 'string' ? payload.detail : '操作失败'
    throw new Error(detail)
  }
  return payload
}

export async function listRevisions(): Promise<Revision[]> {
  const response = await request('/api/readability/revisions')
  if (!response.ok) throw new Error('扫描修订读取失败')
  const payload = await response.json()
  return payload.items as Revision[]
}

export async function loadReadability(revision: string, dates: string[]): Promise<Readability> {
  const query = new URLSearchParams({ revision })
  if (dates.length) query.set('dates', dates.join(','))
  const response = await request(`/api/readability/readings?${query.toString()}`)
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.detail ?? '分区可读明细读取失败')
  return payload as Readability
}

export async function reportReading(body: {
  revision: string
  table: string
  partition: string
  rows: string
  note: string
}): Promise<BatchResult> {
  return (await post('/api/readability/readings', body)) as BatchResult
}

export async function updateScope(body: {
  revision: string
  dates: string[]
  reason: string
}): Promise<BatchResult> {
  return (await post('/api/readability/scope', body)) as BatchResult
}

export async function listBatches(revision: string): Promise<Batch[]> {
  const query = new URLSearchParams({ revision })
  const response = await request(`/api/readability/batches?${query.toString()}`)
  if (!response.ok) throw new Error('销售日加工批次读取失败')
  const payload = await response.json()
  return payload.items as Batch[]
}

export async function generateBatches(body: {
  revision: string
  dates: string[]
  note: string
}): Promise<BatchResult> {
  return (await post('/api/readability/sales-batches', body)) as BatchResult
}

export async function refreshBatches(body: {
  revision: string
  dates?: string[]
}): Promise<BatchResult> {
  return (await post('/api/readability/sales-batches/refresh', body)) as BatchResult
}

export async function runBatchAction(batchId: string, action: string): Promise<BatchResult> {
  return (await post(`/api/readability/batches/${encodeURIComponent(batchId)}/actions`, {
    action,
  })) as BatchResult
}
