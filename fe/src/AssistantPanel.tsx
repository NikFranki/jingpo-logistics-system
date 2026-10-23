import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { Alert, Button, Modal, Spin, Switch, Tooltip } from 'antd'
import { DragOutlined, ExpandOutlined, MessageOutlined, MinusOutlined, PlusOutlined, SendOutlined, SettingOutlined, ShrinkOutlined, StopOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AssistantError, createAssistantSession, deleteAssistantSession, queryAssistant, readAssistantWritePolicy, referenceLabels, referencePaths, updateAssistantWritePolicy, type ActionType, type AssistantAnswer, type AssistantWritePolicy } from './assistantApi'
import './assistant.css'
import { useAuth } from './auth/AuthContext'
import { AuthRequestError } from './auth/transport'
import {
  executeAssistantAction, previewAssistantAction, readAssistantAction,
  rejectAssistantAction, syncAssistantAction,
  type ActionCandidate, type ActionPreview, type ActionSyncResult,
} from './assistantApi'

type Geometry = { x: number; y: number; width: number; height: number }
type GestureMode = 'move' | 'nw' | 'ne' | 'sw' | 'se'
type ActionStatus = 'PREVIEW_READY_CONFIRM' | 'PREVIEW_READY_AUTO' | 'EXECUTING' | 'SUCCEEDED' | 'REJECTED' | 'EXPIRED' | 'FAILED' | 'UNKNOWN'
type AssistantAction = { candidate: ActionCandidate; preview?: ActionPreview; proposalId?: string; status: ActionStatus; message?: string; outcome?: ActionSyncResult }
type ChatMessage = { id: string; role: 'user' | 'assistant'; text: string; answer?: AssistantAnswer; action?: AssistantAction; error?: boolean; retry?: { text: string; messageId: string } }

function readStored(key: string) {
  try { return sessionStorage.getItem(key) } catch { return null }
}

function writeStored(key: string, value: string | null) {
  try { if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value) } catch { /* Storage can be disabled by browser policy. */ }
}

type StoredAction = {
  agentSessionId: string
  candidateId: string
  messageId: string
  actionType: ActionCandidate['action_type']
  subjectType: ActionCandidate['subject_type']
  subjectId: string
  proposalId: string
}

function storedActions(key: string): StoredAction[] {
  const raw = readStored(key)
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    return parsed.filter((item): item is StoredAction => Boolean(item && typeof item === 'object' &&
      typeof item.agentSessionId === 'string' && uuid.test(item.agentSessionId) &&
      typeof item.candidateId === 'string' && uuid.test(item.candidateId) &&
      typeof item.messageId === 'string' && uuid.test(item.messageId) &&
      typeof item.proposalId === 'string' && uuid.test(item.proposalId) &&
      ['order.update', 'shipment.destination.update'].includes(item.actionType) &&
      ['order', 'shipment'].includes(item.subjectType) && typeof item.subjectId === 'string' && /^[1-9][0-9]{0,18}$/.test(item.subjectId)))
  } catch { return [] }
}

function actionStatusText(status: ActionStatus | ActionSyncResult['status']) {
  return ({
    PREVIEW_READY_CONFIRM: '请核对变更内容后确认。', PREVIEW_READY_AUTO: '已按你启用的自动执行设置提交。',
    EXECUTING: '正在处理。', SUCCEEDED: '已完成。', REJECTED: '本次修改已取消。',
    EXPIRED: '提案已过期，请重新提出修改。', FAILED: '修改未完成，请刷新数据后重新提出。',
    UNKNOWN: '执行结果暂时无法确认。',
  } as Record<ActionStatus, string>)[status as ActionStatus]
}

function displayActionValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未设置'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function formatActionExpiry(expiresAt: string, now: number) {
  const expiry = Date.parse(expiresAt)
  if (!Number.isFinite(expiry)) return '有效期无法确认'
  const deadline = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(expiry))
  const seconds = Math.max(0, Math.ceil((expiry - now) / 1000))
  if (!seconds) return `预览已到截止时间，正在核对服务端状态（${deadline}）`
  const remaining = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
  return `剩余 ${remaining} · 截止 ${deadline}`
}

function constrain(box: Geometry): Geometry {
  const viewportWidth = document.documentElement.clientWidth
  const viewportHeight = document.documentElement.clientHeight
  const maxWidth = Math.max(1, Math.floor(viewportWidth / 2))
  const maxHeight = Math.max(1, viewportHeight)
  const minWidth = Math.min(320, maxWidth)
  const minHeight = Math.min(320, maxHeight)
  const width = Math.max(minWidth, Math.min(box.width, maxWidth))
  const height = Math.max(minHeight, Math.min(box.height, maxHeight))
  return { width, height, x: Math.max(0, Math.min(box.x, viewportWidth - width)), y: Math.max(0, Math.min(box.y, viewportHeight - height)) }
}

function resizeFromCorner(box: Geometry, corner: Exclude<GestureMode, 'move'>, dx: number, dy: number): Geometry {
  const west = corner.includes('w')
  const north = corner.includes('n')
  const viewportWidth = document.documentElement.clientWidth
  const viewportHeight = document.documentElement.clientHeight
  const maxWidth = Math.min(Math.floor(viewportWidth / 2), west ? box.x + box.width : viewportWidth - box.x)
  const maxHeight = Math.min(viewportHeight, north ? box.y + box.height : viewportHeight - box.y)
  const minWidth = Math.min(320, maxWidth)
  const minHeight = Math.min(320, maxHeight)
  const width = Math.max(minWidth, Math.min(box.width + (west ? -dx : dx), maxWidth))
  const height = Math.max(minHeight, Math.min(box.height + (north ? -dy : dy), maxHeight))
  return {
    x: west ? box.x + box.width - width : box.x,
    y: north ? box.y + box.height - height : box.y,
    width,
    height,
  }
}

export default function AssistantPanel() {
  const auth = useAuth()
  const ownScope = auth.session?.user.role === 'user'
  const identityKey = auth.session ? `${auth.session.user.id}:${auth.session.loginSessionId ?? ''}:${auth.session.user.role}:${auth.session.user.authVersion}` : 'anonymous'
  const [open, setOpen] = useState(false)
  const [maximized, setMaximized] = useState(false)
  const [compact, setCompact] = useState(window.innerWidth <= 640)
  const [geometry, setGeometry] = useState(() => constrain({ x: window.innerWidth - 464, y: window.innerHeight - 636, width: 440, height: 604 }))
  const [geometryAnnouncement, setGeometryAnnouncement] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [now, setNow] = useState(() => Date.now())
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionBusy, setActionBusy] = useState(false)
  const [writePolicy, setWritePolicy] = useState<AssistantWritePolicy | null>(null)
  const [policyLoading, setPolicyLoading] = useState(false)
  const [policyBusy, setPolicyBusy] = useState(false)
  const [policyOpen, setPolicyOpen] = useState(false)
  const [policyError, setPolicyError] = useState('')
  const [progress, setProgress] = useState('思考中')
  const session = useRef<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const launcher = useRef<HTMLButtonElement>(null)
  const log = useRef<HTMLDivElement>(null)
  const [gesture, setGesture] = useState<{ mode: GestureMode; x: number; y: number; box: Geometry } | null>(null)
  const previousIdentity = useRef(identityKey)
  const actionRequest = useRef<AbortController | null>(null)
  const expirationChecks = useRef(new Set<string>())
  const checkActionRef = useRef<(messageId: string, action: AssistantAction) => Promise<void>>(async () => {})
  const sessionStorageKey = `jp-assistant-session:${identityKey}`
  const actionStorageKey = `jp-assistant-action:${identityKey}`
  const terminalActionStatuses: ActionStatus[] = ['SUCCEEDED', 'REJECTED', 'EXPIRED', 'FAILED']

  useEffect(() => {
    const resize = () => { setCompact(window.innerWidth <= 640); setGeometry(box => constrain(box)) }
    window.addEventListener('resize', resize)
    return () => { window.removeEventListener('resize', resize); controller.current?.abort() }
  }, [])

  useEffect(() => {
    if (open) input.current?.focus()
  }, [open])

  useEffect(() => {
    if (open && log.current) log.current.scrollTop = log.current.scrollHeight
  }, [messages, busy, open])

  useEffect(() => {
    if (!messages.some(item => item.action?.preview && item.action.status === 'PREVIEW_READY_CONFIRM')) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [messages])

  useEffect(() => {
    if (previousIdentity.current === identityKey) return
    previousIdentity.current = identityKey
    session.current = null
    controller.current?.abort()
    actionRequest.current?.abort()
    actionRequest.current = null
    setMessages([])
  }, [identityKey])

  const collapse = () => { setOpen(false); requestAnimationFrame(() => launcher.current?.focus()) }
  const persistAction = (action: AssistantAction) => {
    const proposalId = action.preview?.proposal_id ?? action.proposalId
    if (proposalId) {
      const existing = storedActions(actionStorageKey).filter(item => item.proposalId !== proposalId)
      if (terminalActionStatuses.includes(action.status)) {
        writeStored(actionStorageKey, existing.length ? JSON.stringify(existing) : null)
        return
      }
      const record: StoredAction = {
        agentSessionId: action.candidate.agent_session_id,
        candidateId: action.candidate.candidate_id,
        messageId: action.candidate.message_id,
        actionType: action.candidate.action_type,
        subjectType: action.candidate.subject_type,
        subjectId: action.candidate.subject_id,
        proposalId,
      }
      existing.push(record)
      const actions = existing
      writeStored(actionStorageKey, JSON.stringify(actions.slice(-20)))
    }
  }
  const changeAction = (messageId: string, action: AssistantAction) => {
    setMessages(items => items.map(item => item.id === messageId ? { ...item, action } : item))
    persistAction(action)
  }
  const removeStoredAction = (proposalId: string) => {
    const remaining = storedActions(actionStorageKey).filter(item => item.proposalId !== proposalId)
    writeStored(actionStorageKey, remaining.length ? JSON.stringify(remaining) : null)
  }
  const finishAction = async (action: AssistantAction, mode: 'approved' | 'automatic', signal?: AbortSignal): Promise<AssistantAction> => {
    if (!action.preview) return { ...action, status: 'FAILED', message: '缺少后端确认预览，不能执行。' }
    try {
      await executeAssistantAction(action.preview.proposal_id, mode, signal)
    } catch (error) {
      try {
        const current = await readAssistantAction(action.preview.proposal_id, signal)
        if (mode === 'automatic' && current.status === 'PREVIEWED') {
          return { ...action, status: 'PREVIEW_READY_CONFIRM', message: '自动执行条件已变化，请核对后确认。' }
        }
        if (!['SUCCEEDED', 'REJECTED', 'EXPIRED', 'FAILED'].includes(String(current.status))) {
          return { ...action, status: 'UNKNOWN', message: '执行请求结果未确认，请查询同一提案状态。' }
        }
      } catch {
        return { ...action, status: 'UNKNOWN', message: error instanceof Error ? error.message : '执行结果暂时无法确认。' }
      }
    }
    try {
      const outcome = await syncAssistantAction(action.candidate.agent_session_id, action.candidate.candidate_id,
        action.preview.proposal_id, signal)
      removeStoredAction(action.preview.proposal_id)
      return { ...action, status: outcome.status, outcome, message: actionStatusText(outcome.status) }
    } catch {
      return { ...action, status: 'UNKNOWN', message: '后端结果已收到，但助手尚未完成核验；可继续查询同一提案。' }
    }
  }
  const checkAction = async (messageId: string, action: AssistantAction) => {
    const proposalId = action.preview?.proposal_id ?? action.proposalId
    if (!proposalId) return
    try {
      const current = await readAssistantAction(proposalId)
      if (['SUCCEEDED', 'REJECTED', 'EXPIRED', 'FAILED'].includes(String(current.status))) {
        const outcome = await syncAssistantAction(action.candidate.agent_session_id, action.candidate.candidate_id, proposalId)
        removeStoredAction(proposalId)
        changeAction(messageId, { ...action, status: outcome.status, outcome, message: actionStatusText(outcome.status) })
      } else {
        const stillConfirmable = current.status === 'PREVIEWED' && action.status === 'PREVIEW_READY_CONFIRM'
        changeAction(messageId, {
          ...action,
          status: stillConfirmable ? 'PREVIEW_READY_CONFIRM' : 'UNKNOWN',
          message: stillConfirmable ? '服务端确认提案仍有效，请尽快确认。' : '提案仍待确认；本页面不会自动重试写入。',
        })
      }
    } catch (error) {
      changeAction(messageId, { ...action, status: 'UNKNOWN', message: error instanceof Error ? error.message : '暂时无法核对提案状态。' })
    }
  }
  useEffect(() => { checkActionRef.current = checkAction })

  useEffect(() => {
    const timers = messages.flatMap(item => {
      const action = item.action
      const proposalId = action?.preview?.proposal_id
      if (!action || !proposalId || action.status !== 'PREVIEW_READY_CONFIRM') return []
      const delay = Date.parse(action.preview!.expires_at) - Date.now()
      if (delay > 0 || expirationChecks.current.has(proposalId)) return []
      expirationChecks.current.add(proposalId)
      return [window.setTimeout(() => void checkActionRef.current(item.id, action), 0)]
    })
    return () => timers.forEach(timer => window.clearTimeout(timer))
  }, [messages, now])

  const retryPreview = async (messageId: string, action: AssistantAction) => {
    if (actionRequest.current) return
    const request = new AbortController()
    actionRequest.current = request
    setActionBusy(true)
    changeAction(messageId, { ...action, status: 'EXECUTING', message: '正在重新获取后端预览。' })
    try {
      const preview = await previewAssistantAction(action.candidate, request.signal)
      changeAction(messageId, { ...action, preview, proposalId: preview.proposal_id, status: 'PREVIEW_READY_CONFIRM', message: '请核对变更内容后确认。' })
    } catch (error) {
      changeAction(messageId, { ...action, status: 'FAILED', message: error instanceof Error ? error.message : '后端无法生成操作预览。' })
    } finally {
      if (actionRequest.current === request) actionRequest.current = null
      setActionBusy(false)
    }
  }

  const reproposeAction = (action: AssistantAction) => {
    const original = messages.find(item => item.role === 'user' && item.id === action.candidate.message_id)
    if (original) {
      void send(original.text)
      return
    }
    setDraft(action.candidate.action_type === 'order.update'
      ? '请重新描述要修改的订单、字段和值。'
      : '请重新描述运单目的站和更正原因。')
    input.current?.focus()
  }

  useEffect(() => {
    const policyRequest = new AbortController()
    setWritePolicy(null)
    setPolicyError('')
    if (identityKey !== 'anonymous') {
      setPolicyLoading(true)
      void readAssistantWritePolicy(policyRequest.signal).then(setWritePolicy).catch(error => {
        if (!policyRequest.signal.aborted) setPolicyError(error instanceof Error ? error.message : '自动执行设置暂不可用。')
      }).finally(() => { if (!policyRequest.signal.aborted) setPolicyLoading(false) })
    } else {
      setPolicyLoading(false)
    }
    return () => policyRequest.abort()
  }, [identityKey])

  useEffect(() => {
    session.current = readStored(sessionStorageKey)
    const restoredActions = storedActions(actionStorageKey)
    if (!restoredActions.length) return
    const restored = restoredActions.map(stored => {
      const candidate: ActionCandidate = {
        candidate_id: stored.candidateId, agent_session_id: stored.agentSessionId,
        message_id: stored.messageId, action_type: stored.actionType,
        subject_type: stored.subjectType, subject_id: stored.subjectId, payload: {},
      }
      return { stored, message: { id: `restored-${stored.messageId}`, role: 'assistant' as const,
        text: '上次的业务修改提案', action: { candidate, proposalId: stored.proposalId, status: 'UNKNOWN' as const,
          message: '正在核对上次提案；为避免误执行，刷新后不会恢复确认按钮。' } } }
    })
    setMessages(items => {
      const existing = new Set(items.map(item => item.id))
      return [...items, ...restored.map(item => item.message).filter(item => !existing.has(item.id))]
    })
    for (const item of restored) window.setTimeout(() => void checkAction(item.message.id, item.message.action), 0)
  // Storage keys are scoped to the authenticated browser login, not just the account.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identityKey])
  const approveAction = async (messageId: string, action: AssistantAction) => {
    if (actionRequest.current) return
    const request = new AbortController()
    actionRequest.current = request
    setActionBusy(true)
    changeAction(messageId, { ...action, status: 'EXECUTING', message: '正在提交确认操作。' })
    try { changeAction(messageId, await finishAction(action, 'approved', request.signal)) }
    finally { if (actionRequest.current === request) actionRequest.current = null; setActionBusy(false) }
  }
  const rejectAction = async (messageId: string, action: AssistantAction) => {
    if (!action.preview || actionRequest.current) return
    const request = new AbortController()
    actionRequest.current = request
    setActionBusy(true)
    changeAction(messageId, { ...action, status: 'EXECUTING', message: '正在取消提案。' })
    try {
      await rejectAssistantAction(action.preview.proposal_id, request.signal)
      const outcome = await syncAssistantAction(action.candidate.agent_session_id, action.candidate.candidate_id, action.preview.proposal_id, request.signal)
      removeStoredAction(action.preview.proposal_id)
      changeAction(messageId, { ...action, status: outcome.status, outcome, message: actionStatusText(outcome.status) })
    } catch (error) {
      changeAction(messageId, { ...action, status: 'UNKNOWN', message: error instanceof Error ? error.message : '取消结果暂时无法核对。' })
    } finally {
      if (actionRequest.current === request) actionRequest.current = null
      setActionBusy(false)
    }
  }
  const setActionAutoExecution = async (actionType: ActionType, enabled: boolean) => {
    if (!writePolicy || policyBusy) return
    const next = enabled
      ? [...new Set([...writePolicy.enabled_action_types, actionType])]
      : writePolicy.enabled_action_types.filter(item => item !== actionType)
    const request = new AbortController()
    setPolicyBusy(true)
    setPolicyError('')
    try {
      setWritePolicy(await updateAssistantWritePolicy(next, request.signal))
    } catch (error) {
      setPolicyError(error instanceof Error ? error.message : '自动执行设置未保存。')
    } finally {
      setPolicyBusy(false)
    }
  }
  const beginGesture = (event: PointerEvent<HTMLButtonElement>, mode: GestureMode) => {
    if (compact || maximized || event.button !== 0) return
    const box = constrain(geometry)
    setGeometry(box)
    setGesture({ mode, x: event.clientX, y: event.clientY, box })
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const moveGesture = (event: PointerEvent<HTMLButtonElement>) => {
    const current = gesture
    if (!current) return
    const dx = event.clientX - current.x
    const dy = event.clientY - current.y
    setGeometry(current.mode === 'move'
      ? constrain({ ...current.box, x: current.box.x + dx, y: current.box.y + dy })
      : resizeFromCorner(current.box, current.mode, dx, dy))
  }
  const keyboardGeometry = (event: KeyboardEvent<HTMLButtonElement>, mode: GestureMode) => {
    if (compact || maximized || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return
    event.preventDefault()
    const dx = event.key === 'ArrowLeft' ? -20 : event.key === 'ArrowRight' ? 20 : 0
    const dy = event.key === 'ArrowUp' ? -20 : event.key === 'ArrowDown' ? 20 : 0
    const next = mode === 'move'
      ? constrain({ ...geometry, x: geometry.x + dx, y: geometry.y + dy })
      : resizeFromCorner(geometry, mode, dx, dy)
    setGeometry(next)
    setGeometryAnnouncement(mode === 'move'
      ? `助手窗口已移动到水平 ${Math.round(next.x)}、垂直 ${Math.round(next.y)} 像素。`
      : `助手窗口大小已调整为宽 ${Math.round(next.width)}、高 ${Math.round(next.height)} 像素。`)
  }

  const send = async (text: string, messageId: string = crypto.randomUUID(), retry = false) => {
    if (controller.current || !text.trim()) return
    const abort = new AbortController()
    const timeout = window.setTimeout(() => abort.abort('timeout'), 150000)
    controller.current = abort
    setBusy(true)
    setProgress('正在连接助手')
    setDraft('')
    setMessages(items => [...(retry ? items.filter(item => item.retry?.messageId !== messageId) : items), ...(!retry ? [{ id: messageId, role: 'user' as const, text }] : [])])
    try {
      if (!session.current) {
        session.current = await createAssistantSession(abort.signal)
        writeStored(sessionStorageKey, session.current)
      }
      const answer = await queryAssistant(session.current, messageId, text, abort.signal, setProgress)
      let action: AssistantAction | undefined
      if (answer.action_candidate) {
        const candidate = answer.action_candidate
        try {
          const preview = await previewAssistantAction(candidate, abort.signal)
          action = { candidate, preview, status: 'PREVIEW_READY_CONFIRM' }
          const automaticEnabled = preview.execution_policy === 'AUTO_ALLOWED' &&
            writePolicy?.eligible_action_types.includes(candidate.action_type) === true &&
            writePolicy.enabled_action_types.includes(candidate.action_type)
          if (automaticEnabled) {
            setProgress('正在按你的自动执行设置提交并核对结果')
            action = { ...action, status: 'EXECUTING', message: '已按你的自动执行设置提交，正在核对结果。' }
            persistAction(action)
            action = await finishAction(action, 'automatic', abort.signal)
          }
          persistAction(action)
        } catch (error) {
          action = { candidate, status: 'FAILED', message: error instanceof AuthRequestError ? error.message : error instanceof Error ? error.message : '后端无法生成操作预览。' }
        }
      }
      setMessages(items => [...items, { id: crypto.randomUUID(), role: 'assistant', text: answer.answer, answer, action }])
    } catch (error) {
      const cancelled = abort.signal.aborted
      const timedOut = abort.signal.reason === 'timeout'
      const message = timedOut ? '助手响应超时，请稍后重试。' : cancelled ? '查询已停止，你可以继续提问。' : error instanceof AssistantError ? error.message : '连接中断，请重试当前问题。'
      const expired = error instanceof AssistantError && error.code === 'SESSION_EXPIRED'
      setMessages(items => [...items, { id: crypto.randomUUID(), role: 'assistant', text: message, error: !cancelled,
        retry: (!cancelled || timedOut) && !expired ? { text, messageId } : undefined }])
    } finally {
      window.clearTimeout(timeout)
      controller.current = null
      setBusy(false)
      if (open) input.current?.focus()
    }
  }

  const newConversation = () => {
    if (controller.current) return
    const old = session.current
    const pendingActions = messages.filter(item => item.action &&
      !['SUCCEEDED', 'REJECTED', 'EXPIRED', 'FAILED'].includes(item.action.status))
    session.current = null
    writeStored(sessionStorageKey, null)
    if (old && pendingActions.length === 0) void deleteAssistantSession(old).catch(() => {})
    setMessages(pendingActions.map(item => ({ ...item,
      text: '新对话已开始；此提案仍绑定原会话，可继续核对或处理。',
    })))
    setDraft('')
    input.current?.focus()
  }

  const visibleGeometry = constrain(geometry)
  const expandedStyle = maximized ? { inset: compact ? 8 : 24, width: 'auto', height: 'auto' } : compact ? { left: 8, right: 8, bottom: 8, width: 'auto', height: 'min(78dvh, 640px)' } : { left: visibleGeometry.x, top: visibleGeometry.y, width: visibleGeometry.width, height: visibleGeometry.height }

  return <>
    {!open && <Button ref={launcher} className="jp-assistant-launcher" type="primary" icon={<MessageOutlined />} onClick={() => setOpen(true)} aria-label="打开物流查询助手">查询助手{busy && <Spin size="small" />}</Button>}
    {open && <section className={`jp-assistant${!compact && !maximized ? ' jp-assistant-resizable' : ''}`} style={expandedStyle} role="region" aria-label="物流查询助手" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); collapse() } }}>
      <header className="jp-assistant-header">
        <button type="button" className="jp-assistant-move" aria-label="移动助手窗口，可用方向键调整位置" onPointerDown={event => beginGesture(event, 'move')} onPointerMove={moveGesture} onPointerUp={() => setGesture(null)} onPointerCancel={() => setGesture(null)} onKeyDown={event => keyboardGeometry(event, 'move')}>
          {!compact && !maximized && <DragOutlined />}
          <span>物流查询助手</span>
        </button>
        <div className="jp-assistant-actions">
          <Tooltip title="开始新对话"><Button type="text" icon={<PlusOutlined />} aria-label="开始新对话" disabled={busy} onClick={newConversation} /></Tooltip>
          <Tooltip title={maximized ? '还原窗口' : '放大窗口'}><Button type="text" icon={maximized ? <ShrinkOutlined /> : <ExpandOutlined />} aria-label={maximized ? '还原窗口' : '放大窗口'} onClick={() => setMaximized(value => !value)} /></Tooltip>
          <Tooltip title="收起，保留当前对话"><Button type="text" icon={<MinusOutlined />} aria-label="收起助手" onClick={collapse} /></Tooltip>
        </div>
      </header>
      <div className="jp-assistant-scope">物流查询助手<span>{policyLoading ? '正在读取自动执行设置…' : policyError ? '自动执行设置暂不可用 · 修改仍需逐次确认' : writePolicy?.eligible_action_types.length ? '修改前先预览 · 自动执行仅按你的设置' : '暂无可自动执行操作 · 修改前先预览并由你确认'}</span>
        {writePolicy?.eligible_action_types.length ? <Button type="link" size="small" icon={<SettingOutlined />} onClick={() => setPolicyOpen(true)}>自动执行设置</Button> : null}
      </div>
      <Modal title="自动执行设置" open={policyOpen} onCancel={() => setPolicyOpen(false)} footer={<Button onClick={() => setPolicyOpen(false)}>完成</Button>} destroyOnHidden>
        <p>这里只能开启服务端明确允许的操作。关闭后，新提案需要你逐次确认。</p>
        {policyError && <Alert type="error" showIcon message={policyError} />}
        {policyBusy ? <Spin /> : writePolicy?.eligible_action_types.map(actionType => <div className="jp-assistant-policy-row" key={actionType}>
          <span>{actionType === 'order.update' ? '修改订单' : '更正运单目的站'}</span>
          <Switch checked={writePolicy.enabled_action_types.includes(actionType)} disabled={policyBusy}
            aria-label={`${actionType === 'order.update' ? '修改订单' : '更正运单目的站'}自动执行`}
            onChange={checked => void setActionAutoExecution(actionType, checked)} />
        </div>)}
      </Modal>
      <div ref={log} className="jp-assistant-log" role="log" aria-label="对话记录" aria-live="polite" aria-relevant="additions">
        {messages.length === 0 && <div className="jp-assistant-empty">
          <h2>从一个问题开始</h2>
          <p>查询物流状态、运输安排或目的站更正记录。查询详情时，请带上完整编号或对象 ID。</p>
          <div className="jp-assistant-suggestions">
            {(ownScope ? ['查看我的订单', '查看我的待揽收运单', '我的运单运输到哪里了？'] : ['哪些运单发生过目的站更正？', '列出运输中的任务', '查看待揽收运单']).map(text => <button type="button" key={text} onClick={() => void send(text)} disabled={busy}>{text}<SendOutlined /></button>)}
          </div>
        </div>}
        {messages.map(item => <article className={`jp-assistant-message jp-assistant-message-${item.role}`} key={item.id}>
          <div className="jp-assistant-speaker">{item.role === 'user' ? '你' : '查询助手'}</div>
          {item.error ? <Alert showIcon type="error" message={item.text} /> : <div className="jp-assistant-copy"><Markdown remarkPlugins={[remarkGfm]} components={{ a: ({ children }) => <span>{children}</span>, img: () => null }}>{item.text}</Markdown></div>}
          {item.action && <section className="jp-assistant-action" tabIndex={0} aria-labelledby={`jp-assistant-action-title-${item.id}`}>
            <h3 id={`jp-assistant-action-title-${item.id}`}>{item.action.candidate.action_type === 'order.update' ? '订单修改预览' : '目的站更正预览'}</h3>
            {item.action.preview && !['FAILED', 'EXPIRED'].includes(item.action.status) && <>
              <p><Link to={`${referencePaths[item.action.preview.subject.type]}/${item.action.preview.subject.id}`}>
                {item.action.preview.subject.type === 'order' ? '订单' : '运单'} {item.action.preview.subject.number}
              </Link> · ID {item.action.preview.subject.id}</p>
              <dl>{item.action.preview.changes.map(change => <div key={change.field}><dt>{change.label}</dt><dd><span>原值：{displayActionValue(change.before)}</span><span>新值：{displayActionValue(change.after)}</span></dd></div>)}</dl>
              <ul>{item.action.preview.impact.map((impact, index) => <li key={index}>{impact}</li>)}</ul>
              <p>有效期：{formatActionExpiry(item.action.preview.expires_at, now)}</p>
              <p>执行方式：{item.action.preview.execution_policy === 'AUTO_ALLOWED' ? '按已启用的自动执行设置提交' : '需你逐次确认'}</p>
            </>}
            <p role="status">{item.action.message ?? (item.action.status === 'PREVIEW_READY_AUTO' ? '已按你启用的自动执行设置提交。' : item.action.status === 'PREVIEW_READY_CONFIRM' ? '请核对变更内容后确认。' : actionStatusText(item.action.status))}</p>
            {item.action.outcome?.status === 'SUCCEEDED' && <p>已完成：{String(item.action.outcome.result_summary?.summary?.status ?? item.action.outcome.result_summary?.summary?.stage ?? '业务数据已更新')}。</p>}
            {item.action.preview && item.action.status === 'PREVIEW_READY_CONFIRM' && <div className="jp-assistant-action-buttons"><Button type="primary" onClick={() => void approveAction(item.id, item.action!)}>确认执行</Button><Button onClick={() => void rejectAction(item.id, item.action!)}>取消本次修改</Button></div>}
            {item.action.status === 'FAILED' && !item.action.preview && !item.action.proposalId && <Button size="small" disabled={busy || actionBusy} onClick={() => void retryPreview(item.id, item.action!)}>重试获取预览</Button>}
            {['FAILED', 'EXPIRED'].includes(item.action.status) && (item.action.preview || item.action.proposalId) && <Button size="small" disabled={busy || actionBusy} onClick={() => reproposeAction(item.action!)}>{messages.some(message => message.role === 'user' && message.id === item.action!.candidate.message_id) ? '重新提出修改' : '重新描述修改'}</Button>}
            {item.action.status === 'UNKNOWN' && (item.action.preview || item.action.proposalId) && <Button size="small" onClick={() => void checkAction(item.id, item.action!)}>核对同一提案状态</Button>}
          </section>}
          {item.retry && <Button size="small" disabled={busy} onClick={() => void send(item.retry!.text, item.retry!.messageId, true)}>重试当前问题</Button>}
          {item.answer?.warnings.length ? <details className="jp-assistant-warnings"><summary>查询范围与提示（{item.answer.warnings.length}）</summary><ul>{item.answer.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details> : null}
          {item.answer?.references.length ? <div className="jp-assistant-references">
            <div className="jp-assistant-reference-title">本次查询涉及的对象</div>
            <ul>{item.answer.references.filter(ref => !ownScope || ref.type !== 'transport_task').map(ref => <li key={`${ref.type}:${ref.id}`}><Link to={`${referencePaths[ref.type]}/${ref.id}`}><span>{referenceLabels[ref.type]}</span><strong>{ref.number || `${referenceLabels[ref.type]} ${ref.id}`}</strong><small>ID {ref.id}</small></Link></li>)}</ul>
            {item.answer.references_truncated && <p>对象较多，仅展示前 100 个。请缩小范围继续查询。</p>}
          </div> : null}
        </article>)}
        {busy && <div className="jp-assistant-progress" role="status"><Spin size="small" /><span>{progress}…</span></div>}
      </div>
      <form className="jp-assistant-composer" onSubmit={event => { event.preventDefault(); void send(draft.trim()) }}>
        <label className="jp-assistant-sr-only" htmlFor="jp-assistant-question">输入物流查询问题</label>
        <textarea id="jp-assistant-question" ref={input} rows={2} value={draft} onChange={event => setDraft(event.target.value)} maxLength={4000} placeholder="输入问题，例如：运单 XXX 是否延误？" readOnly={busy} onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!busy) void send(draft.trim()) }
        }} />
        <div className="jp-assistant-composer-bottom"><span>{draft.length > 3500 ? `${draft.length} / 4000` : 'Enter 发送 · Shift + Enter 换行'}</span>{busy ? <Button icon={<StopOutlined />} onClick={() => controller.current?.abort()}>停止查询</Button> : <Button type="primary" htmlType="submit" icon={<SendOutlined />} disabled={!draft.trim()}>发送</Button>}</div>
      </form>
      {!compact && !maximized && (['nw', 'ne', 'sw', 'se'] as const).map(corner => <button key={corner} type="button" className={`jp-assistant-resize jp-assistant-resize-${corner}`} aria-label={`从${{ nw: '左上', ne: '右上', sw: '左下', se: '右下' }[corner]}角调整窗口大小，可用方向键调整`} onPointerDown={event => beginGesture(event, corner)} onPointerMove={moveGesture} onPointerUp={() => setGesture(null)} onPointerCancel={() => setGesture(null)} onKeyDown={event => keyboardGeometry(event, corner)} />)}
      <span className="jp-assistant-sr-only" role="status" aria-live="polite" aria-atomic="true">{geometryAnnouncement}</span>
    </section>}
  </>
}
