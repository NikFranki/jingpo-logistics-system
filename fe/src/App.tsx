import { lazy, Suspense, useCallback, useRef, useState } from 'react'
import { Button, Dropdown, Result, Spin, message } from 'antd'
import { AppstoreOutlined, LogoutOutlined, SwapOutlined, TruckOutlined, UserOutlined } from '@ant-design/icons'
import { ProLayout } from '@ant-design/pro-components'
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { apiError, type Mutate } from './shared'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { httpAuthAdapter } from './auth/httpAdapter'
import { hasPermission } from './auth/contracts'
import { clearNetworkCache } from './api'
import { useEffect } from 'react'

const OrdersPage = lazy(() => import('./pages/OrdersPage'))
const OrderDetailPage = lazy(() => import('./pages/OrdersPage').then(module => ({ default: module.OrderDetailPage })))
const ShipmentsPage = lazy(() => import('./pages/ShipmentsPage'))
const ShipmentDetailPage = lazy(() => import('./pages/ShipmentsPage').then(module => ({ default: module.ShipmentDetailPage })))
const TasksPage = lazy(() => import('./pages/TasksPage'))
const TaskDetailPage = lazy(() => import('./pages/TasksPage').then(module => ({ default: module.TaskDetailPage })))
const NetworkPage = lazy(() => import('./NetworkPage'))
const TransportLinesPage = lazy(() => import('./TransportLinesPage'))
const LineServicesPage = lazy(() => import('./LineServicesPage'))
const AssistantPanel = lazy(() => import('./AssistantPanel'))
const AccountWorkspace = lazy(() => import('./auth/AccountWorkspace'))

export default function App() {
  const { pathname } = useLocation()
  if (pathname === '/v8-preview' || pathname.startsWith('/v8-preview/')) return <Routes><Route path="/v8-preview/*" element={<Suspense fallback={<Spin />}><AccountWorkspace preview /></Suspense>} /></Routes>
  return <AuthProvider adapter={httpAuthAdapter} live><AuthenticatedApp /></AuthProvider>
}

function AuthenticatedApp() {
  const location = useLocation()
  const { pathname } = location
  const { session, loading, error, refresh } = useAuth()
  if (loading) return <Spin size="large" style={{ display: 'block', margin: '64px auto' }} />
  if (error) return <Result status="warning" title="账号服务暂不可用" subTitle={error} extra={<Button onClick={() => void refresh()}>重试</Button>} />
  if (['/login', '/register', '/verify-email', '/forgot-password', '/reset-password', '/account', '/change-password', '/admin/users', '/cli/authorize', '/oauth/google/callback'].includes(pathname)) return <Suspense fallback={<Spin />}><AccountWorkspace /></Suspense>
  if (!session) return <Navigate to={`/login?next=${encodeURIComponent(pathname + location.search)}`} replace />
  if (session.user.needsUsername) return <Navigate to="/oauth/google/callback" replace />
  if (!session.user.emailVerified) return <Navigate to="/verify-email" replace />
  if (session.user.role === 'user' && (pathname.startsWith('/network') || pathname.startsWith('/tasks'))) return <Result status="403" title="当前账号只能查看自己的订单和运单" extra={<Link to="/orders">返回订单</Link>} />
  return <LogisticsApp key={`${session.user.id}:${session.loginSessionId}:${session.user.authVersion}`} />
}

function LogisticsApp() {
  const { session, signOut } = useAuth()
  const user = session!.user
  useEffect(() => { clearNetworkCache(); return clearNetworkCache }, [])
  const location = useLocation()
  const navigate = useNavigate()
  const [messageApi, contextHolder] = message.useMessage()
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [revision, setRevision] = useState(0)
  const retryKeys = useRef(new Map<string, string>())
  const reloadCurrent = useCallback(() => setRevision(value => value + 1), [])

  const mutate: Mutate = useCallback(async <T,>(identity: string, action: (key: string) => Promise<T>, success: string): Promise<T | undefined> => {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    const key = retryKeys.current.get(identity) ?? crypto.randomUUID()
    retryKeys.current.set(identity, key)
    try {
      const result = await action(key)
      retryKeys.current.delete(identity)
      messageApi.success(success)
      reloadCurrent()
      return result
    } catch (error) {
      messageApi.error(apiError(error))
      return undefined
    } finally { busyRef.current = false; setBusy(false) }
  }, [messageApi, reloadCurrent])

  const menuData = [
    { path: '/orders', name: '订单', icon: <AppstoreOutlined /> },
    { path: '/shipments', name: '运单', icon: <TruckOutlined /> },
    ...(user.role !== 'user' ? [{ path: '/tasks', name: '运输任务', icon: <SwapOutlined /> },
      { path: '/network', name: '网络配置', icon: <AppstoreOutlined />, children: [
        { path: '/network/stations', name: '站点管理' },
        { path: '/network/transport-lines', name: '运输线路' },
      ] }] : []),
    ...(hasPermission(user, 'users:manage') ? [{ path: '/admin/users', name: '用户管理' }] : []),
  ]
  const activePath = location.pathname.startsWith('/network/stations') ? '/network/stations' : location.pathname.startsWith('/network') ? '/network/transport-lines' : location.pathname.startsWith('/tasks') ? '/tasks' : location.pathname.startsWith('/shipments') ? '/shipments' : '/orders'
  return <>{contextHolder}<ProLayout
    title="JINGPO 鲸破"
    logo={<TruckOutlined />}
    layout="mix"
    fixSiderbar
    location={{ pathname: activePath }}
    route={{ routes: menuData }}
    menuItemRender={(item, dom) => item.path ? <Link to={item.path}>{dom}</Link> : dom}
    avatarProps={false}
    actionsRender={() => [<Dropdown key="account-menu" trigger={['click']} menu={{
      items: [
        { key: 'account', icon: <UserOutlined />, label: '个人信息设置' },
        { key: 'logout', icon: <LogoutOutlined />, label: '退出登录' },
      ],
      onClick: ({ key }) => {
        if (key === 'account') navigate('/account')
        if (key === 'logout') void signOut().then(() => navigate('/login', { replace: true })).catch(reason => messageApi.error(apiError(reason)))
      },
    }}>
      <Button type="text" aria-label={`用户菜单：${user.username}`}>{user.username}</Button>
    </Dropdown>]}
    contentStyle={{ minHeight: 'calc(100vh - 56px)' }}
  >
    <Suspense fallback={<Spin size="large" style={{ display: 'block', margin: '64px auto' }} />}>
      <Routes>
        <Route path="/" element={<Navigate to="/orders" replace />} />
        <Route path="/orders" element={<OrdersPage revision={revision} mutate={mutate} />} />
        <Route path="/orders/:orderId" element={<OrderDetailPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="/shipments" element={<ShipmentsPage revision={revision} />} />
        <Route path="/shipments/:shipmentId" element={<ShipmentDetailPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="/tasks" element={<TasksPage revision={revision} />} />
        <Route path="/tasks/:taskId" element={<TaskDetailPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="/network" element={<Navigate to="/network/transport-lines" replace />} />
        <Route path="/network/path-plans" element={<Navigate to="/network/transport-lines" replace />} />
        <Route path="/network/routes" element={<Navigate to="/network/transport-lines" replace />} />
        <Route path="/network/transport-lines" element={<TransportLinesPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="/network/transport-lines/:lineId/services" element={<LineServicesPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="/network/stations" element={<NetworkPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="*" element={<Navigate to="/orders" replace />} />
      </Routes>
    </Suspense>
  </ProLayout>{import.meta.env.VITE_AGENT_V8_ENABLED === 'true' ? <Suspense fallback={null}><AssistantPanel /></Suspense> : <Button disabled style={{ position: 'fixed', right: 24, bottom: 24 }}>查询助手待 V8 接入</Button>}</>
}
