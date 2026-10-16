import type { Account, AuthAdapter, LoginSession, Registration, Role, UserChange } from './contracts'

function notConnected(): Promise<never> {
  return Promise.reject(new Error('账号服务尚未接入。请稍后重试，或使用独立预览页面检查交互。'))
}

export const disconnectedAdapter: AuthAdapter = {
  currentSession: async () => null,
  signIn: notConnected,
  register: notConnected,
  signOut: notConnected,
  resendVerification: notConnected,
  verifyEmail: notConnected,
  requestPasswordReset: notConnected,
  resetPassword: notConnected,
  startGoogle: notConnected,
  completeGoogle: notConnected,
  listUsers: notConnected,
  updateUser: notConnected,
  authorizeCli: notConnected,
}

export function createPreviewAdapter(): AuthAdapter & { selectRole: (role: Role) => LoginSession } {
  let accounts: Account[] = [
    { id: 'preview-admin', username: 'admin', email: 'admin@example.com', role: 'admin', enabled: true, emailVerified: true, googleLinked: false },
    { id: 'preview-ops', username: 'ops', email: 'ops@example.com', role: 'ops', enabled: true, emailVerified: true, googleLinked: true },
    { id: 'preview-developer', username: 'developer', email: 'developer@example.com', role: 'developer', enabled: true, emailVerified: true, googleLinked: false },
    { id: 'preview-user', username: 'sales', email: 'sales@example.com', role: 'user', enabled: true, emailVerified: true, googleLinked: false },
    { id: 'preview-pending', username: 'finance', email: 'finance@example.com', role: 'user', enabled: true, emailVerified: false, googleLinked: false },
  ]
  let current: LoginSession | null = null
  const login = (user: Account) => {
    current = { user: { ...user }, expiresAt: new Date(Date.now() + 86_400_000).toISOString() }
    return current
  }
  const requireSession = () => {
    if (!current || !current.user.enabled || Date.parse(current.expiresAt) <= Date.now()) throw new Error('预览登录已失效，请重新选择角色。')
    return current
  }
  const requireAdmin = () => {
    if (requireSession().user.role !== 'admin') throw new Error('只有超级管理员可以管理用户。')
  }
  return {
    currentSession: async () => current,
    selectRole: role => login(accounts.find(account => account.id === `preview-${role}`)!),
    signIn: async username => {
      const user = accounts.find(account => account.username === username)
      if (!user || !user.enabled) throw new Error('请选择有效的预览账号：admin、ops、developer、sales 或 finance。')
      return login(user)
    },
    register: async (values: Registration) => {
      if (accounts.some(account => account.username === values.username || account.email.toLowerCase() === values.email.toLowerCase())) throw new Error('此预览用户名或邮箱已使用，请更换。')
      const user: Account = { id: crypto.randomUUID(), username: values.username, email: values.email, role: 'user', enabled: true, emailVerified: false, googleLinked: false }
      accounts = [...accounts, user]
      login(user)
    },
    signOut: async () => { current = null },
    resendVerification: async () => { requireSession() },
    verifyEmail: async token => {
      if (token !== 'preview-verification') throw new Error('验证链接无效或已过期，请重新发送验证邮件。')
      const session = requireSession()
      if (session.user.emailVerified) throw new Error('此验证链接已使用。')
      session.user = { ...session.user, emailVerified: true }
      accounts = accounts.map(account => account.id === session.user.id ? { ...session.user } : account)
    },
    requestPasswordReset: async () => {},
    resetPassword: async token => {
      if (token !== 'preview-reset') throw new Error('重置链接无效或已过期，请重新申请。')
      current = null
    },
    startGoogle: async () => { throw new Error('Google 登录和绑定需等待后端接入；预览模式不会跳转到第三方。') },
    completeGoogle: async username => {
      if (accounts.some(account => account.username === username)) throw new Error('此预览用户名已被使用，请更换。')
      const user: Account = { id: crypto.randomUUID(), username, email: 'google-preview@example.com', role: 'user', enabled: true, emailVerified: true, googleLinked: true }
      accounts = [...accounts, user]
      return login(user)
    },
    listUsers: async (_, params) => {
      requireAdmin()
      const rows = accounts.filter(account => `${account.username} ${account.email}`.toLowerCase().includes(params.search.toLowerCase()))
      return { items: rows.slice((params.page - 1) * params.pageSize, params.page * params.pageSize).map(account => ({ ...account })), total: rows.length }
    },
    updateUser: async (id: string, change: UserChange) => {
      requireAdmin()
      const user = accounts.find(account => account.id === id)
      if (!user) throw new Error('用户不存在，请刷新列表。')
      if (user.role === 'admin') throw new Error('不能修改唯一超级管理员的角色或启用状态。')
      const updated = { ...user, ...change }
      accounts = accounts.map(account => account.id === id ? updated : account)
      return { ...updated }
    },
    authorizeCli: async () => { throw new Error('CLI 授权需要服务端验证请求，当前仅展示确认界面。') },
  }
}
