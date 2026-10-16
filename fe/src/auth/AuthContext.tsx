import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { AuthAdapter, LoginSession } from './contracts'
import { authEvent, authSyncKey, clearIdentityRequests, syncIdentityTabs } from './transport'

type AuthState = {
  adapter: AuthAdapter
  session: LoginSession | null
  loading: boolean
  error?: string
  acceptSession: (session: LoginSession | null) => void
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}
const Context = createContext<AuthState | null>(null)
const fingerprint = (value: LoginSession | null) => value ? `${value.user.id}:${value.loginSessionId ?? ''}:${value.user.authVersion ?? 0}:${value.user.role}:${value.user.emailVerified}:${value.user.needsUsername}` : ''

export function AuthProvider({ adapter, children, live = false }: { adapter: AuthAdapter; children: ReactNode; live?: boolean }) {
  const [session, setSession] = useState<LoginSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const generation = useRef(0)
  const current = useRef<LoginSession | null>(null)
  const commit = useCallback((value: LoginSession | null) => {
    if (live && fingerprint(value) !== fingerprint(current.current)) clearIdentityRequests()
    current.current = value
    setSession(value)
  }, [live])
  const acceptSession = useCallback((value: LoginSession | null) => {
    generation.current += 1
    commit(value)
    setError(undefined)
    setLoading(false)
  }, [commit])
  const refresh = useCallback(async () => {
    const version = ++generation.current
    const controller = new AbortController()
    try {
      const result = await adapter.currentSession(controller.signal)
      if (version === generation.current) { commit(result ? { ...result, user: { ...result.user } } : null); setError(undefined) }
    } catch (reason) {
      if (version === generation.current) { commit(null); setError(reason instanceof Error ? reason.message : '账号状态暂不可用，请重试。') }
    } finally {
      if (version === generation.current) setLoading(false)
    }
  }, [adapter, commit])
  useEffect(() => { void refresh(); return () => { generation.current += 1 } }, [refresh])
  useEffect(() => {
    if (!live) return
    const invalid = (event: Event) => {
      const code = (event as CustomEvent<string>).detail
      acceptSession(null)
      if (!['AUTH_REQUIRED', 'ACCOUNT_DISABLED'].includes(code)) { setLoading(true); void refresh() }
    }
    const sync = (event: StorageEvent) => { if (event.key === authSyncKey) { clearIdentityRequests(); commit(null); setLoading(true); void refresh() } }
    const focus = () => { if (document.visibilityState === 'visible') void refresh() }
    const interval = setInterval(() => { if (document.visibilityState === 'visible' && current.current) void refresh() }, 15_000)
    window.addEventListener(authEvent, invalid)
    window.addEventListener('storage', sync)
    window.addEventListener('focus', focus)
    document.addEventListener('visibilitychange', focus)
    return () => { clearInterval(interval); window.removeEventListener(authEvent, invalid); window.removeEventListener('storage', sync); window.removeEventListener('focus', focus); document.removeEventListener('visibilitychange', focus) }
  }, [live, refresh, acceptSession, commit])
  useEffect(() => {
    if (!session) return
    const expiry = Date.parse(session.expiresAt)
    const remaining = expiry - Date.now()
    if (!Number.isFinite(expiry) || remaining <= 0) { acceptSession(null); return }
    const timeout = setTimeout(() => { acceptSession(null); if (live) syncIdentityTabs() }, Math.min(remaining, 86_400_000))
    return () => clearTimeout(timeout)
  }, [session, acceptSession, live])
  const signOut = useCallback(async () => {
    await adapter.signOut()
    acceptSession(null)
  }, [adapter, acceptSession])
  return <Context.Provider value={{ adapter, session, loading, error, acceptSession, refresh, signOut }}>{children}</Context.Provider>
}

export function useAuth() {
  const value = useContext(Context)
  if (!value) throw new Error('AuthProvider is required')
  return value
}
