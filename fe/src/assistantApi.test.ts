import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ authRequest: vi.fn() }))

vi.mock('./auth/transport', () => ({
  authRequest: mocks.authRequest,
  currentIdentityVersion: () => 1,
  identityFetch: vi.fn(),
  notifyIdentityChange: vi.fn(),
  onIdentityReset: vi.fn(),
}))

import { previewAssistantAction, type ActionCandidate } from './assistantApi'

const candidate: ActionCandidate = {
  candidate_id: 'c08b469b-522a-4bd7-b66e-26d9d96a0f21',
  agent_session_id: 'b766748f-2361-4e08-9724-9bcb0fdaf9eb',
  message_id: '39f2147a-4b2a-481b-a9fa-2d9d96f266b2',
  action_type: 'order.update', subject_type: 'order', subject_id: '42',
  payload: { product_name: 'new product' },
}

const response = {
  proposal_id: '56edb1a8-4b2a-481a-b9fa-2d9a06dc632b',
  candidate_id: candidate.candidate_id, action_type: candidate.action_type,
  subject: { type: 'order', id: '42', number: 'ORD-000042' },
  changes: [{ field: 'product_name', label: '货品名称', before: 'old product', after: 'new product' }],
  impact: ['订单仍须处于待发货状态'], execution_policy: 'CONFIRM_REQUIRED',
  expires_at: '2030-01-01T00:05:00Z',
}

describe('previewAssistantAction response contract', () => {
  beforeEach(() => mocks.authRequest.mockResolvedValue(response))

  it('accepts a valid BE preview bound to the requested candidate and object', async () => {
    await expect(previewAssistantAction(candidate, new AbortController().signal)).resolves.toEqual(response)
  })

  it('rejects a preview for a different candidate or action', async () => {
    mocks.authRequest.mockResolvedValueOnce({ ...response, candidate_id: 'b08b469b-522a-4bd7-b66e-26d9d96a0f21' })
    await expect(previewAssistantAction(candidate, new AbortController().signal)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('rejects an invalid or missing expiration time', async () => {
    mocks.authRequest.mockResolvedValueOnce({ ...response, expires_at: 'later' })
    await expect(previewAssistantAction(candidate, new AbortController().signal)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('rejects unexpected preview fields and malformed changes', async () => {
    mocks.authRequest.mockResolvedValueOnce({ ...response, execution_mode: 'approved' })
    await expect(previewAssistantAction(candidate, new AbortController().signal)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
    mocks.authRequest.mockResolvedValueOnce({ ...response, changes: [{ ...response.changes[0], secret: 'unexpected' }] })
    await expect(previewAssistantAction(candidate, new AbortController().signal)).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })
})
