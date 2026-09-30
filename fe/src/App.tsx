import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Form, Select, Space, Spin, Tag, Timeline, Typography, message } from 'antd'
import { AppstoreOutlined, ClockCircleOutlined, ControlOutlined, PlusOutlined, SwapOutlined, TruckOutlined, ArrowLeftOutlined } from '@ant-design/icons'
import { PageContainer, ProFormDateTimePicker, ProFormDigit, ProFormSelect, ProFormText, ModalForm, ProLayout, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import dayjs from 'dayjs'
import { api, ApiError, type Action, type Candidate, type Order, type OrderDetail, type OrderInput, type RouteCode, type Shipment, type ShipmentDetail, type Stage, type TaskDetail, type TaskItem, type Station, type TransportRoute } from './api'
import NetworkPage from './NetworkPage'
import { statusTagStyles, type StatusTone } from './theme'

const { Text, Title } = Typography
const legacyStageText: Record<string, string> = { AT_A: 'A 站内', IN_TRANSIT_AB: 'A → B 运输中', AT_B: 'B 站内', IN_TRANSIT_BC: 'B → C 运输中', AT_C: 'C 站内' }
const stageText: Record<Stage, string> = new Proxy({ PENDING_PICKUP: '待揽收', PICKED_UP: '已揽收', AT_STATION: '在站', IN_TRANSIT: '运输中', OUT_FOR_DELIVERY: '派送中', SIGNED: '已签收' }, { get: (labels, property) => typeof property === 'string' ? labels[property as Stage] ?? legacyStageText[property] ?? property : Reflect.get(labels, property) })
const taskStatusText: Record<string, string> = new Proxy({ PENDING_DEPARTURE: '待发车', IN_TRANSIT: '运输中', ARRIVED: '已到达' }, { get: (labels, property) => typeof property === 'string' ? labels[property as keyof typeof labels] ?? property : Reflect.get(labels, property) })
const eventText: Record<string, string> = { SHIPMENT_CREATED: '发货单已创建', PICKUP: '包裹已揽收', ARRIVE: '到达并入站', DEPART: '运输任务已发车', START_DELIVERY: '开始派送', SIGN: '买家已签收' }
const actionText: Record<string, string> = { PICKUP: '揽收', ARRIVE: '首次入站', START_DELIVERY: '开始派送', SIGN: '签收', DEPART: '任务发车' }
const stages = Object.fromEntries(Object.entries(stageText).map(([value, text]) => [value, { text }]))
const blankOrder: OrderInput = { product_name: '', quantity: 1, sender_name: '', sender_address: '', recipient_name: '', recipient_address: '' }

function formatTime(value?: string | null) { return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—' }
function apiError(error: unknown) {
  if (error instanceof ApiError) {
    const fields = error.details?.map(item => `${item.field.replace(/^body\./, '')}: ${item.message}`).join('；')
    return `${fields || error.message}${error.requestId ? `（请求 ID ${error.requestId}）` : ''}`
  }
  return error instanceof Error ? error.message : '请求失败，请重试。'
}
function delayText(item: { delay_status: string; delay_minutes: number | null }) {
  if (item.delay_status === 'OVERDUE') return `超时中 · ${item.delay_minutes ?? 0} 分钟`
  if (item.delay_status === 'LATE_ARRIVAL') return `已到达 · 晚到 ${item.delay_minutes ?? 0} 分钟`
  return item.delay_status === 'NOT_APPLICABLE' ? '未启用延误监测' : '未延误'
}
function stageLabel(value: string) { return legacyStageText[value] ?? stageText[value as Stage] ?? value }
function allowed(actions: Action[], action: string) { return actions.find(item => item.action === action) }
function StatusTag({ tone, children, icon }: { tone: StatusTone; children: ReactNode; icon?: ReactNode }) {
  return <Tag icon={icon} style={statusTagStyles[tone]}>{children}</Tag>
}

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
  const mutate = useCallback(async <T,>(identity: string, action: (key: string) => Promise<T>, success: string): Promise<T | undefined> => {
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
  }, [busy, messageApi, reloadCurrent])

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
  </ProLayout></>
}

type Mutate = <T,>(identity: string, action: (key: string) => Promise<T>, success: string) => Promise<T | undefined>
type Shared = { revision: number; busy: boolean; mutate: Mutate }

function useNetwork(revision: number) {
  const [stations, setStations] = useState<Station[]>([])
  const [routes, setRoutes] = useState<TransportRoute[]>([])
  const [error, setError] = useState<string>()
  useEffect(() => { let active = true; Promise.all([api.stations(), api.routes()]).then(([s, r]) => {
    if (active) { setStations(s); setRoutes(r); setError(undefined) }
  }).catch(e => { if (active) setError(apiError(e)) }); return () => { active = false } }, [revision])
  return { stations, routes, error }
}

function OrdersPage({ revision, mutate }: Pick<Shared, 'revision' | 'mutate'>) {
  const actionRef = useRef<ActionType | undefined>(undefined)
  const [formOpen, setFormOpen] = useState(false)
  const [loadError, setLoadError] = useState<string>()
  const navigate = useNavigate()
  useEffect(() => { actionRef.current?.reload() }, [revision])
  const columns: ProColumns<Order>[] = [
    { title: '订单号', dataIndex: 'order_no', copyable: true, width: 150 },
    { title: '运单号', dataIndex: 'shipment_no', hideInTable: true, hideInSearch: false },
    { title: '商品', dataIndex: 'product_name', search: false, render: (_, row) => `${row.product_name} × ${row.quantity}` },
    { title: '订单状态', dataIndex: 'status', search: false, valueEnum: { PENDING_SHIPMENT: { text: '待创建运单' }, SHIPMENT_CREATED: { text: '运单已创建' }, COMPLETED: { text: '已完成' } } },
    { title: '运单 / 运输阶段', dataIndex: 'stage', valueType: 'select', valueEnum: stages, fieldProps: { placeholder: '全部运输阶段' }, render: (_, row) => <OrderShipment orderId={row.id} orderStatus={row.status} revision={revision} /> },
    { title: '创建时间', dataIndex: 'created_at', valueType: 'dateTime', search: false },
    { title: '操作', valueType: 'option', render: (_, row) => <a onClick={() => navigate(`/orders/${row.id}`)}>查看详情</a> },
  ]
  return <PageContainer title="订单与运单" subTitle="创建订单、生成发货单，并查看从揽收到签收的完整轨迹。" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setFormOpen(true)}>创建模拟订单</Button>}>
    {loadError && <Alert type="error" showIcon message="订单列表加载失败" description={loadError} action={<Button size="small" onClick={() => actionRef.current?.reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <ProTable<Order> actionRef={actionRef} rowKey="id" columns={columns} scroll={{ x: 980 }} search={{ labelWidth: 96 }} options={{ reload: true, density: true, setting: true }} pagination={{ pageSize: 20, showSizeChanger: true }} request={async params => {
      try {
        const result = await api.orders({ page: params.current ?? 1, page_size: params.pageSize ?? 20, order_no: params.order_no as string, shipment_no: params.shipment_no as string, stage: params.stage as string })
        setLoadError(undefined)
        return { data: result.items, success: true, total: result.total }
      } catch (error) { const reason = apiError(error); setLoadError(reason); return { data: [], success: false, total: 0 } }
    }} />
    <ModalForm<OrderInput> title="创建模拟订单" open={formOpen} onOpenChange={setFormOpen} initialValues={blankOrder} modalProps={{ destroyOnClose: true }} submitter={{ searchConfig: { submitText: '创建订单' } }} onFinish={async values => {
      const saved = await mutate(`create-order:${JSON.stringify(values)}`, key => api.createOrder(values, key), '订单已创建')
      if (saved) { actionRef.current?.reload(); navigate(`/orders/${saved.id}`) }
      return Boolean(saved)
    }}>
      <ProFormText name="product_name" label="商品名称" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} />
      <ProFormDigit name="quantity" label="商品数量" min={1} precision={0} rules={[{ required: true }]} />
      <ProFormText name="sender_name" label="卖家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} />
      <ProFormText name="sender_address" label="卖家详细地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} />
      <ProFormText name="recipient_name" label="买家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} />
      <ProFormText name="recipient_address" label="买家详细地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} />
      <Text type="secondary">配送区域固定为 Z，订单号由后端生成。</Text>
    </ModalForm>
  </PageContainer>
}

function OrderDetailPage({ revision, busy, mutate }: Shared) {
  const { orderId = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<OrderDetail>()
  const [shipment, setShipment] = useState<ShipmentDetail>()
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string>()
  const [retry, setRetry] = useState(0)
  const [editOrderOpen, setEditOrderOpen] = useState(false)
  const [shipmentOpen, setShipmentOpen] = useState(false)
  const network = useNetwork(revision)
  const [addressOpen, setAddressOpen] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    setLoadError(undefined)
    Promise.resolve().then(async () => {
      const order = await api.order(orderId)
      const shipmentDetail = order.shipment ? await api.shipment(order.shipment.id) : undefined
      if (active) { setDetail(order); setShipment(shipmentDetail) }
    }).catch(error => { if (active) setLoadError(apiError(error)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [orderId, revision, retry])

  const title = detail ? `${detail.order_no}${shipment ? ` / ${shipment.shipment_no}` : ''}` : '订单详情'
  return <PageContainer title={title} subTitle="订单信息、运单当前位置与完整物流轨迹。" extra={<Space wrap><Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/orders')}>返回订单列表</Button>{detail && !detail.shipment && <Button onClick={() => setEditOrderOpen(true)}>编辑订单</Button>}{detail && !detail.shipment && <Button type="primary" loading={busy} onClick={() => setShipmentOpen(true)}>创建发货单</Button>}</Space>}>
    {loadError && <Alert type="error" showIcon message="订单详情加载失败" description={loadError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} style={{ marginBottom: 16 }} />}
    {loading && !detail ? <Spin /> : detail && <>
      {shipment && <ShipmentLocation shipment={shipment} />}
      <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
        <Descriptions.Item label="订单状态">{detail.status === 'COMPLETED' ? '已完成' : detail.shipment ? '运单已创建' : '待创建运单'}</Descriptions.Item>
        <Descriptions.Item label="商品">{detail.product_name} × {detail.quantity}</Descriptions.Item>
        <Descriptions.Item label="下单发件地址"><span className="jp-wrap-anywhere">{detail.sender_name} · {detail.sender_address}</span></Descriptions.Item>
        <Descriptions.Item label="下单收件地址"><span className="jp-wrap-anywhere">{detail.recipient_name} · {detail.recipient_address}</span></Descriptions.Item>
        {shipment && <><Descriptions.Item label="运单发件地址"><span className="jp-wrap-anywhere">{shipment.sender_address}</span></Descriptions.Item><Descriptions.Item label="运单目的站"><StationName id={shipment.destination_station_id} /></Descriptions.Item><Descriptions.Item label="当前配送地址"><span className="jp-wrap-anywhere">{shipment.recipient_address}</span></Descriptions.Item></>}
        <Descriptions.Item label="最近扫描站点">{shipment?.last_scanned_station_id ? <StationName id={shipment.last_scanned_station_id} /> : '—'}</Descriptions.Item>
        <Descriptions.Item label="当前阶段">{shipment ? <StatusTag tone="info">{stageText[shipment.stage]}</StatusTag> : '未创建运单'}</Descriptions.Item>
        {shipment?.stage === 'AT_STATION' && <Descriptions.Item label="当前所在站">{shipment.last_scanned_station_id && <StationName id={shipment.last_scanned_station_id} />}</Descriptions.Item>}
        {shipment?.active_transport_task && <Descriptions.Item label={shipment.stage === 'IN_TRANSIT' ? '当前运输' : '待发车线路'}><StationName id={shipment.active_transport_task.origin_station_id} /> → <StationName id={shipment.active_transport_task.destination_station_id} /></Descriptions.Item>}
      </Descriptions>
      {shipment?.stage === 'PENDING_PICKUP' && <Button style={{ marginTop: 16 }} onClick={() => setAddressOpen(true)}>编辑运单地址</Button>}
      <Title level={5} style={{ marginTop: 28 }}>物流轨迹</Title>
      {shipment ? <Timeline items={shipment.tracking_events.map(event => ({ children: <><Text strong>{eventText[event.event_type] ?? event.event_type}</Text><br /><Text type="secondary">{formatTime(event.occurred_at)}{event.station_id ? ' · ' : ''}</Text>{event.station_id && <StationName id={event.station_id} />}</> }))} /> : <Empty description="创建发货单后，物流轨迹会显示在这里" />}
      <Text type="secondary">建运单前可编辑订单；揽收前可修改运单地址；区域 Z 固定。</Text>
    </>}
    <ModalForm<{ destination_station_id: string }> title="创建发货单" open={shipmentOpen} onOpenChange={setShipmentOpen} modalProps={{ destroyOnClose: true }} onFinish={async values => {
      if (!detail) return false
      const destination = Number(values.destination_station_id)
      return Boolean(await mutate(`create-shipment:${detail.id}:${destination}`, key => api.createShipment(detail.id, destination, key), '运单已创建'))
    }}>
      {network.error && <Alert type="error" message={network.error} />}
      <ProFormSelect name="destination_station_id" label="目的站" options={network.stations.filter(s => s.enabled && s.allows_delivery).map(s => ({ value: s.id, label: `${s.code} · ${s.name}` }))} rules={[{ required: true }]} fieldProps={{ placeholder: '选择负责最终派送的站点', notFoundContent: '暂无可派送站点，请先在网络配置中创建' }} />
      <Text type="secondary">目的站创建后不可变更；运单到达该站后才能开始派送。</Text>
    </ModalForm>
    <ModalForm<OrderInput> title={`编辑订单 ${detail?.order_no ?? ''}`} open={editOrderOpen} onOpenChange={setEditOrderOpen} initialValues={detail ? { product_name: detail.product_name, quantity: detail.quantity, sender_name: detail.sender_name, sender_address: detail.sender_address, recipient_name: detail.recipient_name, recipient_address: detail.recipient_address } : blankOrder} modalProps={{ destroyOnClose: true }} submitter={{ searchConfig: { submitText: '保存修改' } }} onFinish={async values => {
      if (!detail) return false
      const result = await mutate(`edit-order:${detail.id}:${JSON.stringify(values)}`, key => api.updateOrder(detail.id, values, key), '订单已更新')
      if (result) setEditOrderOpen(false)
      return Boolean(result)
    }}>
      <ProFormText name="product_name" label="商品名称" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} /><ProFormDigit name="quantity" label="商品数量" min={1} precision={0} rules={[{ required: true }]} /><ProFormText name="sender_name" label="卖家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} /><ProFormText name="sender_address" label="卖家详细地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /><ProFormText name="recipient_name" label="买家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} /><ProFormText name="recipient_address" label="买家详细地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /><Text type="secondary">配送区域固定为 Z，订单号由后端生成。</Text>
    </ModalForm>
    <ModalForm<{ sender_address: string; recipient_address: string }> title="编辑运单地址" open={addressOpen} onOpenChange={setAddressOpen} initialValues={{ sender_address: shipment?.sender_address, recipient_address: shipment?.recipient_address }} modalProps={{ destroyOnClose: true }} onFinish={async values => {
      if (!shipment) return false
      const result = await mutate(`address:${shipment.id}:${JSON.stringify(values)}`, key => api.updateAddress(shipment.id, values, key), '运单地址已更新')
      if (result) { setShipment(result); setAddressOpen(false) }
      return Boolean(result)
    }}><ProFormText name="sender_address" label="发件地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /><ProFormText name="recipient_address" label="收件地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /></ModalForm>
  </PageContainer>
}

function OrderShipment({ orderId, orderStatus, revision }: { orderId: string; orderStatus: string; revision: number }) {
  const [shipment, setShipment] = useState<OrderDetail['shipment']>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setLoading(true)
    setFailed(false)
    api.order(orderId).then(result => { if (active) setShipment(result.shipment) }).catch(() => { if (active) setFailed(true) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [orderId, revision, retry])
  if (loading) return <Text type="secondary">加载运单信息…</Text>
  if (failed) return <Space size={4}><Text type="secondary">运单信息暂不可用</Text><Button type="link" size="small" onClick={() => setRetry(value => value + 1)}>重试</Button></Space>
  if (!shipment) return orderStatus === 'PENDING_SHIPMENT' ? <Tag>未创建运单</Tag> : orderStatus === 'COMPLETED' ? <StatusTag tone="success">已签收</StatusTag> : <Text type="secondary">尚未关联运单</Text>
  return <Space><Text code>{shipment.shipment_no}</Text><StatusTag tone={shipment.stage === 'SIGNED' ? 'success' : 'info'}>{stageLabel(shipment.stage)}</StatusTag></Space>
}
function StationName({ id }: { id: string }) {
  const [station, setStation] = useState<{ code: string; name: string }>()
  useEffect(() => { let active = true; api.stations().then(rows => { if (active) setStation(rows.find(row => row.id === id)) }).catch(() => { if (active) setStation(undefined) }); return () => { active = false } }, [id])
  return <>{station ? `${station.code} 站（${station.name}）` : id}</>
}

function ShipmentLocation({ shipment }: { shipment: ShipmentDetail }) {
  if (shipment.stage === 'AT_STATION' && shipment.last_scanned_station_id) {
    return <Alert style={{ marginBottom: 16 }} type="info" showIcon message={<>当前所在站：<Text strong><StationName id={shipment.last_scanned_station_id} /></Text></>} />
  }
  if (shipment.stage === 'IN_TRANSIT' && shipment.active_transport_task) {
    return <Alert style={{ marginBottom: 16 }} type="info" showIcon message={<>当前运输区间：<Text strong><StationName id={shipment.active_transport_task.origin_station_id} /> → <StationName id={shipment.active_transport_task.destination_station_id} /></Text></>} description={<>最近扫描在 <StationName id={shipment.last_scanned_station_id ?? shipment.active_transport_task.origin_station_id} />；到达目的站并入站后才会显示为“当前所在站”。</>} />
  }
  if (shipment.stage === 'OUT_FOR_DELIVERY') return <Alert style={{ marginBottom: 16 }} type="info" showIcon message="包裹正在派送，等待签收。" />
  if (shipment.stage === 'SIGNED') return <Alert style={{ marginBottom: 16 }} type="success" showIcon message="包裹已签收。" />
  return null
}

function TaskShipmentTag({ item }: { item: TaskDetail['shipments'][number] }) {
  const [shipment, setShipment] = useState<ShipmentDetail>()
  useEffect(() => { let active = true; api.shipment(item.id).then(result => { if (active) setShipment(result) }).catch(() => {}); return () => { active = false } }, [item.id])
  return <Tag key={item.id}>{item.shipment_no} · {stageText[item.stage]}{shipment?.stage === 'AT_STATION' && shipment.last_scanned_station_id ? <> · 当前在 <StationName id={shipment.last_scanned_station_id} /></> : shipment?.stage === 'IN_TRANSIT' && shipment.active_transport_task ? <> · <StationName id={shipment.active_transport_task.origin_station_id} /> → <StationName id={shipment.active_transport_task.destination_station_id} /> 运输中</> : null}</Tag>
}

function TasksPage({ revision, mutate, clock }: Pick<Shared, 'revision' | 'mutate'> & { clock?: string }) {
  const network = useNetwork(revision)
  const [taskForm] = Form.useForm()
  const actionRef = useRef<ActionType | undefined>(undefined)
  const [createOpen, setCreateOpen] = useState(false)
  const [loadError, setLoadError] = useState<string>()
  const [messageApi, holder] = message.useMessage()
  const navigate = useNavigate()
  useEffect(() => { actionRef.current?.reload() }, [revision])
  const columns: ProColumns<TaskItem>[] = [
    { title: '任务号', dataIndex: 'task_no', copyable: true },
    { title: '线路', dataIndex: 'route_code', valueEnum: Object.fromEntries(network.routes.map(r => [r.code, { text: `${r.code} · ${r.origin.code} → ${r.destination.code}${r.enabled ? "" : "（停用）"}` }])) },
    { title: '任务状态', dataIndex: 'status', valueEnum: Object.fromEntries(Object.entries(taskStatusText).map(([key, text]) => [key, { text }])) },
    { title: '预计到达', dataIndex: 'expected_arrival_at', valueType: 'dateTime', search: false },
    { title: '实际到达', dataIndex: 'arrived_at', valueType: 'dateTime', search: false },
    { title: '延误提示', dataIndex: 'delay_status', search: false, render: (_, row) => row.delay_status === 'OVERDUE' ? <StatusTag tone="error">{delayText(row)}</StatusTag> : row.delay_status === 'LATE_ARRIVAL' ? <StatusTag tone="warning">{delayText(row)}</StatusTag> : delayText(row) },
    { title: '操作', valueType: 'option', render: (_, row) => <a onClick={() => navigate(`/tasks/${row.id}`)}>查看详情</a> },
  ]
  return <>{holder}<PageContainer title="运输任务" subTitle="选择已配置的线路创建任务，并跟踪运输状态。" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>创建运输任务</Button>}>
    {network.error && <Alert type="error" message={network.error} style={{ marginBottom: 16 }} />}{loadError && <Alert type="error" showIcon message="运输任务列表加载失败" description={loadError} action={<Button size="small" onClick={() => actionRef.current?.reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <ProTable<TaskItem> actionRef={actionRef} rowKey="id" columns={columns} search={{ labelWidth: 90 }} pagination={{ pageSize: 20 }} request={async params => {
      try { const result = await api.tasks({ page: params.current ?? 1, page_size: params.pageSize ?? 20, task_no: params.task_no as string, route_code: params.route_code as string, status: params.status as string }); setLoadError(undefined); return { data: result.items, success: true, total: result.total } }
      catch (error) { const reason = apiError(error); setLoadError(reason); return { data: [], success: false, total: 0 } }
    }} />
    <ModalForm title="创建运输任务" dateFormatter="string" open={createOpen} onOpenChange={setCreateOpen} form={taskForm} onValuesChange={changes => { if ("route_code" in changes) taskForm.setFieldValue("shipment_ids", []) }} modalProps={{ destroyOnClose: true }} onFinish={async (values: { route_code: RouteCode; expected_arrival_at: string; shipment_ids: string[] }) => {
      // ProForm submits dateTime as a formatted string; this field is explicitly Beijing time.
      const body = { route_code: values.route_code, expected_arrival_at: `${values.expected_arrival_at.replace(' ', 'T')}+08:00`, shipment_ids: values.shipment_ids.map(Number) }
      const result = await mutate(`create-task:${JSON.stringify(body)}`, key => api.createTask(body, key), '运输任务已创建；运单仍在起点站')
      if (result) { setCreateOpen(false); actionRef.current?.reload(); navigate(`/tasks/${result.id}`) }
      return Boolean(result)
    }}>
      <ProFormSelect name="route_code" label="运输线路" options={network.routes.filter(r => r.enabled && r.origin.enabled && r.destination.enabled).map(r => ({ label: `${r.code} · ${r.origin.code} → ${r.destination.code}`, value: r.code }))} fieldProps={{ notFoundContent: "暂无可用线路，请先配置站点和线路" }} rules={[{ required: true }]} />
      <ProFormDateTimePicker name="expected_arrival_at" label="预计到达时间（北京时间）" rules={[{ required: true }]} fieldProps={{ showTime: true, disabledDate: date => Boolean(clock && date.isBefore(dayjs(clock), 'day')) }} />
      <ProFormSelect name="shipment_ids" label="待出站运单" mode="multiple" dependencies={['route_code']} rules={[{ required: true, message: '至少选择一张运单' }]} request={async params => {
        try {
          if (!params.route_code) return []
          const result = await api.candidates(params.route_code as RouteCode, 1)
          return result.items.map((item: Candidate) => ({ label: `${item.shipment_no} · ${stageText[item.stage]}`, value: item.id }))
        } catch (error) {
          messageApi.error(apiError(error))
          return []
        }
      }} fieldProps={{ showSearch: true, optionFilterProp: 'label', placeholder: '选择一张或多张运单', maxTagCount: 'responsive' }} />
      <Text type="secondary">预计到达须晚于当前演示时间 {formatTime(clock)}。任务创建后不可编辑。</Text>
    </ModalForm>
  </PageContainer></>
}

function TaskDetailPage({ revision, goSimulation }: { revision: number; goSimulation: () => void }) {
  const { taskId = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<TaskDetail>()
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string>()
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    setLoadError(undefined)
    api.task(taskId).then(result => { if (active) setDetail(result) }).catch(error => { if (active) setLoadError(apiError(error)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [taskId, revision, retry])

  return <PageContainer title={detail?.task_no ?? '运输任务详情'} subTitle="查看线路、到达站点、任务时间与关联运单当前位置。" extra={<Space wrap><Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tasks')}>返回运输任务</Button><Button type="primary" onClick={goSimulation}>打开演示控制</Button></Space>}>
    {loadError && <Alert type="error" showIcon message="运输任务详情加载失败" description={loadError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} style={{ marginBottom: 16 }} />}
    {loading && !detail ? <Spin /> : detail && <>
      {detail.status === 'ARRIVED' && <Alert style={{ marginBottom: 16 }} type="success" showIcon message={<>任务已到达：<Text strong><StationName id={detail.destination_station_id} /></Text></>} />}
      <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
        <Descriptions.Item label="运输线路"><StationName id={detail.origin_station_id} /> → <StationName id={detail.destination_station_id} /></Descriptions.Item>
        <Descriptions.Item label="任务状态">{taskStatusText[detail.status]}</Descriptions.Item>
        <Descriptions.Item label="预计到达">{formatTime(detail.expected_arrival_at)}</Descriptions.Item>
        <Descriptions.Item label="实际发车">{formatTime(detail.departed_at)}</Descriptions.Item>
        <Descriptions.Item label="实际到达">{formatTime(detail.arrived_at)}</Descriptions.Item>
        <Descriptions.Item label="延误提示">{detail.delay_status === 'OVERDUE' ? <StatusTag tone="error">{delayText(detail)}</StatusTag> : detail.delay_status === 'LATE_ARRIVAL' ? <StatusTag tone="warning">{delayText(detail)}</StatusTag> : delayText(detail)}</Descriptions.Item>
        <Descriptions.Item label="关联运单" span={2}><Space wrap>{detail.shipments.map(item => <TaskShipmentTag key={item.id} item={item} />)}</Space></Descriptions.Item>
      </Descriptions>
      <Text type="secondary" style={{ display: 'block', marginTop: 16 }}>演示时间：{formatTime(detail.simulation_time)}。页面操作将按该时钟记录。</Text>
    </>}
  </PageContainer>
}

function SimulationPage({ revision, busy, mutate, clock }: Shared & { clock?: string }) {
  const network = useNetwork(revision)
  const [firstStation, setFirstStation] = useState<string>()
  const [shipments, setShipments] = useState<Shipment[]>([])
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [shipment, setShipment] = useState<ShipmentDetail>()
  const [task, setTask] = useState<TaskDetail>()
  const [messageApi, holder] = message.useMessage()
  const [loadingLists, setLoadingLists] = useState(true)
  const [loadError, setLoadError] = useState<string>()
  const refreshLists = useCallback(async () => {
    setLoadingLists(true)
    setLoadError(undefined)
    try {
      const [shipmentPage, taskPage] = await Promise.all([api.shipments({ page: 1, page_size: 100 }), api.tasks({ page: 1, page_size: 100 })])
      setShipments(shipmentPage.items); setTasks(taskPage.items)
      if (shipment?.id) setShipment(await api.shipment(shipment.id))
      if (task?.id) setTask(await api.task(task.id))
    } catch (error) { const reason = apiError(error); setLoadError(reason); messageApi.error(reason) }
    finally { setLoadingLists(false) }
  }, [messageApi, shipment?.id, task?.id])
  useEffect(() => { void refreshLists() }, [revision, refreshLists])
  const runShipment = async (event: string) => {
    if (!shipment) return
    if (event === "ARRIVE" && !firstStation) { messageApi.error("请先选择首次入站站点"); return }
    const result = await mutate(`shipment-event:${shipment.id}:${event}:${event === "ARRIVE" ? firstStation : ""}`, async key => {
      const stationId = event === 'ARRIVE' ? firstStation : undefined
      return api.shipmentEvent(shipment.id, event, stationId, key)
    }, `${actionText[event]}已完成`)
    if (result) setShipment(result)
  }
  const runTask = async (action: 'depart' | 'arrive') => {
    if (!task) return
    const result = await mutate(`task-action:${task.id}:${action}`, key => api.taskAction(task.id, action, key), action === 'depart' ? '任务已发车' : '任务已到达，全部关联运单已入站')
    if (result) setTask(await api.task(task.id))
  }
  return <>{holder}<PageContainer title="演示控制" subTitle="用模拟事件推进物流链路，所有业务时间由后端演示时钟决定。">
    {network.error && <Alert type="error" message={network.error} style={{ marginBottom: 16 }} />}
    {loadError && <Alert type="error" showIcon message="演示数据加载失败" description={loadError} action={<Button size="small" onClick={() => void refreshLists()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <Card title={<Space><ClockCircleOutlined />演示时钟</Space>} extra={<Space><Button disabled={busy} onClick={() => mutate(`advance:${clock}:30`, key => api.advance(30, key), '演示时钟已推进 30 分钟')}>推进 30 分钟</Button><Button type="primary" disabled={busy} onClick={() => mutate(`advance:${clock}:120`, key => api.advance(120, key), '演示时钟已推进 2 小时')}>推进 2 小时</Button></Space>} style={{ marginBottom: 16 }}>
      <Title level={3} style={{ marginTop: 0 }}>{formatTime(clock)}</Title><Text type="secondary">推进时钟只检查超时，不自动发车或到达。</Text>
    </Card>
    <div className="simulation-grid"><Card title="运单事件" extra={<TruckOutlined />}><Form layout="vertical"><Form.Item label="选择运单"><Select allowClear showSearch optionFilterProp="label" value={shipment?.id} loading={loadingLists} disabled={!shipments.length} placeholder={loadingLists ? '正在加载运单…' : shipments.length ? '请选择运单' : '暂无可操作运单'} notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的运单" />} options={shipments.map(item => ({ value: item.id, label: `${item.shipment_no} · ${stageText[item.stage]}` }))} onChange={async id => { setShipment(undefined); if (!id) return; try { setShipment(await api.shipment(id)) } catch (error) { messageApi.error(apiError(error)) } }} /></Form.Item></Form>
      {shipment && <><Space wrap style={{ marginBottom: 16 }}><StatusTag tone="info">{stageText[shipment.stage]}</StatusTag><Text type="secondary">最后扫描：{shipment.last_scanned_station_id ? <StationName id={shipment.last_scanned_station_id} /> : '—'}</Text>{shipment.active_transport_task && <Text type="secondary">当前线路：<StationName id={shipment.active_transport_task.origin_station_id} /> → <StationName id={shipment.active_transport_task.destination_station_id} /></Text>}</Space><Space direction="vertical" style={{ width: "100%" }}><Text>运单目的站：<StationName id={shipment.destination_station_id} /></Text>{shipment.stage === "PICKED_UP" && <Form layout="vertical"><Form.Item label="首次入站站点"><Select value={firstStation} onChange={setFirstStation} options={network.stations.filter(s => s.enabled && s.allows_first_arrival).map(s => ({ value: s.id, label: `${s.code} · ${s.name}` }))} placeholder="选择实际接收包裹的首站" notFoundContent="暂无允许首次入站的站点，请先配置" /></Form.Item></Form>}<Space wrap>{(['PICKUP', 'ARRIVE', 'START_DELIVERY', 'SIGN'] as const).map(event => { const rule = allowed(shipment.allowed_actions, event); return <Button key={event} disabled={busy || !rule?.enabled} title={rule?.reason ?? undefined} onClick={() => void runShipment(event)}>{actionText[event]}</Button> })}</Space></Space><Alert style={{ marginTop: 16 }} type="info" showIcon message="按当前阶段执行操作；到达运单目的站后才能开始派送。" /></>}
    </Card><Card title="运输任务事件" extra={<SwapOutlined />}><Form layout="vertical"><Form.Item label="选择运输任务"><Select allowClear showSearch optionFilterProp="label" value={task?.id} loading={loadingLists} disabled={!tasks.length} placeholder={loadingLists ? '正在加载任务…' : tasks.length ? '请选择任务' : '暂无运输任务'} notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的运输任务" />} options={tasks.map(item => ({ value: item.id, label: `${item.task_no} · ${taskStatusText[item.status]}` }))} onChange={async id => { setTask(undefined); if (!id) return; try { setTask(await api.task(id)) } catch (error) { messageApi.error(apiError(error)) } }} /></Form.Item></Form>
      {task && <><Space wrap style={{ marginBottom: 16 }}><StatusTag tone="info">{taskStatusText[task.status]}</StatusTag><Tag><StationName id={task.origin_station_id} /> → <StationName id={task.destination_station_id} /></Tag>{task.delay_status === 'OVERDUE' && <StatusTag tone="error">{delayText(task)}</StatusTag>}{task.delay_status === 'LATE_ARRIVAL' && <StatusTag tone="warning">{delayText(task)}</StatusTag>}</Space><Space wrap><Button disabled={busy || !allowed(task.allowed_actions, 'DEPART')?.enabled} title={allowed(task.allowed_actions, 'DEPART')?.reason ?? undefined} onClick={() => void runTask('depart')}>任务发车</Button><Button type="primary" disabled={busy || !allowed(task.allowed_actions, 'ARRIVE')?.enabled} title={allowed(task.allowed_actions, 'ARRIVE')?.reason ?? undefined} onClick={() => void runTask('arrive')}>到达并全部入站</Button></Space><Alert style={{ marginTop: 16 }} type="info" showIcon message="任务到达与全部关联运单入站由后端一次完成。" /></>}
    </Card></div>
  </PageContainer></>
}
