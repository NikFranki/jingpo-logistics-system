import { authRequest, currentIdentityVersion, identityFetch, notifyIdentityChange, onIdentityReset } from './auth/transport'

export type ObjectType = 'order' | 'shipment' | 'transport_task'
export type ObjectReference = { type: ObjectType; id: string; number: string | null }
export type ActionType = 'order.update' | 'shipment.destination.update'
export type ActionCandidate = {
  candidate_id: string
  agent_session_id: string
  message_id: string
  action_type: ActionType
  subject_type: 'order' | 'shipment'
  subject_id: string
  payload: Record<string, unknown>
}
export type ActionPreview = {
  proposal_id: string
  candidate_id: string
  action_type: ActionType
  subject: { type: 'order' | 'shipment'; id: string; number: string }
  changes: { field: string; label: string; before: unknown; after: unknown }[]
  impact: string[]
  execution_policy: 'CONFIRM_REQUIRED' | 'AUTO_ALLOWED'
  expires_at: string
}
export type ActionSyncResult = {
  candidate_id: string
  proposal_id: string
  status: 'SUCCEEDED' | 'REJECTED' | 'EXPIRED' | 'FAILED'
  action_type: ActionType
  subject?: { type: 'order' | 'shipment'; id: string; number: string }
  result_summary?: { summary?: Record<string, unknown>; be_request_id?: string } | null
}
export type AssistantWritePolicy = {
  enabled_action_types: ActionType[]
  eligible_action_types: ActionType[]
  version: number
}
export type AssistantAnswer = {
  message_id: string
  answer: string
  references: ObjectReference[]
  references_truncated: boolean
  warnings: string[]
  stop_reason: string | null
  usage: { model_calls: number; tool_calls: number; http_calls: number }
  trace_id: string
  action_candidate?: ActionCandidate
}

export class AssistantError extends Error {
  code: string
  constructor(message: string, code = 'NETWORK_ERROR') {
    super(message)
    this.code = code
  }
}

async function check(response: Response) {
  if (response.ok) return
  const body = await response.json().catch(() => null)
  if (response.status === 401 || ['AUTH_CHANGED', 'EMAIL_NOT_VERIFIED', 'PROFILE_INCOMPLETE'].includes(body?.error?.code)) notifyIdentityChange(body?.error?.code ?? 'AUTH_REQUIRED')
  throw new AssistantError(body?.error?.message ?? '助手服务暂不可用，请稍后重试。', body?.error?.code ?? `HTTP_${response.status}`)
}

let tokenCache: { token: string; expires: number; version: number } | undefined
let tokenRequest: { version: number; promise: Promise<string> } | undefined
onIdentityReset(() => { tokenCache = undefined; tokenRequest = undefined })
async function agentToken() {
  const version = currentIdentityVersion()
  if (tokenCache?.version === version && tokenCache.expires > Date.now() + 30_000) return tokenCache.token
  if (tokenRequest?.version === version) return tokenRequest.promise
  const promise = authRequest<{ access_token: string; expires_at: string }>('/auth/agent-token', { method: 'POST' }).then(result => {
    if (version !== currentIdentityVersion()) throw new AssistantError('账号状态已变化，请重新查询。', 'AUTH_CHANGED')
    if (typeof result.access_token !== 'string' || Date.parse(result.expires_at) <= Date.now()) throw new AssistantError('助手凭据无效，请重新登录。', 'AUTH_REQUIRED')
    tokenCache = { token: result.access_token, expires: Date.parse(result.expires_at), version }
    return result.access_token
  }).finally(() => { if (tokenRequest?.version === version) tokenRequest = undefined })
  tokenRequest = { version, promise }
  return promise
}

async function agentFetch(path: string, init: RequestInit) {
  const token = await agentToken()
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  return identityFetch(path, { ...init, headers, credentials: 'omit' })
}

export async function createAssistantSession(signal: AbortSignal): Promise<string> {
  const response = await agentFetch('/agent/api/v1/sessions', { method: 'POST', signal })
  await check(response)
  const data = await response.json()
  if (typeof data.session_id !== 'string') throw new AssistantError('助手响应异常，请开始新对话。', 'INVALID_RESPONSE')
  return data.session_id
}

export async function deleteAssistantSession(id: string) {
  const response = await agentFetch(`/agent/api/v1/sessions/${id}`, { method: 'DELETE', signal: AbortSignal.timeout(5000) })
  if (response.status !== 404) await check(response)
}

function parseAnswer(data: AssistantAnswer, messageId: string): AssistantAnswer {
  if (data.message_id !== messageId || typeof data.answer !== 'string' || !Array.isArray(data.references) || !Array.isArray(data.warnings)) {
    throw new AssistantError('助手响应异常，请重试。', 'INVALID_RESPONSE')
  }
  // Navigation paths are created locally from a fixed type map, never from model URLs.
  data.references = data.references.filter(ref => ['order', 'shipment', 'transport_task'].includes(ref.type) && /^[1-9][0-9]{0,18}$/.test(ref.id))
  return data
}

function parseActionCandidate(value: unknown, messageId: string): ActionCandidate | undefined {
  if (!value || typeof value !== 'object') return undefined
  const candidate = value as Record<string, unknown>
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
  if (Object.keys(candidate).sort().join(',') !== 'action_type,agent_session_id,candidate_id,message_id,payload,subject_id,subject_type') return undefined
  if (typeof candidate.candidate_id !== 'string' || !uuid.test(candidate.candidate_id) ||
      typeof candidate.agent_session_id !== 'string' || !uuid.test(candidate.agent_session_id) ||
      candidate.message_id !== messageId || typeof candidate.subject_id !== 'string' ||
      !/^[1-9][0-9]{0,18}$/.test(candidate.subject_id) || !candidate.payload || typeof candidate.payload !== 'object') return undefined
  if (candidate.action_type === 'order.update' && candidate.subject_type === 'order') {
    const allowed = new Set(['product_name', 'quantity', 'sender_name', 'sender_address', 'recipient_name', 'recipient_address', 'earliest_handover_at', 'latest_delivery_at', 'sender_province_id', 'sender_city_id', 'sender_district_id', 'recipient_province_id', 'recipient_city_id', 'recipient_district_id'])
    const keys = Object.keys(candidate.payload)
    if (!keys.length || keys.some(key => !allowed.has(key))) return undefined
    const payload = candidate.payload as Record<string, unknown>
    for (const [key, field] of Object.entries(payload)) {
      if (['quantity', 'sender_province_id', 'sender_city_id', 'sender_district_id', 'recipient_province_id', 'recipient_city_id', 'recipient_district_id'].includes(key)) {
        if (field === null && key === 'quantity') return undefined
        if (field !== null && (!Number.isSafeInteger(field) || Number(field) <= 0)) return undefined
      } else if (['earliest_handover_at', 'latest_delivery_at'].includes(key)) {
        if (field !== null && (typeof field !== 'string' || !/^\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)$/.test(field) || !Number.isFinite(Date.parse(field)))) return undefined
      } else if (typeof field !== 'string' || !field.trim() || field.length > (key.endsWith('_address') ? 500 : 100)) return undefined
    }
    for (const side of ['sender', 'recipient']) {
      const regionFields = [`${side}_province_id`, `${side}_city_id`, `${side}_district_id`]
      if (regionFields.some(field => Object.hasOwn(payload, field)) && !regionFields.every(field => Object.hasOwn(payload, field))) return undefined
    }
    if (typeof payload.earliest_handover_at === 'string' && typeof payload.latest_delivery_at === 'string' &&
        Date.parse(payload.earliest_handover_at) > Date.parse(payload.latest_delivery_at)) return undefined
  } else if (candidate.action_type === 'shipment.destination.update' && candidate.subject_type === 'shipment') {
    const payload = candidate.payload as Record<string, unknown>
    if (Object.keys(payload).sort().join(',') !== 'destination_station_id,expected_destination_station_id,reason' ||
        !Number.isSafeInteger(payload.expected_destination_station_id) || !Number.isSafeInteger(payload.destination_station_id) ||
        typeof payload.reason !== 'string' || !payload.reason.trim()) return undefined
  } else return undefined
  return candidate as unknown as ActionCandidate
}

export async function queryAssistant(
  sessionId: string, messageId: string, message: string, signal: AbortSignal,
  onProgress: (message: string) => void,
): Promise<AssistantAnswer> {
  const response = await agentFetch(`/agent/api/v1/sessions/${sessionId}/messages`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: messageId, message }), signal,
  })
  await check(response)
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new AssistantError('助手响应异常，请重试。', 'INVALID_RESPONSE')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let actionCandidate: ActionCandidate | undefined
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      let separator: number
      while ((separator = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        const lines = block.split('\n')
        const name = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
        const raw = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (!raw) continue
        const data = JSON.parse(raw)
        if (name === 'progress' && typeof data.message === 'string') onProgress(data.message)
        if (name === 'action_proposed') actionCandidate = parseActionCandidate(data, messageId)
        if (name === 'error') {
          if (['AUTH_CHANGED', 'AUTH_REQUIRED', 'ACCOUNT_DISABLED'].includes(data.code)) notifyIdentityChange(data.code)
          throw new AssistantError(data.message ?? '查询失败，请重试。', data.code)
        }
        if (name === 'result') {
          const resultCandidate = parseActionCandidate(data.action_candidate, messageId)
          return parseAnswer({ ...data, action_candidate: actionCandidate ?? resultCandidate }, messageId)
        }
      }
      if (done) break
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
  throw new AssistantError('连接中断，尚未收到完整回答。可以重试当前问题。')
}

export async function previewAssistantAction(candidate: ActionCandidate, signal: AbortSignal): Promise<ActionPreview> {
  const data = await authRequest<unknown>('/agent-actions/preview', {
    method: 'POST', signal,
    body: JSON.stringify({
      candidate_id: candidate.candidate_id, agent_session_id: candidate.agent_session_id,
      message_id: candidate.message_id, action_type: candidate.action_type,
      subject_id: candidate.subject_id, payload: candidate.payload,
    }),
  })
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
  const keysAre = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join(',') === [...keys].sort().join(',')
  if (!data || typeof data !== 'object') throw new AssistantError('助手预览响应异常，请重试。', 'INVALID_RESPONSE')
  const preview = data as Record<string, unknown>
  if (!keysAre(preview, ['proposal_id', 'candidate_id', 'action_type', 'subject', 'changes', 'impact', 'execution_policy', 'expires_at']) ||
      typeof preview.proposal_id !== 'string' || !uuid.test(preview.proposal_id) ||
      preview.candidate_id !== candidate.candidate_id || preview.action_type !== candidate.action_type ||
      (preview.execution_policy !== 'CONFIRM_REQUIRED' && preview.execution_policy !== 'AUTO_ALLOWED') ||
      typeof preview.expires_at !== 'string' || !/^\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)$/.test(preview.expires_at) || !Number.isFinite(Date.parse(preview.expires_at)) ||
      !preview.subject || typeof preview.subject !== 'object' || !Array.isArray(preview.changes) || !preview.changes.length ||
      !Array.isArray(preview.impact) || preview.impact.some(item => typeof item !== 'string' || !item.trim())) {
    throw new AssistantError('助手预览响应异常，请重试。', 'INVALID_RESPONSE')
  }
  const subject = preview.subject as Record<string, unknown>
  const expectedNumber = candidate.subject_type === 'order' ? `ORD-${candidate.subject_id.padStart(6, '0')}` : `SHP-${candidate.subject_id.padStart(6, '0')}`
  if (!keysAre(subject, ['type', 'id', 'number']) || subject.type !== candidate.subject_type || subject.id !== candidate.subject_id ||
      (subject.number !== null && subject.number !== expectedNumber)) {
    throw new AssistantError('助手预览对象与请求不匹配。', 'INVALID_RESPONSE')
  }
  for (const change of preview.changes) {
    if (!change || typeof change !== 'object' || !keysAre(change as Record<string, unknown>, ['field', 'label', 'before', 'after']) ||
        typeof change.field !== 'string' || !change.field.trim() || typeof change.label !== 'string' || !change.label.trim() ||
        !['string', 'number', 'boolean'].includes(typeof change.before) && change.before !== null ||
        !['string', 'number', 'boolean'].includes(typeof change.after) && change.after !== null) {
      throw new AssistantError('助手预览变更内容异常，请重试。', 'INVALID_RESPONSE')
    }
  }
  return preview as unknown as ActionPreview
}

export async function executeAssistantAction(proposalId: string, mode: 'approved' | 'automatic', signal?: AbortSignal) {
  return authRequest<Record<string, unknown>>(`/agent-actions/${proposalId}/execute`, {
    method: 'POST', signal: signal ?? AbortSignal.timeout(60_000), body: JSON.stringify({ mode }),
  })
}

export async function rejectAssistantAction(proposalId: string, signal?: AbortSignal) {
  return authRequest<Record<string, unknown>>(`/agent-actions/${proposalId}/reject`, {
    method: 'POST', signal: signal ?? AbortSignal.timeout(30_000),
    body: JSON.stringify({ reason_code: 'USER_CANCELLED' }),
  })
}

export async function readAssistantAction(proposalId: string, signal?: AbortSignal) {
  return authRequest<Record<string, unknown>>(`/agent-actions/${proposalId}`, {
    method: 'GET', signal: signal ?? AbortSignal.timeout(30_000),
  })
}

export async function syncAssistantAction(sessionId: string, candidateId: string, proposalId: string, signal?: AbortSignal): Promise<ActionSyncResult> {
  const response = await agentFetch(`/agent/api/v1/sessions/${sessionId}/actions/${candidateId}/sync`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proposal_id: proposalId }), signal: signal ?? AbortSignal.timeout(30_000),
  })
  await check(response)
  const data = await response.json()
  if (data.candidate_id !== candidateId || data.proposal_id !== proposalId ||
      !['SUCCEEDED', 'REJECTED', 'EXPIRED', 'FAILED'].includes(data.status)) {
    throw new AssistantError('业务操作结果暂时无法核对。', 'ACTION_STATUS_INVALID')
  }
  return data as ActionSyncResult
}

const actionTypes: ActionType[] = ['order.update', 'shipment.destination.update']
function parseAssistantWritePolicy(value: unknown): AssistantWritePolicy {
  if (!value || typeof value !== 'object') throw new AssistantError('自动执行设置响应异常，当前仍需逐次确认。', 'INVALID_RESPONSE')
  const policy = value as Record<string, unknown>
  const validTypes = (items: unknown): items is ActionType[] => Array.isArray(items) &&
    items.every(item => typeof item === 'string' && actionTypes.includes(item as ActionType))
  if (!validTypes(policy.enabled_action_types) || !validTypes(policy.eligible_action_types) ||
      typeof policy.version !== 'number' || !Number.isSafeInteger(policy.version) || policy.version < 0) {
    throw new AssistantError('自动执行设置响应异常，当前仍需逐次确认。', 'INVALID_RESPONSE')
  }
  const enabled = policy.enabled_action_types
  const eligible = policy.eligible_action_types
  if (enabled.some(item => !eligible.includes(item))) {
    throw new AssistantError('自动执行设置响应异常，当前仍需逐次确认。', 'INVALID_RESPONSE')
  }
  return { enabled_action_types: enabled, eligible_action_types: eligible, version: policy.version }
}

export async function readAssistantWritePolicy(signal?: AbortSignal): Promise<AssistantWritePolicy> {
  return parseAssistantWritePolicy(await authRequest<unknown>('/auth/agent-write-policy', {
    method: 'GET', signal: signal ?? AbortSignal.timeout(15_000),
  }))
}

export async function updateAssistantWritePolicy(enabledActionTypes: ActionType[], signal?: AbortSignal): Promise<AssistantWritePolicy> {
  return parseAssistantWritePolicy(await authRequest<unknown>('/auth/agent-write-policy', {
    method: 'PUT', signal: signal ?? AbortSignal.timeout(15_000),
    body: JSON.stringify({ enabled_action_types: enabledActionTypes }),
  }))
}

export const referenceLabels: Record<ObjectType, string> = { order: '订单', shipment: '运单', transport_task: '运输任务' }
export const referencePaths: Record<ObjectType, string> = { order: '/orders', shipment: '/shipments', transport_task: '/tasks' }
