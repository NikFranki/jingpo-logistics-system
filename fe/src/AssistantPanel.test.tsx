import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const mocks = vi.hoisted(() => ({
  auth: { session: null as { user: { id: string; role: string; authVersion: number }; loginSessionId: string } | null },
  createAssistantSession: vi.fn(),
  deleteAssistantSession: vi.fn(),
  queryAssistant: vi.fn(),
  previewAssistantAction: vi.fn(),
  executeAssistantAction: vi.fn(),
  readAssistantAction: vi.fn(),
  rejectAssistantAction: vi.fn(),
  syncAssistantAction: vi.fn(),
  readAssistantWritePolicy: vi.fn(),
  updateAssistantWritePolicy: vi.fn(),
}))

vi.mock('./auth/AuthContext', () => ({ useAuth: () => mocks.auth }))
vi.mock('./assistantApi', () => ({
  AssistantError: class AssistantError extends Error {
    code: string
    constructor(message: string, code: string) { super(message); this.code = code }
  },
  createAssistantSession: mocks.createAssistantSession,
  deleteAssistantSession: mocks.deleteAssistantSession,
  queryAssistant: mocks.queryAssistant,
  previewAssistantAction: mocks.previewAssistantAction,
  executeAssistantAction: mocks.executeAssistantAction,
  readAssistantAction: mocks.readAssistantAction,
  rejectAssistantAction: mocks.rejectAssistantAction,
  syncAssistantAction: mocks.syncAssistantAction,
  readAssistantWritePolicy: mocks.readAssistantWritePolicy,
  updateAssistantWritePolicy: mocks.updateAssistantWritePolicy,
  referenceLabels: { order: '订单', shipment: '运单', transport_task: '运输任务' },
  referencePaths: { order: '/orders', shipment: '/shipments', transport_task: '/tasks' },
}))

import AssistantPanel from './AssistantPanel'

const candidate = {
  candidate_id: 'c08b469b-522a-4bd7-b66e-26d9d96a0f21',
  agent_session_id: 'b766748f-2361-4e08-9724-9bcb0fdaf9eb',
  message_id: '39f2147a-4d46-4b39-9078-c674f1f266b2',
  action_type: 'order.update',
  subject_type: 'order',
  subject_id: '42',
  payload: { product_name: 'new product' },
}
const preview = {
  proposal_id: '56edb1a8-4b2a-481a-b9fa-2d9a06dc632b',
  candidate_id: candidate.candidate_id,
  action_type: candidate.action_type,
  subject: { type: 'order', id: '42', number: 'ORD-000042' },
  changes: [{ field: 'product_name', label: '货品名称', before: 'old product', after: 'new product' }],
  impact: ['仅修改待发货订单'],
  execution_policy: 'CONFIRM_REQUIRED',
  expires_at: '2030-01-01T00:05:00Z',
}

function prepareIdentity() {
  mocks.auth.session = {
    user: { id: 'user-42', role: 'user', authVersion: 3 },
    loginSessionId: 'login-42',
  }
}

function renderPanel() {
  return render(<MemoryRouter><AssistantPanel /></MemoryRouter>)
}

async function requestAction() {
  const user = userEvent.setup()
  renderPanel()
  await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))
  await user.type(screen.getByRole('textbox', { name: '输入物流查询问题' }), '修改订单货品名称')
  await user.click(screen.getByRole('button', { name: /发送/ }))
  await screen.findByText('订单修改预览')
  return user
}

describe('AssistantPanel controlled actions', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 })
    Object.defineProperty(document.documentElement, 'clientWidth', { configurable: true, value: 1280 })
    Object.defineProperty(document.documentElement, 'clientHeight', { configurable: true, value: 800 })
    prepareIdentity()
    sessionStorage.clear()
    mocks.createAssistantSession.mockResolvedValue('agent-session')
    mocks.deleteAssistantSession.mockResolvedValue(undefined)
    mocks.queryAssistant.mockImplementation(async (_sessionId: string, messageId: string) => ({
      message_id: messageId, answer: '请核对订单修改。', references: [],
      references_truncated: false, warnings: [], stop_reason: 'completed',
      usage: { model_calls: 1, tool_calls: 1, http_calls: 1 }, trace_id: 'trace-v11',
      action_candidate: { ...candidate, message_id: messageId },
    }))
    mocks.previewAssistantAction.mockResolvedValue(preview)
    mocks.executeAssistantAction.mockResolvedValue({ status: 'SUCCEEDED' })
    mocks.readAssistantAction.mockResolvedValue({ status: 'PREVIEWED' })
    mocks.rejectAssistantAction.mockResolvedValue({ status: 'REJECTED' })
    mocks.syncAssistantAction.mockResolvedValue({
      candidate_id: candidate.candidate_id, proposal_id: preview.proposal_id,
      status: 'SUCCEEDED', action_type: candidate.action_type,
      result_summary: { summary: { status: 'PENDING_SHIPMENT' } },
    })
    mocks.readAssistantWritePolicy.mockResolvedValue({ enabled_action_types: [], eligible_action_types: [], version: 0 })
    mocks.updateAssistantWritePolicy.mockImplementation(async (enabledActionTypes: string[]) => ({
      enabled_action_types: enabledActionTypes, eligible_action_types: ['order.update'], version: 1,
    }))
  })

  afterEach(() => { cleanup(); sessionStorage.clear() })

  it('loads the server policy and explains when no action is eligible for automatic execution', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))
    expect(await screen.findByText('暂无可自动执行操作 · 修改前先预览并由你确认')).toBeTruthy()
    expect(mocks.readAssistantWritePolicy).toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '自动执行设置' })).toBeNull()
  })

  it('shows only eligible server actions and saves the selected preference', async () => {
    mocks.readAssistantWritePolicy.mockResolvedValue({
      enabled_action_types: [], eligible_action_types: ['order.update'], version: 2,
    })
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))
    expect(mocks.readAssistantWritePolicy).toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: /自动执行设置/ }))

    const orderSwitch = screen.getByRole('switch', { name: '修改订单自动执行' })
    expect(screen.queryByRole('switch', { name: '更正运单目的站自动执行' })).toBeNull()
    await user.click(orderSwitch)

    expect(mocks.updateAssistantWritePolicy).toHaveBeenCalledWith(['order.update'], expect.any(AbortSignal))
    expect(screen.getByRole('switch', { name: '修改订单自动执行' }).getAttribute('aria-checked')).toBe('true')
  })

  it('does not execute before confirmation and shows the verified result afterward', async () => {
    const user = await requestAction()

    expect(screen.getByText('原值：old product')).toBeTruthy()
    expect(screen.getByText('新值：new product')).toBeTruthy()
    expect(screen.getByRole('link', { name: '订单 ORD-000042' }).getAttribute('href')).toBe('/orders/42')
    expect(mocks.executeAssistantAction).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '确认执行' }))

    expect(mocks.executeAssistantAction).toHaveBeenCalledWith(preview.proposal_id, 'approved', expect.any(AbortSignal))
    expect(await screen.findByText('已完成：PENDING_SHIPMENT。')).toBeTruthy()
    expect(mocks.syncAssistantAction).toHaveBeenCalledWith(
      candidate.agent_session_id, candidate.candidate_id, preview.proposal_id, expect.any(AbortSignal),
    )
  })

  it('shows preview expiry and rechecks the same proposal at the deadline', async () => {
    mocks.previewAssistantAction.mockResolvedValueOnce({
      ...preview, expires_at: new Date(Date.now() + 500).toISOString(),
    })
    mocks.readAssistantAction.mockResolvedValueOnce({ status: 'PREVIEWED' })
    await requestAction()

    expect(screen.getByText(/有效期：剩余/)).toBeTruthy()
    expect(screen.getByText('执行方式：需你逐次确认')).toBeTruthy()
    expect(await screen.findByText('服务端确认提案仍有效，请尽快确认。', {}, { timeout: 3000 })).toBeTruthy()
    expect(mocks.readAssistantAction).toHaveBeenCalledWith(preview.proposal_id)
    expect(mocks.executeAssistantAction).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '确认执行' })).toBeTruthy()
  })

  it('puts the named action preview card in the keyboard focus order', async () => {
    await requestAction()
    const card = screen.getByRole('region', { name: '订单修改预览' })

    expect(card.getAttribute('tabindex')).toBe('0')
    card.focus()
    expect(document.activeElement).toBe(card)
  })

  it('cancels a proposal without calling the business execute endpoint', async () => {
    const user = await requestAction()
    mocks.syncAssistantAction.mockResolvedValueOnce({
      candidate_id: candidate.candidate_id, proposal_id: preview.proposal_id,
      status: 'REJECTED', action_type: candidate.action_type,
    })

    await user.click(screen.getByRole('button', { name: '取消本次修改' }))

    expect(mocks.rejectAssistantAction).toHaveBeenCalledWith(preview.proposal_id, expect.any(AbortSignal))
    expect(mocks.executeAssistantAction).not.toHaveBeenCalled()
    expect(await screen.findByText('本次修改已取消。')).toBeTruthy()
  })

  it('keeps confirmation if AUTO_ALLOWED is not backed by the user policy and eligible list', async () => {
    mocks.previewAssistantAction.mockResolvedValueOnce({ ...preview, execution_policy: 'AUTO_ALLOWED' })
    await requestAction()

    expect(mocks.executeAssistantAction).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '确认执行' })).toBeTruthy()
  })

  it('automatically executes only when BE preview, eligibility, and user opt-in all allow it', async () => {
    mocks.readAssistantWritePolicy.mockResolvedValueOnce({
      enabled_action_types: ['order.update'], eligible_action_types: ['order.update'], version: 4,
    })
    mocks.previewAssistantAction.mockResolvedValueOnce({ ...preview, execution_policy: 'AUTO_ALLOWED' })
    await requestAction()

    expect(mocks.executeAssistantAction).toHaveBeenCalledWith(preview.proposal_id, 'automatic', expect.any(AbortSignal))
    expect(await screen.findByText(/已完成：PENDING_SHIPMENT/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull()
  })

  it('returns an unexecuted automatic proposal to explicit confirmation without retrying execute', async () => {
    mocks.readAssistantWritePolicy.mockResolvedValueOnce({
      enabled_action_types: ['order.update'], eligible_action_types: ['order.update'], version: 4,
    })
    mocks.previewAssistantAction.mockResolvedValueOnce({ ...preview, execution_policy: 'AUTO_ALLOWED' })
    mocks.executeAssistantAction.mockRejectedValueOnce(new Error('当前策略已关闭'))
    mocks.readAssistantAction.mockResolvedValueOnce({ status: 'PREVIEWED' })
    await requestAction()

    expect(await screen.findByText('自动执行条件已变化，请核对后确认。')).toBeTruthy()
    expect(screen.getByRole('button', { name: '确认执行' })).toBeTruthy()
    expect(mocks.executeAssistantAction).toHaveBeenCalledTimes(1)
  })

  it('keeps an uncertain execution pending and only offers status lookup', async () => {
    const user = await requestAction()
    mocks.executeAssistantAction.mockRejectedValueOnce(new Error('请求超时'))
    mocks.readAssistantAction.mockResolvedValueOnce({ status: 'PREVIEWED' })

    await user.click(screen.getByRole('button', { name: '确认执行' }))

    expect(await screen.findByText('执行请求结果未确认，请查询同一提案状态。')).toBeTruthy()
    expect(screen.getByRole('button', { name: '核对同一提案状态' })).toBeTruthy()
    expect(screen.queryByText(/已完成/)).toBeNull()
    expect(mocks.executeAssistantAction).toHaveBeenCalledTimes(1)
  })

  it('recovers a timed-out confirmation when BE already committed the proposal', async () => {
    const user = await requestAction()
    mocks.executeAssistantAction.mockRejectedValueOnce(new Error('请求超时'))
    mocks.readAssistantAction.mockResolvedValueOnce({ status: 'SUCCEEDED' })

    await user.click(screen.getByRole('button', { name: '确认执行' }))

    expect(await screen.findByText('已完成：PENDING_SHIPMENT。')).toBeTruthy()
    expect(mocks.executeAssistantAction).toHaveBeenCalledTimes(1)
    expect(mocks.readAssistantAction).toHaveBeenCalledWith(preview.proposal_id, expect.any(AbortSignal))
    expect(mocks.syncAssistantAction).toHaveBeenCalledWith(
      candidate.agent_session_id, candidate.candidate_id, preview.proposal_id, expect.any(AbortSignal),
    )
  })

  it('recovers when BE committed but Agent verification temporarily failed', async () => {
    const user = await requestAction()
    mocks.syncAssistantAction.mockRejectedValueOnce(new Error('Agent 暂不可用'))
    mocks.readAssistantAction.mockResolvedValueOnce({ status: 'SUCCEEDED' })

    await user.click(screen.getByRole('button', { name: '确认执行' }))

    expect(await screen.findByText('后端结果已收到，但助手尚未完成核验；可继续查询同一提案。')).toBeTruthy()
    expect(mocks.executeAssistantAction).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: '核对同一提案状态' }))

    expect(await screen.findByText('已完成：PENDING_SHIPMENT。')).toBeTruthy()
    expect(mocks.executeAssistantAction).toHaveBeenCalledTimes(1)
    expect(mocks.readAssistantAction).toHaveBeenCalledWith(preview.proposal_id)
  })

  it('keeps a failed preview non-executable and retries the same candidate on request', async () => {
    mocks.previewAssistantAction.mockRejectedValueOnce(new Error('预览接口暂不可用'))
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))
    await user.type(screen.getByRole('textbox', { name: '输入物流查询问题' }), '修改订单货品名称')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText('预览接口暂不可用')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull()
    expect(mocks.executeAssistantAction).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '重试获取预览' }))
    expect(await screen.findByRole('button', { name: '确认执行' })).toBeTruthy()
    expect(mocks.previewAssistantAction).toHaveBeenCalledTimes(2)
    expect(mocks.previewAssistantAction.mock.calls[1][0].candidate_id).toBe(candidate.candidate_id)
    expect(mocks.executeAssistantAction).not.toHaveBeenCalled()
  })

  it('clears stale preview values and offers a fresh proposal after BE rejects the write', async () => {
    const user = await requestAction()
    mocks.executeAssistantAction.mockRejectedValueOnce(new Error('运单状态已变化，请重新预览'))
    mocks.readAssistantAction.mockResolvedValueOnce({ status: 'FAILED' })
    mocks.syncAssistantAction.mockResolvedValueOnce({
      candidate_id: candidate.candidate_id, proposal_id: preview.proposal_id,
      status: 'FAILED', action_type: candidate.action_type,
    })

    await user.click(screen.getByRole('button', { name: '确认执行' }))

    expect(await screen.findByText('修改未完成，请刷新数据后重新提出。')).toBeTruthy()
    expect(screen.queryByText('原值：old product')).toBeNull()
    expect(screen.queryByText('新值：new product')).toBeNull()
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull()
    expect(screen.getByRole('button', { name: '重新提出修改' })).toBeTruthy()
  })

  it('keeps pending proposals and their Agent session when starting a new conversation', async () => {
    const user = await requestAction()
    await user.click(screen.getByRole('button', { name: '开始新对话' }))

    expect(screen.getByText('新对话已开始；此提案仍绑定原会话，可继续核对或处理。')).toBeTruthy()
    expect(mocks.deleteAssistantSession).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('jp-assistant-action:user-42:login-42:user:3')).toContain(preview.proposal_id)
  })

  it('clears the previous account conversation when the active login changes', async () => {
    const view = renderPanel()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))
    await user.type(screen.getByRole('textbox', { name: '输入物流查询问题' }), '修改订单货品名称')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    await screen.findByText('订单修改预览')

    mocks.auth.session = {
      user: { id: 'user-99', role: 'user', authVersion: 1 }, loginSessionId: 'login-99',
    }
    view.rerender(<MemoryRouter><AssistantPanel /></MemoryRouter>)

    expect(screen.queryByText('修改订单货品名称')).toBeNull()
    expect(screen.queryByText('订单修改预览')).toBeNull()
    expect(screen.getByText('从一个问题开始')).toBeTruthy()
  })

  it('restores status after refresh without restoring a confirmation button', async () => {
    const secondAction = {
      agentSessionId: candidate.agent_session_id,
      candidateId: 'b08b469b-522a-4bd7-b66e-26d9d96a0f21',
      messageId: '49f2147a-4d46-4b39-9078-c674f1f266b2',
      actionType: candidate.action_type,
      subjectType: candidate.subject_type,
      subjectId: candidate.subject_id,
      proposalId: '66edb1a8-4b2a-481a-b9fa-2d9a06dc632b',
    }
    sessionStorage.setItem('jp-assistant-action:user-42:login-42:user:3', JSON.stringify([
      { ...secondAction, candidateId: candidate.candidate_id, messageId: candidate.message_id, proposalId: preview.proposal_id },
      secondAction,
    ]))
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))

    expect((await screen.findAllByText('上次的业务修改提案')).length).toBe(2)
    expect((await screen.findAllByText('提案仍待确认；本页面不会自动重试写入。')).length).toBe(2)
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull()
    expect(mocks.executeAssistantAction).not.toHaveBeenCalled()
  })

  it('supports keyboard movement and resizing within viewport bounds', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))
    const panel = document.querySelector<HTMLElement>('.jp-assistant')!
    const move = screen.getByRole('button', { name: '移动助手窗口，可用方向键调整位置' })
    const southeast = screen.getByRole('button', { name: '从右下角调整窗口大小，可用方向键调整' })

    expect(panel.style.left).toBe('816px')
    fireEvent.keyDown(move, { key: 'ArrowLeft' })
    expect(panel.style.left).toBe('796px')

    expect(panel.style.width).toBe('440px')
    fireEvent.keyDown(southeast, { key: 'ArrowRight' })
    expect(panel.style.width).toBe('460px')
    expect(screen.getByText('助手窗口大小已调整为宽 460、高 604 像素。').getAttribute('aria-live')).toBe('polite')
    fireEvent.keyDown(southeast, { key: 'ArrowDown' })
    expect(panel.style.height).toBe('624px')
  })

  it.each([
    ['nw', '左上', -40, -30, 480, 634, 776, 134],
    ['ne', '右上', 40, -30, 464, 634, 816, 134],
    ['sw', '左下', -40, 30, 480, 634, 776, 164],
    ['se', '右下', 40, 30, 464, 634, 816, 164],
  ] as const)('resizes from the %s corner and keeps the opposite corner anchored', async (_corner, label, dx, dy, width, height, left, top) => {
    renderPanel()
    await userEvent.setup().click(screen.getByRole('button', { name: '打开物流查询助手' }))
    const panel = document.querySelector<HTMLElement>('.jp-assistant')!
    const handle = screen.getByRole('button', { name: `从${label}角调整窗口大小，可用方向键调整` })
    Object.defineProperty(handle, 'setPointerCapture', { value: vi.fn() })

    fireEvent.pointerDown(handle, { pointerId: 1, pointerType: 'touch', button: 0, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(handle, { pointerId: 1, pointerType: 'touch', clientX: 100 + dx, clientY: 100 + dy })
    fireEvent.pointerUp(handle, { pointerId: 1, pointerType: 'touch' })

    expect(panel.style.width).toBe(`${width}px`)
    expect(panel.style.height).toBe(`${height}px`)
    expect(panel.style.left).toBe(`${left}px`)
    expect(panel.style.top).toBe(`${top}px`)
  })

  it.each([
    ['nw', '左上', 1000, 1000, 320, 320, 936, 448],
    ['ne', '右上', -1000, 1000, 320, 320, 816, 448],
    ['sw', '左下', 1000, -1000, 320, 320, 936, 164],
    ['se', '右下', -1000, -1000, 320, 320, 816, 164],
  ] as const)('keeps the %s corner resize above minimum dimensions', async (_corner, label, dx, dy, width, height, left, top) => {
    renderPanel()
    await userEvent.setup().click(screen.getByRole('button', { name: '打开物流查询助手' }))
    const panel = document.querySelector<HTMLElement>('.jp-assistant')!
    const handle = screen.getByRole('button', { name: `从${label}角调整窗口大小，可用方向键调整` })
    Object.defineProperty(handle, 'setPointerCapture', { value: vi.fn() })

    fireEvent.pointerDown(handle, { pointerId: 1, pointerType: 'touch', button: 0, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(handle, { pointerId: 1, pointerType: 'touch', clientX: 100 + dx, clientY: 100 + dy })

    expect(panel.style.width).toBe(`${width}px`)
    expect(panel.style.height).toBe(`${height}px`)
    expect(panel.style.left).toBe(`${left}px`)
    expect(panel.style.top).toBe(`${top}px`)
  })

  it('keeps a dragged corner inside the viewport at maximum dimensions', async () => {
    renderPanel()
    await userEvent.setup().click(screen.getByRole('button', { name: '打开物流查询助手' }))
    const panel = document.querySelector<HTMLElement>('.jp-assistant')!
    const handle = screen.getByRole('button', { name: '从右下角调整窗口大小，可用方向键调整' })
    Object.defineProperty(handle, 'setPointerCapture', { value: vi.fn() })

    fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: 900, clientY: 700 })
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 2000, clientY: 2000 })

    expect(panel.style.left).toBe('816px')
    expect(panel.style.top).toBe('164px')
    expect(panel.style.width).toBe('464px')
    expect(panel.style.height).toBe('636px')
  })

  it('drags from the title area while keeping header buttons outside the drag gesture', async () => {
    renderPanel()
    await userEvent.setup().click(screen.getByRole('button', { name: '打开物流查询助手' }))
    const panel = document.querySelector<HTMLElement>('.jp-assistant')!
    const title = screen.getByRole('button', { name: '移动助手窗口，可用方向键调整位置' })
    const newConversation = screen.getByRole('button', { name: '开始新对话' })
    Object.defineProperty(title, 'setPointerCapture', { value: vi.fn() })

    fireEvent.pointerDown(title, { pointerId: 1, pointerType: 'touch', button: 0, clientX: 900, clientY: 200 })
    fireEvent.pointerMove(title, { pointerId: 1, pointerType: 'touch', clientX: 850, clientY: 150 })
    fireEvent.pointerUp(title, { pointerId: 1, pointerType: 'touch' })
    expect(panel.style.left).toBe('766px')
    expect(panel.style.top).toBe('114px')

    fireEvent.pointerDown(newConversation, { pointerId: 2, pointerType: 'touch', button: 0, clientX: 1000, clientY: 200 })
    fireEvent.pointerMove(newConversation, { pointerId: 2, pointerType: 'touch', clientX: 1200, clientY: 400 })
    fireEvent.pointerUp(newConversation, { pointerId: 2, pointerType: 'touch' })
    expect(panel.style.left).toBe('766px')
    expect(panel.style.top).toBe('114px')
  })

  it('uses the available narrow viewport width and hides desktop resize controls', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    Object.defineProperty(document.documentElement, 'clientWidth', { configurable: true, value: 390 })
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))

    const panel = document.querySelector<HTMLElement>('.jp-assistant')!
    expect(panel.style.left).toBe('8px')
    expect(panel.style.right).toBe('8px')
    expect(panel.style.width).toBe('auto')
    expect(screen.queryByRole('button', { name: /角调整窗口大小/ })).toBeNull()
  })

  it('shows an expired proposal after refresh without restoring confirmation', async () => {
    mocks.readAssistantAction.mockResolvedValueOnce({ status: 'EXPIRED' })
    mocks.syncAssistantAction.mockResolvedValueOnce({
      candidate_id: candidate.candidate_id, proposal_id: preview.proposal_id,
      status: 'EXPIRED', action_type: candidate.action_type,
    })
    sessionStorage.setItem('jp-assistant-action:user-42:login-42:user:3', JSON.stringify([{
      agentSessionId: candidate.agent_session_id,
      candidateId: candidate.candidate_id,
      messageId: candidate.message_id,
      actionType: candidate.action_type,
      subjectType: candidate.subject_type,
      subjectId: candidate.subject_id,
      proposalId: preview.proposal_id,
    }]))
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))

    expect(await screen.findByText('提案已过期，请重新提出修改。')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull()
    expect(screen.getByRole('button', { name: '重新描述修改' })).toBeTruthy()
    expect(screen.queryByText('原值：old product')).toBeNull()
    expect(mocks.executeAssistantAction).not.toHaveBeenCalled()
  })

  it('keeps status unknown when the Agent session was lost after a successful BE write', async () => {
    mocks.readAssistantAction.mockResolvedValueOnce({ status: 'SUCCEEDED' })
    mocks.syncAssistantAction.mockRejectedValueOnce(new Error('Agent 会话已失效'))
    sessionStorage.setItem('jp-assistant-action:user-42:login-42:user:3', JSON.stringify([{
      agentSessionId: candidate.agent_session_id,
      candidateId: candidate.candidate_id,
      messageId: candidate.message_id,
      actionType: candidate.action_type,
      subjectType: candidate.subject_type,
      subjectId: candidate.subject_id,
      proposalId: preview.proposal_id,
    }]))
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '打开物流查询助手' }))

    expect(await screen.findByText('Agent 会话已失效')).toBeTruthy()
    expect(screen.queryByText(/已完成：/)).toBeNull()
    expect(screen.getByRole('button', { name: '核对同一提案状态' })).toBeTruthy()
  })
})
