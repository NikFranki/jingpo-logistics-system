import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { Alert, Button, Spin, Tooltip } from 'antd'
import { ArrowsAltOutlined, DragOutlined, ExpandOutlined, MessageOutlined, MinusOutlined, PlusOutlined, SendOutlined, ShrinkOutlined, StopOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AssistantError, createAssistantSession, deleteAssistantSession, queryAssistant, referenceLabels, referencePaths, type AssistantAnswer } from './assistantApi'
import './assistant.css'
import { useAuth } from './auth/AuthContext'

type Geometry = { x: number; y: number; width: number; height: number }
type ChatMessage = { id: string; role: 'user' | 'assistant'; text: string; answer?: AssistantAnswer; error?: boolean; retry?: { text: string; messageId: string } }

function constrain(box: Geometry): Geometry {
  const maxWidth = Math.max(280, window.innerWidth - 32)
  const maxHeight = Math.max(280, window.innerHeight - 32)
  const width = Math.max(Math.min(360, maxWidth), Math.min(box.width, maxWidth))
  const height = Math.max(Math.min(420, maxHeight), Math.min(box.height, maxHeight))
  return { width, height, x: Math.max(16, Math.min(box.x, window.innerWidth - width - 16)), y: Math.max(16, Math.min(box.y, window.innerHeight - height - 16)) }
}

export default function AssistantPanel() {
  const ownScope = useAuth().session?.user.role === 'user'
  const [open, setOpen] = useState(false)
  const [maximized, setMaximized] = useState(false)
  const [compact, setCompact] = useState(window.innerWidth <= 640)
  const [geometry, setGeometry] = useState(() => constrain({ x: window.innerWidth - 464, y: window.innerHeight - 636, width: 440, height: 604 }))
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('思考中')
  const session = useRef<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const launcher = useRef<HTMLButtonElement>(null)
  const log = useRef<HTMLDivElement>(null)
  const gesture = useRef<{ mode: 'move' | 'resize'; x: number; y: number; box: Geometry } | null>(null)

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

  const collapse = () => { setOpen(false); requestAnimationFrame(() => launcher.current?.focus()) }
  const beginGesture = (event: PointerEvent<HTMLButtonElement>, mode: 'move' | 'resize') => {
    if (compact || maximized || event.button !== 0) return
    gesture.current = { mode, x: event.clientX, y: event.clientY, box: geometry }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const moveGesture = (event: PointerEvent<HTMLButtonElement>) => {
    const current = gesture.current
    if (!current) return
    const dx = event.clientX - current.x
    const dy = event.clientY - current.y
    setGeometry(constrain(current.mode === 'move' ? { ...current.box, x: current.box.x + dx, y: current.box.y + dy } : { ...current.box, width: current.box.width + dx, height: current.box.height + dy }))
  }
  const keyboardGeometry = (event: KeyboardEvent<HTMLButtonElement>, mode: 'move' | 'resize') => {
    if (compact || maximized || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return
    event.preventDefault()
    const dx = event.key === 'ArrowLeft' ? -20 : event.key === 'ArrowRight' ? 20 : 0
    const dy = event.key === 'ArrowUp' ? -20 : event.key === 'ArrowDown' ? 20 : 0
    setGeometry(box => constrain(mode === 'move' ? { ...box, x: box.x + dx, y: box.y + dy } : { ...box, width: box.width + dx, height: box.height + dy }))
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
      if (!session.current) session.current = await createAssistantSession(abort.signal)
      const answer = await queryAssistant(session.current, messageId, text, abort.signal, setProgress)
      setMessages(items => [...items, { id: crypto.randomUUID(), role: 'assistant', text: answer.answer, answer }])
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
    session.current = null
    if (old) void deleteAssistantSession(old).catch(() => {})
    setMessages([])
    setDraft('')
    input.current?.focus()
  }

  const expandedStyle = maximized ? { inset: compact ? 8 : 24, width: 'auto', height: 'auto' } : compact ? { left: 8, right: 8, bottom: 8, width: 'auto', height: 'min(78dvh, 640px)' } : { left: geometry.x, top: geometry.y, width: geometry.width, height: geometry.height }

  return <>
    {!open && <Button ref={launcher} className="jp-assistant-launcher" type="primary" icon={<MessageOutlined />} onClick={() => setOpen(true)} aria-label="打开物流查询助手">查询助手{busy && <Spin size="small" />}</Button>}
    {open && <section className="jp-assistant" style={expandedStyle} role="region" aria-label="物流查询助手" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); collapse() } }}>
      <header className="jp-assistant-header">
        <button type="button" className="jp-assistant-move" aria-label="移动助手窗口，可用方向键调整位置" onPointerDown={event => beginGesture(event, 'move')} onPointerMove={moveGesture} onPointerUp={() => { gesture.current = null }} onPointerCancel={() => { gesture.current = null }} onKeyDown={event => keyboardGeometry(event, 'move')}>
          {!compact && !maximized && <DragOutlined />}
          <span>物流查询助手</span>
        </button>
        <div className="jp-assistant-actions">
          <Tooltip title="开始新对话"><Button type="text" icon={<PlusOutlined />} aria-label="开始新对话" disabled={busy} onClick={newConversation} /></Tooltip>
          <Tooltip title={maximized ? '还原窗口' : '放大窗口'}><Button type="text" icon={maximized ? <ShrinkOutlined /> : <ExpandOutlined />} aria-label={maximized ? '还原窗口' : '放大窗口'} onClick={() => setMaximized(value => !value)} /></Tooltip>
          <Tooltip title="收起，保留当前对话"><Button type="text" icon={<MinusOutlined />} aria-label="收起助手" onClick={collapse} /></Tooltip>
        </div>
      </header>
      <div className="jp-assistant-scope">只读查询<span>{ownScope ? '自己的订单 · 关联运单和进度' : '订单 · 运单 · 运输任务'}</span></div>
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
      {!compact && !maximized && <button type="button" className="jp-assistant-resize" aria-label="调整窗口大小，可用方向键调整" onPointerDown={event => beginGesture(event, 'resize')} onPointerMove={moveGesture} onPointerUp={() => { gesture.current = null }} onPointerCancel={() => { gesture.current = null }} onKeyDown={event => keyboardGeometry(event, 'resize')}><ArrowsAltOutlined /></button>}
    </section>}
  </>
}
