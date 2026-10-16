import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Button, Descriptions, Divider, Form, Input, Modal, Result, Select, Space, Spin, Switch, Table, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, GoogleOutlined, LogoutOutlined, TruckOutlined } from '@ant-design/icons'
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './AuthContext'
import { createPreviewAdapter, disconnectedAdapter } from './adapters'
import { canManageUsers, canMaintainOrders, canOperateLogistics, roleDescriptions, roleLabels, roles, safeReturnPath } from './contracts'
import type { Account, AuthAdapter, Role, UserChange } from './contracts'
import './auth.css'

const { Title, Paragraph, Text } = Typography
type Paths = { base: string; preview: boolean }
const problem = (reason: unknown) => reason instanceof Error ? reason.message : '操作未完成，请重试。'
const passwordRules = [{ required: true, message: '请输入密码' }]
const newPasswordRules = [...passwordRules, { min: 12, max: 128, message: '密码长度为 12～128 位' }, { pattern: /^(?=.*[A-Za-z])(?=.*[0-9])[\s\S]+$/, message: '密码需同时包含字母和数字' }]
const usernameRules = [{ required: true, message: '请输入用户名' }, { max: 40, message: '用户名长度不能超过 40 位' }]

function Feedback({ error, success }: { error?: string; success?: string }) {
  return <div aria-live="polite">{error && <Alert type="error" showIcon message={error} className="jp-auth-feedback" />}{success && <Alert type="success" showIcon message={success} className="jp-auth-feedback" />}</div>
}

function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [success, setSuccess] = useState<string>()
  const active = useRef(true)
  const locked = useRef(false)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  const run = async (action: () => Promise<void>) => {
    if (locked.current) return
    locked.current = true
    setBusy(true); setError(undefined); setSuccess(undefined)
    try { await action() } catch (reason) { if (active.current) setError(problem(reason)) }
    finally { locked.current = false; if (active.current) setBusy(false) }
  }
  return { busy, error, success, setSuccess, run }
}

function Gate({ children, paths, admin = false }: { children: ReactNode; paths: Paths; admin?: boolean }) {
  const { session, loading, error, refresh } = useAuth()
  const location = useLocation()
  if (loading) return <div className="jp-auth-loading"><Spin tip="正在确认账号状态"><div /></Spin></div>
  if (error) return <Result status="warning" title="暂时无法确认账号状态" subTitle={error} extra={<Button onClick={() => void refresh()}>重试</Button>} />
  if (!session) return <Navigate to={`${paths.base}/login?next=${encodeURIComponent(location.pathname)}`} replace />
  if (!session.user.enabled) return <Result status="403" title="账号已停用" subTitle="请联系管理员处理。" />
  if (session.user.needsUsername) return <Navigate to={`${paths.base}/oauth/google/callback`} replace />
  if (!session.user.emailVerified) return <Navigate to={`${paths.base}/verify-email`} replace />
  if (admin && !canManageUsers(session.user.role)) return <Result status="403" title="仅超级管理员可管理用户" extra={<Link to={`${paths.base}/account`}>返回个人账号</Link>} />
  return children
}

function Login({ paths }: { paths: Paths }) {
  const { adapter, acceptSession } = useAuth()
  const action = useAction()
  const navigate = useNavigate()
  const location = useLocation()
  const next = safeReturnPath(new URLSearchParams(location.search).get('next'), `${paths.base}/account`)
  const destination = paths.preview && !next.startsWith(`${paths.base}/`) ? `${paths.base}/account` : next
  return <><Title level={2}>登录鲸破</Title><Paragraph type="secondary">使用公司账号，继续处理物流业务。</Paragraph><Feedback {...action} />
    {paths.preview && <Alert type="info" message="表单模拟登录：用户名可填 admin、ops、developer、sales 或 finance；密码仅用于检查必填，不会保存或验证。" showIcon className="jp-auth-feedback" />}
    <Form layout="vertical" requiredMark={false} onFinish={(values: { username: string; password: string }) => void action.run(async () => {
      const session = await adapter.signIn(values.username, values.password)
      acceptSession(session)
      navigate(session.user.needsUsername ? `${paths.base}/oauth/google/callback` : session.user.emailVerified ? destination : `${paths.base}/verify-email`, { replace: true })
    })}>
      <Form.Item label="用户名" name="username" rules={[{ required: true, whitespace: true, message: '请输入用户名' }]}><Input autoComplete="username" autoFocus /></Form.Item>
      <Form.Item label="密码" name="password" rules={passwordRules}><Input.Password autoComplete="current-password" /></Form.Item>
      <div className="jp-auth-secondary"><Link to={`${paths.base}/forgot-password`}>忘记密码？</Link></div>
      <Button block type="primary" htmlType="submit" loading={action.busy}>{paths.preview ? '模拟登录' : '登录'}</Button>
    </Form>
    <Divider plain>或</Divider><GoogleButton intent="login" action={action} />
    <Paragraph className="jp-auth-footnote">还没有账号？ <Link to={`${paths.base}/register`}>注册账号</Link></Paragraph>
  </>
}

function GoogleButton({ intent, action }: { intent: 'login' | 'bind'; action: ReturnType<typeof useAction> }) {
  const { adapter } = useAuth()
  return <Button block icon={<GoogleOutlined />} loading={action.busy} onClick={() => void action.run(async () => {
    const result = await adapter.startGoogle(intent)
    const url = new URL(result.redirectUrl, window.location.origin)
    if (url.protocol !== 'https:' && url.origin !== window.location.origin) throw new Error('登录跳转地址无效，请联系管理员。')
    window.location.assign(url.href)
  })}>{intent === 'bind' ? '绑定 Google 账号' : '使用 Google 登录'}</Button>
}

function Register({ paths }: { paths: Paths }) {
  const { adapter, refresh } = useAuth()
  const navigate = useNavigate()
  const action = useAction()
  return <><Title level={2}>注册账号</Title><Paragraph type="secondary">验证邮箱后，即可创建订单并查看自己的物流进度。</Paragraph><Feedback {...action} />
    <Form layout="vertical" requiredMark={false} onFinish={(values: { username: string; email: string; password: string }) => void action.run(async () => {
      await adapter.register({ username: values.username, email: values.email.trim(), password: values.password })
      await refresh(); navigate(`${paths.base}/verify-email`, { replace: true, state: { email: values.email.trim() } })
    })}>
      <Form.Item label="用户名" name="username" rules={usernameRules}><Input autoComplete="username" autoFocus /></Form.Item>
      <Form.Item label="邮箱" name="email" rules={[{ required: true, message: '请输入邮箱' }, { type: 'email', message: '请输入有效邮箱地址' }]}><Input autoComplete="email" type="email" /></Form.Item>
      <Form.Item label="密码" name="password" rules={newPasswordRules} extra="12～128 位，同时包含字母和数字。"><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
      <ConfirmPassword />
      <Text type="secondary">注册后默认为普通用户。其他角色由管理员分配。</Text>
      <Button block type="primary" htmlType="submit" loading={action.busy} className="jp-auth-submit">{paths.preview ? '模拟注册' : '注册并发送验证邮件'}</Button>
    </Form><Paragraph className="jp-auth-footnote">已有账号？ <Link to={`${paths.base}/login`}>返回登录</Link></Paragraph>
  </>
}

function ConfirmPassword() {
  return <Form.Item label="确认密码" name="confirmPassword" dependencies={['password']} rules={[{ required: true, message: '请再次输入密码' }, ({ getFieldValue }) => ({ validator: (_, value) => !value || getFieldValue('password') === value ? Promise.resolve() : Promise.reject(new Error('两次输入的密码不一致')) })]}><Input.Password autoComplete="new-password" /></Form.Item>
}

function VerifyEmail({ paths }: { paths: Paths }) {
  const { adapter, session, refresh, signOut } = useAuth()
  const action = useAction()
  const location = useLocation()
  const [token] = useState(() => new URLSearchParams(location.search).get('token') ?? '')
  const [email, setEmail] = useState<string>(() => location.state?.email ?? session?.user.email ?? '')
  const navigate = useNavigate()
  useEffect(() => {
    if (new URLSearchParams(location.search).has('token')) navigate(location.pathname, { replace: true })
  }, [location.search, location.pathname, navigate])
  const verify = (value: string) => void action.run(async () => { await adapter.verifyEmail(value); await refresh(); action.setSuccess(session ? '邮箱验证成功，可以继续使用账号。' : '邮箱验证成功，请登录继续。') })
  return <><Title level={2}>验证你的邮箱</Title><Paragraph type="secondary">{email ? `请前往 ${email}，打开验证邮件中的链接。` : '请使用注册邮箱中的验证链接完成验证。'}</Paragraph><Feedback {...action} />
    {action.success?.startsWith('邮箱验证成功') || (session?.user.emailVerified && session.user.email === email && !token) ? <Result status="success" title="邮箱已验证" extra={<Button type="primary" onClick={() => navigate(`${paths.base}${session ? '/account' : '/login'}`)}>{session ? '进入个人账号' : '返回登录'}</Button>} /> : <Space direction="vertical" size="middle" className="jp-auth-full">
      {token && <Button type="primary" loading={action.busy} onClick={() => verify(token)}>确认验证邮箱</Button>}
      <Form layout="vertical" onFinish={() => void action.run(async () => { await adapter.resendVerification(email.trim()); action.setSuccess(paths.preview ? '预览模式未发送邮件。可模拟邮箱验证。' : '如果该邮箱可用，会收到后续操作说明，请检查邮箱。') })}>
        <Form.Item label="注册邮箱" name="email" initialValue={email} rules={[{ required: true, message: '请输入注册邮箱' }, { type: 'email', message: '请输入有效邮箱地址' }]}><Input type="email" autoComplete="email" onChange={event => setEmail(event.target.value)} /></Form.Item>
        <Button htmlType="submit" loading={action.busy}>重新发送验证邮件</Button>
      </Form>
      {paths.preview && session && <Button onClick={() => verify('preview-verification')} loading={action.busy}>仅预览：模拟验证成功</Button>}
      <Button type="link" onClick={() => void action.run(async () => { if (session) await signOut(); navigate(`${paths.base}/login`) })}>返回登录</Button>
    </Space>}
  </>
}

function PasswordPage({ paths, reset = false }: { paths: Paths; reset?: boolean }) {
  const { adapter, acceptSession } = useAuth()
  const action = useAction()
  const navigate = useNavigate()
  const location = useLocation()
  const [token] = useState(() => new URLSearchParams(location.search).get('token') ?? '')
  useEffect(() => {
    if (new URLSearchParams(location.search).has('token')) navigate(location.pathname, { replace: true })
  }, [location.search, location.pathname, navigate])
  return <><Title level={2}>{reset ? '设置新密码' : '找回密码'}</Title><Paragraph type="secondary">{reset ? '完成重置后，所有设备都需要重新登录。' : '填写注册邮箱，我们会发送重置密码的邮件。'}</Paragraph><Feedback {...action} />
    {reset && action.success ? <Result status="success" title="密码已重置" subTitle="请返回登录页重新登录。" /> : reset && !token && !paths.preview ? <Alert type="warning" message="缺少有效的重置链接，请重新申请找回密码。" showIcon /> : <Form layout="vertical" requiredMark={false} onFinish={(values: { email: string; password: string }) => void action.run(async () => {
      if (reset) { await adapter.resetPassword(token || 'preview-reset', values.password); acceptSession(null); action.setSuccess(paths.preview ? '模拟重置完成；未保存密码，预览登录已清除。' : '密码已重置，请使用新密码重新登录。') }
      else { await adapter.requestPasswordReset(values.email.trim()); action.setSuccess(paths.preview ? '预览模式未发送邮件。可打开重置密码预览检查下一步。' : '如果该邮箱可用于找回密码，你会收到邮件，请检查收件箱。') }
    })}>
      {reset ? <><Form.Item label="新密码" name="password" rules={newPasswordRules}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item><ConfirmPassword /></> : <Form.Item label="注册邮箱" name="email" rules={[{ required: true, message: '请输入邮箱' }, { type: 'email', message: '请输入有效邮箱地址' }]}><Input type="email" autoComplete="email" autoFocus /></Form.Item>}
      <Button block type="primary" htmlType="submit" loading={action.busy}>{reset ? '重置密码' : '发送重置邮件'}</Button>
    </Form>}
    <Paragraph className="jp-auth-footnote"><Link to={`${paths.base}/login`}>返回登录</Link>{paths.preview && !reset && <> · <Link to={`${paths.base}/reset-password`}>预览重置密码</Link></>}</Paragraph>
  </>
}

function AccountPage({ paths }: { paths: Paths }) {
  const { session, signOut } = useAuth()
  const action = useAction()
  const navigate = useNavigate()
  if (!session) return null
  const user = session.user
  return <><Title level={2}>个人账号</Title><Paragraph type="secondary">查看账号身份、登录状态和授权范围。</Paragraph><Feedback {...action} />
    <Descriptions column={1} bordered items={[
      { key: 'username', label: '用户名', children: user.username },
      { key: 'email', label: '邮箱', children: <Space wrap>{user.email}<Tag color="success">已验证</Tag></Space> },
      { key: 'role', label: '角色', children: roleLabels[user.role] },
      { key: 'scope', label: '数据范围', children: roleDescriptions[user.role] },
      { key: 'expiry', label: '本次登录到期', children: new Date(session.expiresAt).toLocaleString('zh-CN') },
      { key: 'google', label: 'Google', children: user.googleLinked ? '已绑定' : '尚未绑定' },
      { key: 'password', label: '本地密码', children: user.hasPassword === false ? <Link to={`${paths.base}/forgot-password`}>通过邮箱设置密码</Link> : '已设置' },
    ]} />
    <div className="jp-auth-section">{!user.googleLinked && <GoogleButton intent="bind" action={action} />}<Paragraph type="secondary" className="jp-auth-footnote">Agent 仅提供查询；{user.role === 'developer' ? '你的回答默认包含技术关联说明。' : '你的回答默认使用业务语言。'}</Paragraph></div>
    {canManageUsers(user.role) && <Link to={`${paths.base}/admin/users`}>管理用户账号</Link>}
    <Divider /><Button icon={<LogoutOutlined />} loading={action.busy} onClick={() => void action.run(async () => { await signOut(); navigate(`${paths.base}/login`, { replace: true }) })}>退出当前登录</Button>
  </>
}

function UserManagement() {
  const { adapter } = useAuth()
  const [users, setUsers] = useState<Account[]>([])
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string>()
  const [revision, setRevision] = useState(0)
  const [draft, setDraft] = useState<{ user: Account; change: UserChange }>()
  const action = useAction()
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    adapter.listUsers(controller.signal, { search, page, pageSize }).then(result => { if (!controller.signal.aborted) { setUsers(result.items); setTotal(result.total); setLoadError(undefined) } }).catch(reason => { if (!controller.signal.aborted) { setUsers([]); setTotal(0); setLoadError(problem(reason)) } }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [adapter, revision, search, page, pageSize])
  return <><Title level={2}>用户管理</Title><Paragraph type="secondary">每个账号只有一个角色。角色变更和停用将在接口接入后由后端立即生效。</Paragraph><Feedback {...action} />
    {loadError && <Alert type="error" showIcon message="用户列表加载失败" description={loadError} action={<Button onClick={() => setRevision(value => value + 1)}>重试</Button>} className="jp-auth-feedback" />}
    <Input.Search aria-label="搜索用户名或邮箱" placeholder="搜索用户名或邮箱" value={query} onChange={event => setQuery(event.target.value)} onSearch={value => { setPage(1); setSearch(value.trim()) }} allowClear className="jp-auth-search" />
    <Table<Account> loading={loading} rowKey="id" scroll={{ x: 850 }} dataSource={users} pagination={{ current: page, pageSize, total, showSizeChanger: true, onChange: (next, size) => { setPage(next); setPageSize(size) } }} locale={{ emptyText: search ? '没有匹配的用户，请调整搜索条件。' : '暂无用户' }} columns={[
      { title: '用户', key: 'user', render: (_, user) => <><Text strong>{user.username}</Text><div><Text type="secondary">{user.email}</Text></div></> },
      { title: '角色', key: 'role', width: 180, render: (_, user) => user.role === 'admin' ? <Tag>超级管理员</Tag> : <Select aria-label={`修改 ${user.username} 的角色`} value={user.role} disabled={action.busy} className="jp-auth-role-select" options={roles.filter(role => role !== 'admin').map(role => ({ value: role, label: roleLabels[role] }))} onChange={(role: Exclude<Role, 'admin'>) => setDraft({ user, change: { role } })} /> },
      { title: '邮箱验证', key: 'verified', render: (_, user) => <Tag color={user.emailVerified ? 'success' : 'warning'}>{user.emailVerified ? '已验证' : '待验证'}</Tag> },
      { title: '启用', key: 'enabled', render: (_, user) => <Switch aria-label={`启用 ${user.username}`} checked={user.enabled} disabled={user.role === 'admin' || action.busy} onChange={enabled => setDraft({ user, change: { enabled } })} /> },
    ]} />
    <Paragraph type="secondary">唯一超级管理员不能在这里被停用或降级。未验证的用户仍需先完成邮箱验证。</Paragraph>
    <Modal title={draft?.change.role ? '确认修改角色' : draft?.change.enabled ? '确认启用账号' : '确认停用账号'} open={Boolean(draft)} confirmLoading={action.busy} onCancel={() => { if (!action.busy) setDraft(undefined) }} okText="确认修改" cancelText="返回" onOk={() => void action.run(async () => {
      if (!draft) return
      const updated = await adapter.updateUser(draft.user.id, draft.change)
      setUsers(rows => rows.map(user => user.id === updated.id ? updated : user)); setDraft(undefined); setRevision(value => value + 1)
      action.setSuccess('账号已更新。')
    })}>
      <Paragraph>{draft?.change.role ? `将 ${draft.user.username} 的角色改为${roleLabels[draft.change.role]}。` : draft?.change.enabled ? `重新启用 ${draft.user.username}。` : `停用 ${draft?.user.username} 后，该账号将无法继续访问。`}</Paragraph>
      {action.error && <Alert type="error" showIcon message={action.error} />}
    </Modal>
  </>
}

function PermissionsPreview() {
  const { session } = useAuth()
  if (!session) return null
  const role = session.user.role
  return <><Title level={2}>角色交互预览</Title><Paragraph type="secondary">这些是示例操作，不会查询或修改真实业务数据。</Paragraph>
    <Descriptions column={1} bordered items={[
      { key: 'role', label: '当前角色', children: roleLabels[role] },
      { key: 'scope', label: '可见数据', children: role === 'user' ? '自己的订单、关联运单和进度' : '全公司业务，包括历史无归属记录' },
      { key: 'assistant', label: '助手风格', children: role === 'developer' ? '业务对象 ID、关联关系、版本与执行状态' : '业务进度和易懂的原因说明' },
    ]} />
    <Space wrap className="jp-auth-section"><Button disabled={!canMaintainOrders(role)}>创建订单（示例）</Button><Button disabled={!canMaintainOrders(role)}>修改自己的订单（示例）</Button><Button disabled={!canOperateLogistics(role)}>调整班次（示例）</Button><Button disabled={!canOperateLogistics(role)}>维护线路（示例）</Button></Space>
    <Alert type="info" showIcon message="这里仅预览角色对应的界面状态。业务状态、数据归属及最终授权将在 BE 接入后校验。" />
  </>
}

function CliAuthorization() {
  const { adapter, session } = useAuth()
  const action = useAction()
  return <><Title level={2}>授权命令行助手</Title><Paragraph>当前账号：<Text strong>{session?.user.username}</Text>。确认这是你刚刚在本机发起的登录。授权后，CLI 将按你的账号权限只读查询物流数据。</Paragraph><Feedback {...action} />
    <Form layout="vertical" onFinish={(values: { code: string; decision: 'approve' | 'deny' }) => void action.run(async () => { await adapter.authorizeCli(values.code.trim(), values.decision === 'approve'); action.setSuccess(values.decision === 'approve' ? '授权完成，可以返回终端。' : '已拒绝本次授权。') })} initialValues={{ decision: 'approve' }}>
      <Form.Item name="code" label="终端显示的授权码" normalize={value => value?.toUpperCase().replace(/\s/g, '')} rules={[{ required: true, message: '请输入终端显示的授权码' }, { pattern: /^[A-Z0-9]{8}$/, message: '请输入 8 位授权码' }]}><Input autoComplete="off" maxLength={8} /></Form.Item>
      <Form.Item name="decision" label="本次决定"><Select options={[{ value: 'approve', label: '允许登录' }, { value: 'deny', label: '拒绝登录' }]} /></Form.Item>
      <Button type="primary" htmlType="submit" loading={action.busy}>提交决定</Button>
    </Form><Paragraph className="jp-auth-footnote">授权码 10 分钟内有效。不是你发起的请求，请选择拒绝登录。</Paragraph>
  </>
}

function GoogleCallback({ paths }: { paths: Paths }) {
  const { adapter, acceptSession, session } = useAuth()
  const [stage, setStage] = useState<'pending' | 'username' | 'conflict' | 'failed'>(paths.preview || session?.user.needsUsername ? 'username' : session ? 'pending' : 'failed')
  const navigate = useNavigate()
  const action = useAction()
  useEffect(() => {
    if (!paths.preview && session && !session.user.needsUsername) navigate(session.user.emailVerified ? '/account' : '/verify-email', { replace: true })
  }, [paths.preview, session, navigate])
  return <><Title level={2}>Google 登录</Title>
    {paths.preview && <Form layout="vertical"><Form.Item label="预览回调状态"><Select value={stage} onChange={setStage} options={[{ value: 'username', label: '首次登录：补填用户名' }, { value: 'conflict', label: '邮箱已有账号' }, { value: 'failed', label: '登录失败或取消' }, { value: 'pending', label: '等待身份验证' }]} /></Form.Item></Form>}
    <Feedback {...action} />
    {stage === 'pending' && <Result status="info" title="等待账号服务验证身份" subTitle="Google 身份验证接入后才能继续，当前不会建立真实登录。" />}
    {stage === 'failed' && <Result status="warning" title="Google 登录未完成" subTitle="你可以返回登录页重试，或使用用户名和密码登录。" />}
    {stage === 'conflict' && <Result status="info" title="此邮箱已有账号" subTitle="请先登录已有账号，再在个人账号中绑定 Google。" extra={<Link to={`${paths.base}/forgot-password`}>忘记密码？</Link>} />}
      {stage === 'username' && <><Paragraph type="secondary">为新账号设置用户名，初始角色为普通用户。</Paragraph><Form layout="vertical" onFinish={(values: { username: string }) => void action.run(async () => { const result = await adapter.completeGoogle(values.username); acceptSession(result); navigate(`${paths.base}${result.user.emailVerified ? '/account' : '/verify-email'}`, { replace: true }) })}>
      <Form.Item label="用户名" name="username" rules={usernameRules}><Input autoComplete="username" autoFocus /></Form.Item><Button type="primary" htmlType="submit" loading={action.busy}>{paths.preview ? '模拟完成注册' : '完成注册'}</Button>
    </Form></>}
    <Paragraph className="jp-auth-footnote"><Link to={`${paths.base}/login`}>返回登录</Link></Paragraph>
  </>
}

function Workspace({ paths, selectRole }: { paths: Paths; selectRole?: (role: Role) => void }) {
  const { session, loading } = useAuth()
  const location = useLocation()
  const protectedPage = /\/(account|admin|permissions|cli)(\/|$)/.test(location.pathname)
  return <div className="jp-auth-root">
    <header className="jp-auth-header"><Link to={paths.preview ? `${paths.base}/login` : '/'} className="jp-auth-brand"><TruckOutlined /> JINGPO 鲸破</Link><Text type="secondary">账号与访问权限</Text></header>
    <div className="jp-auth-content">
      {paths.preview && <Alert showIcon type="warning" message="独立交互预览 · 数据仅保存在当前页面内存，刷新即重置；不会调用 BE 或 Agent。" className="jp-auth-environment" />}
      {paths.preview && <div className="jp-auth-preview-controls"><label htmlFor="jp-preview-role">预览角色</label><Select id="jp-preview-role" placeholder="选择角色后进入个人账号" value={session?.user.role} onChange={selectRole} options={roles.map(role => ({ value: role, label: roleLabels[role] }))} className="jp-auth-preview-select" /><Link to={`${paths.base}/login`}>登录流程</Link><Link to={`${paths.base}/register`}>注册流程</Link><Link to={`${paths.base}/oauth/google/callback`}>Google 回调</Link></div>}
      {protectedPage && session && session.user.emailVerified && <nav className="jp-auth-nav" aria-label="账号导航"><Link to={`${paths.base}/account`}>个人账号</Link>{paths.preview && <Link to={`${paths.base}/permissions`}>角色交互</Link>}{canManageUsers(session.user.role) && <Link to={`${paths.base}/admin/users`}>用户管理</Link>}</nav>}
      <main className={`jp-auth-surface${location.pathname.endsWith('/admin/users') ? ' jp-auth-wide' : ''}`} key={protectedPage ? session ? `${session.user.id}:${session.user.role}:${session.user.authVersion}` : 'anonymous' : 'public-auth'}>
        {loading ? <div className="jp-auth-loading"><Spin /></div> : <Routes>
          <Route path="login" element={<Login paths={paths} />} />
          <Route path="register" element={<Register paths={paths} />} />
          <Route path="verify-email" element={<VerifyEmail paths={paths} />} />
          <Route path="forgot-password" element={<PasswordPage key="forgot" paths={paths} />} />
          <Route path="reset-password" element={<PasswordPage key="reset" paths={paths} reset />} />
          <Route path="account" element={<Gate paths={paths}><AccountPage paths={paths} /></Gate>} />
          <Route path="admin/users" element={<Gate paths={paths} admin><UserManagement /></Gate>} />
          <Route path="permissions" element={<Gate paths={paths}><PermissionsPreview /></Gate>} />
          <Route path="cli/authorize" element={<Gate paths={paths}><CliAuthorization /></Gate>} />
          <Route path="oauth/google/callback" element={<GoogleCallback paths={paths} />} />
          <Route path="*" element={<Navigate to={`${paths.base}/login`} replace />} />
        </Routes>}
      </main>
      <footer className="jp-auth-footer"><Link to="/"><ArrowLeftOutlined /> 返回物流系统</Link><Text type="secondary">角色权限由管理员统一分配</Text></footer>
    </div>
  </div>
}

export default function AccountWorkspace({ preview = false, adapter }: { preview?: boolean; adapter?: AuthAdapter }) {
  const [previewAdapter] = useState(createPreviewAdapter)
  const selected = adapter ?? (preview ? previewAdapter : disconnectedAdapter)
  const paths = { base: preview ? '/v8-preview' : '', preview }
  return preview ? <AuthProvider adapter={selected}><WorkspaceBridge paths={paths} previewAdapter={previewAdapter} /></AuthProvider> : <Workspace paths={paths} />
}

function WorkspaceBridge({ paths, previewAdapter }: { paths: Paths; previewAdapter?: ReturnType<typeof createPreviewAdapter> }) {
  const { acceptSession } = useAuth()
  const navigate = useNavigate()
  return <Workspace paths={paths} selectRole={previewAdapter ? role => { acceptSession(previewAdapter.selectRole(role)); navigate(`${paths.base}/account`) } : undefined} />
}
