export type Role = 'admin' | 'ops' | 'developer' | 'user'
export type Account = {
  id: string
  username: string
  email: string
  role: Role
  enabled: boolean
  emailVerified: boolean
  googleLinked: boolean
  needsUsername?: boolean
  authVersion?: number
  permissions?: string[]
  hasPassword?: boolean
}
export type LoginSession = { user: Account; expiresAt: string; loginSessionId?: string }
export type Registration = { username: string; email: string; password: string }
export type UserChange = { role?: Exclude<Role, 'admin'>; enabled?: boolean }

export interface AuthAdapter {
  currentSession(signal: AbortSignal): Promise<LoginSession | null>
  signIn(username: string, password: string): Promise<LoginSession>
  register(values: Registration): Promise<void>
  signOut(): Promise<void>
  resendVerification(email: string): Promise<void>
  verifyEmail(token: string): Promise<void>
  requestPasswordReset(email: string): Promise<void>
  resetPassword(token: string, password: string): Promise<void>
  startGoogle(intent: 'login' | 'bind'): Promise<{ redirectUrl: string }>
  completeGoogle(username: string): Promise<LoginSession>
  listUsers(signal: AbortSignal, params: { search: string; page: number; pageSize: number }): Promise<{ items: Account[]; total: number }>
  updateUser(id: string, change: UserChange): Promise<Account>
  authorizeCli(code: string, approve: boolean): Promise<void>
}

export const roleLabels: Record<Role, string> = { admin: '超级管理员', ops: '运营人员', developer: '开发人员', user: '普通用户' }
export const roleDescriptions: Record<Role, string> = {
  admin: '管理全部业务、公共配置和用户账号。',
  ops: '查询和处理全公司业务，维护站点、线路与班次。',
  developer: '只读查询全部业务，助手提供技术化解释。',
  user: '创建和维护自己的订单，查看关联运单与进度。',
}
export const roles: Role[] = ['admin', 'ops', 'developer', 'user']
export const canManageUsers = (role: Role) => role === 'admin'
export const canOperateLogistics = (role: Role) => role === 'admin' || role === 'ops'
export const canMaintainOrders = (role: Role) => role !== 'developer'
export const hasPermission = (user: Account | undefined, permission: string) => Boolean(user && (user.permissions?.includes(permission) ?? (permission === 'users:manage' ? canManageUsers(user.role) : permission.startsWith('orders:') ? canMaintainOrders(user.role) : permission.endsWith(':write') ? canOperateLogistics(user.role) : true)))

export function safeReturnPath(value: string | null, fallback = '/account') {
  if (!value?.startsWith('/') || value.startsWith('//') || value.includes('\\') || [...value].some(character => character.charCodeAt(0) <= 32)) return fallback
  const path = value.split(/[?#]/)[0]
  if (/^\/(login|register|verify-email|forgot-password|reset-password|oauth|cli)(\/|$)/.test(path)) return fallback
  return value
}
