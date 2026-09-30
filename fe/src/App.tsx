import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Spin, message } from 'antd'
import { AppstoreOutlined, ClockCircleOutlined, ControlOutlined, SwapOutlined, TruckOutlined } from '@ant-design/icons'
import { ProLayout } from '@ant-design/pro-components'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api } from './api'
import { apiError, formatTime, StatusTag, type Mutate } from './shared'

const OrdersPage = lazy(() => import('./pages/OrdersPage'))
const OrderDetailPage = lazy(() => import('./pages/OrdersPage').then(module => ({ default: module.OrderDetailPage })))
const TasksPage = lazy(() => import('./pages/TasksPage'))
const TaskDetailPage = lazy(() => import('./pages/TasksPage').then(module => ({ default: module.TaskDetailPage })))
const SimulationPage = lazy(() => import('./pages/SimulationPage'))
const NetworkPage = lazy(() => import('./NetworkPage'))

export default function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const [clock, setClock] = useState<string>()
  const [messageApi, contextHolder] = message.useMessage()
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [revision, setRevision] = useState(0)
  const retryKeys = useRef(new Map<string, string>())
  const reloadCurrent = useCallback(() => setRevision(value => value + 1), [])

  useEffect(() => { api.clock().then(result => setClock(result.current_time)).catch(error => messageApi.error(apiError(error))) }, [revision, messageApi])
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
    { path: '/orders', name: '订单 / 运单', icon: <AppstoreOutlined /> },
    { path: '/tasks', name: '运输任务', icon: <SwapOutlined /> },
    { path: '/network', name: '网络配置', icon: <AppstoreOutlined /> },
    { path: '/simulation', name: '演示控制', icon: <ControlOutlined /> },
  ]
  const activePath = location.pathname.startsWith('/network') ? '/network' : location.pathname.startsWith('/tasks') ? '/tasks' : location.pathname.startsWith('/simulation') ? '/simulation' : '/orders'
  return <>{contextHolder}<ProLayout
    title="JINGPO 鲸破"
    logo={<TruckOutlined />}
    layout="mix"
    fixSiderbar
    location={{ pathname: activePath }}
    route={{ routes: menuData }}
    menuItemRender={(item, dom) => <a onClick={() => item.path && navigate(item.path)}>{dom}</a>}
    actionsRender={() => [<StatusTag key="clock" icon={<ClockCircleOutlined />} tone="info">演示时间 · {formatTime(clock)}</StatusTag>]}
    avatarProps={{ title: '演示操作员', size: 'small' }}
    contentStyle={{ minHeight: 'calc(100vh - 56px)' }}
  >
    <Suspense fallback={<Spin size="large" style={{ display: 'block', margin: '64px auto' }} />}>
      <Routes>
        <Route path="/" element={<Navigate to="/orders" replace />} />
        <Route path="/orders" element={<OrdersPage revision={revision} mutate={mutate} />} />
        <Route path="/orders/:orderId" element={<OrderDetailPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="/tasks" element={<TasksPage revision={revision} mutate={mutate} clock={clock} />} />
        <Route path="/tasks/:taskId" element={<TaskDetailPage revision={revision} goSimulation={() => navigate('/simulation')} />} />
        <Route path="/network" element={<NetworkPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="/simulation" element={<SimulationPage revision={revision} busy={busy} mutate={mutate} clock={clock} />} />
        <Route path="*" element={<Navigate to="/orders" replace />} />
      </Routes>
    </Suspense>
  </ProLayout></>
}
