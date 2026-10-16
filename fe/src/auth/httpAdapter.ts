import type { Account, AuthAdapter, LoginSession } from './contracts'
import { authRequest, AuthRequestError, syncIdentityTabs } from './transport'

type UserResponse = {
  id: string; username: string; email: string; role: Account['role']; enabled: boolean
  email_verified: boolean; needs_username: boolean; auth_version: number; google_bound: boolean
  has_password: boolean; permissions: string[]
}
type SessionResponse = { user: UserResponse; expires_at: string; login_session_id: string }
const post = (body?: unknown): RequestInit => ({ method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
const account = (user: UserResponse): Account => ({
  id: user.id, username: user.username, email: user.email, role: user.role, enabled: user.enabled,
  emailVerified: user.email_verified, googleLinked: user.google_bound, needsUsername: user.needs_username,
  authVersion: user.auth_version, hasPassword: user.has_password, permissions: user.permissions,
})
const session = (result: SessionResponse): LoginSession => ({ user: account(result.user), expiresAt: result.expires_at, loginSessionId: result.login_session_id })

export const httpAuthAdapter: AuthAdapter = {
  currentSession: async signal => {
    try { return session(await authRequest<SessionResponse>('/auth/me', { signal }, false)) }
    catch (error) { if (error instanceof AuthRequestError && error.status === 401) return null; throw error }
  },
  signIn: async (username, password) => {
    const result = session(await authRequest<SessionResponse>('/auth/login', post({ username, password }), false))
    syncIdentityTabs()
    return result
  },
  register: async values => { await authRequest('/auth/register', post(values), false) },
  signOut: async () => { await authRequest('/auth/logout', post()); syncIdentityTabs() },
  resendVerification: async email => { await authRequest('/auth/resend-verification', post({ email }), false) },
  verifyEmail: async token => { await authRequest('/auth/verify-email', post({ token }), false); syncIdentityTabs() },
  requestPasswordReset: async email => { await authRequest('/auth/forgot-password', post({ email }), false) },
  resetPassword: async (token, password) => { await authRequest('/auth/reset-password', post({ token, password }), false); syncIdentityTabs() },
  changePassword: async (currentPassword, password) => {
    await authRequest('/auth/change-password', post({ current_password: currentPassword, password }))
    syncIdentityTabs()
  },
  startGoogle: async intent => {
    const result = await authRequest<{ authorization_url: string }>(intent === 'bind' ? '/auth/google/bind/start' : '/auth/google/start', intent === 'bind' ? post() : {}, intent === 'bind')
    return { redirectUrl: result.authorization_url }
  },
  completeGoogle: async username => {
    await authRequest('/auth/complete-profile', post({ username }))
    const result = session(await authRequest<SessionResponse>('/auth/me', {}, false))
    syncIdentityTabs()
    return result
  },
  listUsers: async (signal, params) => {
    const query = new URLSearchParams({ search: params.search, page: String(params.page), page_size: String(params.pageSize) })
    const result = await authRequest<{ items: UserResponse[]; total: number }>(`/admin/users?${query}`, { signal })
    return { items: result.items.map(account), total: result.total }
  },
  updateUser: async (id, change) => account(await authRequest<UserResponse>(`/admin/users/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(change) })),
  authorizeCli: async (code, approve) => { await authRequest('/auth/cli/approve', post({ user_code: code, approve })) },
}
