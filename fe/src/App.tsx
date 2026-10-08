import { lazy, Suspense, useCallback, useRef, useState } from 'react'
import { Spin, message } from 'antd'
import { AppstoreOutlined, SwapOutlined, TruckOutlined } from '@ant-design/icons'
import { ProLayout } from '@ant-design/pro-components'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { apiError, type Mutate } from './shared'

const OrdersPage = lazy(() => import('./pages/OrdersPage'))
const OrderDetailPage = lazy(() => import('./pages/OrdersPage').then(module => ({ default: module.OrderDetailPage })))
const ShipmentsPage = lazy(() => import('./pages/ShipmentsPage'))
const ShipmentDetailPage = lazy(() => import('./pages/ShipmentsPage').then(module => ({ default: module.ShipmentDetailPage })))
const TasksPage = lazy(() => import('./pages/TasksPage'))
const TaskDetailPage = lazy(() => import('./pages/TasksPage').then(module => ({ default: module.TaskDetailPage })))
const NetworkPage = lazy(() => import('./NetworkPage'))
const TransportLinesPage = lazy(() => import('./TransportLinesPage'))
const LineServicesPage = lazy(() => import('./LineServicesPage'))

export default function App() {
  const location = useLocation()
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
    { path: '/tasks', name: '运输任务', icon: <SwapOutlined /> },
    { path: '/network', name: '网络配置', icon: <AppstoreOutlined />, children: [
      { path: '/network/stations', name: '站点管理' },
      { path: '/network/transport-lines', name: '运输线路' },
    ] },
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
    avatarProps={{ title: '演示操作员', size: 'small' }}
    contentStyle={{ minHeight: 'calc(100vh - 56px)' }}
  >
    <Suspense fallback={<Spin size="large" style={{ display: 'block', margin: '64px auto' }} />}>
      <Routes>
        <Route path="/" element={<Navigate to="/orders" replace />} />
        <Route path="/orders" element={<OrdersPage revision={revision} mutate={mutate} />} />
        <Route path="/orders/:orderId" element={<OrderDetailPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="/shipments" element={<ShipmentsPage revision={revision} />} />
        <Route path="/shipments/:shipmentId" element={<ShipmentDetailPage revision={revision} busy={busy} mutate={mutate} />} />
        <Route path="/tasks" element={<TasksPage revision={revision} mutate={mutate} />} />
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
  </ProLayout></>
}
