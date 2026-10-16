import { authRequest, currentIdentityVersion, identityFetch, notifyIdentityChange, onIdentityReset } from './auth/transport'

export type ObjectType = 'order' | 'shipment' | 'transport_task'
export type ObjectReference = { type: ObjectType; id: string; number: string | null }
export type AssistantAnswer = {
  message_id: string
  answer: string
  references: ObjectReference[]
  references_truncated: boolean
  warnings: string[]
  stop_reason: string | null
  usage: { model_calls: number; tool_calls: number; http_calls: number }
  trace_id: string
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
        if (name === 'error') {
          if (['AUTH_CHANGED', 'AUTH_REQUIRED', 'ACCOUNT_DISABLED'].includes(data.code)) notifyIdentityChange(data.code)
          throw new AssistantError(data.message ?? '查询失败，请重试。', data.code)
        }
        if (name === 'result') return parseAnswer(data, messageId)
      }
      if (done) break
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
  throw new AssistantError('连接中断，尚未收到完整回答。可以重试当前问题。')
}

export const referenceLabels: Record<ObjectType, string> = { order: '订单', shipment: '运单', transport_task: '运输任务' }
export const referencePaths: Record<ObjectType, string> = { order: '/orders', shipment: '/shipments', transport_task: '/tasks' }
