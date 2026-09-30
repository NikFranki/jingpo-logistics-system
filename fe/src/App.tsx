import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Descriptions, Drawer, Empty, Form, Select, Space, Tag, Timeline, Typography, message } from 'antd'
import { AppstoreOutlined, ClockCircleOutlined, ControlOutlined, PlusOutlined, SwapOutlined, TruckOutlined } from '@ant-design/icons'
import { PageContainer, ProFormDateTimePicker, ProFormDigit, ProFormSelect, ProFormText, ModalForm, ProLayout, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import dayjs, { type Dayjs } from 'dayjs'
import { api, ApiError, type Action, type Candidate, type Order, type OrderDetail, type OrderInput, type RouteCode, type Shipment, type ShipmentDetail, type Stage, type TaskDetail, type TaskItem } from './api'

const { Text, Title } = Typography
type Tab = '/orders' | '/tasks' | '/simulation'
const stageText: Record<Stage, string> = { PENDING_PICKUP: '待揽收', PICKED_UP: '已揽收', AT_A: 'A 站内', IN_TRANSIT_AB: 'A → B 运输中', AT_B: 'B 站内', IN_TRANSIT_BC: 'B → C 运输中', AT_C: 'C 站内', OUT_FOR_DELIVERY: '派送中', SIGNED: '已签收' }
const taskStatusText: Record<string, string> = { PENDING_DEPARTURE: '待发车', IN_TRANSIT: '运输中', ARRIVED: '已到达' }
const eventText: Record<string, string> = { SHIPMENT_CREATED: '发货单已创建', PICKUP: '包裹已揽收', ENTER_A: '包裹已在 A 站入站', DEPART_AB: 'A → B 运输任务已发车', ARRIVE_B: '到达 B 并入站', DEPART_BC: 'B → C 运输任务已发车', ARRIVE_C: '到达 C 并入站', START_DELIVERY: '开始派送', SIGN: '买家已签收' }
const actionText: Record<string, string> = { PICKUP: '揽收', ENTER_A: 'A 站入站', START_DELIVERY: '开始派送', SIGN: '签收', DEPART: '任务发车', ARRIVE: '到达并入站' }
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
  return '—'
}
function allowed(actions: Action[], action: string) { return actions.find(item => item.action === action) }

export default function App() {
  const [tab, setTab] = useState<Tab>('/orders')
  const [clock, setClock] = useState<string>()
  const [messageApi, contextHolder] = message.useMessage()
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const retryKeys = useRef(new Map<string, string>())
  const reloadCurrent = useCallback(() => setRevision(value => value + 1), [])

  useEffect(() => { api.clock().then(result => setClock(result.current_time)).catch(error => messageApi.error(apiError(error))) }, [revision, messageApi])
  const mutate = useCallback(async <T,>(identity: string, action: (key: string) => Promise<T>, success: string): Promise<T | undefined> => {
    if (busy) return
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
    } finally { setBusy(false) }
  }, [busy, messageApi, reloadCurrent])

  const menuData = [
    { path: '/orders', name: '订单 / 运单', icon: <AppstoreOutlined /> },
    { path: '/tasks', name: '运输任务', icon: <SwapOutlined /> },
    { path: '/simulation', name: '演示控制', icon: <ControlOutlined /> },
  ]
  return <>{contextHolder}<ProLayout
    title="JINGPO 鲸破"
    logo={<TruckOutlined />}
    layout="mix"
    fixSiderbar
    location={{ pathname: tab }}
    route={{ routes: menuData }}
    menuItemRender={(item, dom) => <a onClick={() => item.path && setTab(item.path as Tab)}>{dom}</a>}
    actionsRender={() => [<Tag key="clock" icon={<ClockCircleOutlined />} color="blue">演示时间 · {formatTime(clock)}</Tag>]}
    avatarProps={{ title: '演示操作员', size: 'small' }}
    contentStyle={{ minHeight: 'calc(100vh - 56px)' }}
  >
    {tab === '/orders' && <OrdersPage key="orders" revision={revision} busy={busy} mutate={mutate} />}
    {tab === '/tasks' && <TasksPage key="tasks" revision={revision} busy={busy} mutate={mutate} clock={clock} goSimulation={() => setTab('/simulation')} />}
    {tab === '/simulation' && <SimulationPage key="simulation" revision={revision} busy={busy} mutate={mutate} clock={clock} />}
  </ProLayout></>
}

type Mutate = <T,>(identity: string, action: (key: string) => Promise<T>, success: string) => Promise<T | undefined>
type Shared = { revision: number; busy: boolean; mutate: Mutate }

function OrdersPage({ revision, busy, mutate }: Shared) {
  const actionRef = useRef<ActionType | undefined>(undefined)
  const [detail, setDetail] = useState<OrderDetail>()
  const [shipment, setShipment] = useState<ShipmentDetail>()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [addressOpen, setAddressOpen] = useState(false)
  const [messageApi, holder] = message.useMessage()
  useEffect(() => { actionRef.current?.reload() }, [revision])

  const openOrder = async (id: string) => {
    try {
      const order = await api.order(id)
      setDetail(order)
      setShipment(order.shipment ? await api.shipment(order.shipment.id) : undefined)
      setDrawerOpen(true)
    } catch (error) { messageApi.error(apiError(error)) }
  }
  const columns: ProColumns<Order>[] = [
    { title: '订单号', dataIndex: 'order_no', copyable: true, width: 150 },
    { title: '运单号', dataIndex: 'shipment_no', hideInTable: true, hideInSearch: false },
    { title: '商品', dataIndex: 'product_name', search: false, render: (_, row) => `${row.product_name} × ${row.quantity}` },
    { title: '订单状态', dataIndex: 'status', search: false, valueEnum: { PENDING_SHIPMENT: { text: '待创建运单' }, SHIPMENT_CREATED: { text: '运单已创建' }, COMPLETED: { text: '已完成' } } },
    { title: '运单 / 运输阶段', dataIndex: 'stage', valueType: 'select', valueEnum: stages, fieldProps: { placeholder: '全部运输阶段' }, render: (_, row) => <OrderShipment orderId={row.id} orderStatus={row.status} revision={revision} /> },
    { title: '创建时间', dataIndex: 'created_at', valueType: 'dateTime', search: false },
    { title: '操作', valueType: 'option', render: (_, row) => <a onClick={() => void openOrder(row.id)}>查看详情</a> },
  ]
  return <>{holder}<PageContainer title="订单与运单" subTitle="创建订单、生成发货单，并查看从揽收到签收的完整轨迹。" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => { setDetail(undefined); setEditing(false); setFormOpen(true) }}>创建模拟订单</Button>}>
    <ProTable<Order> actionRef={actionRef} rowKey="id" columns={columns} search={{ labelWidth: 96 }} options={{ reload: true, density: true, setting: true }} pagination={{ pageSize: 20, showSizeChanger: true }} request={async params => {
      try {
        const result = await api.orders({ page: params.current ?? 1, page_size: params.pageSize ?? 20, order_no: params.order_no as string, shipment_no: params.shipment_no as string, stage: params.stage as string })
        return { data: result.items, success: true, total: result.total }
      } catch (error) { messageApi.error(apiError(error)); return { data: [], success: false, total: 0 } }
    }} />
    <ModalForm<OrderInput> title={editing ? `编辑订单 ${detail?.order_no}` : '创建模拟订单'} open={formOpen} onOpenChange={setFormOpen} initialValues={editing && detail ? { product_name: detail.product_name, quantity: detail.quantity, sender_name: detail.sender_name, sender_address: detail.sender_address, recipient_name: detail.recipient_name, recipient_address: detail.recipient_address } : blankOrder} modalProps={{ destroyOnClose: true }} submitter={{ searchConfig: { submitText: editing ? '保存修改' : '创建订单' } }} onFinish={async values => {
      const identity = `${editing ? `edit-order:${detail?.id}` : 'create-order'}:${JSON.stringify(values)}`
      const saved = await mutate(identity, key => editing && detail ? api.updateOrder(detail.id, values, key) : api.createOrder(values, key), editing ? '订单已更新' : '订单已创建')
      if (saved) { setFormOpen(false); actionRef.current?.reload(); await openOrder(saved.id) }
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
    <Drawer width={760} title={detail ? `${detail.order_no}${detail.shipment ? ` / ${detail.shipment.shipment_no}` : ''}` : '订单详情'} open={drawerOpen} onClose={() => setDrawerOpen(false)} extra={<Space>{detail && !detail.shipment && <Button onClick={() => { setEditing(true); setFormOpen(true) }}>编辑订单</Button>}{detail && !detail.shipment && <Button type="primary" loading={busy} onClick={async () => { const made = await mutate(`create-shipment:${detail.id}`, key => api.createShipment(detail.id, key), '运单已创建'); if (made) await openOrder(detail.id) }}>创建发货单</Button>}</Space>}>
      {detail && <><Descriptions bordered size="small" column={1}><Descriptions.Item label="订单状态">{detail.status === 'COMPLETED' ? '已完成' : detail.shipment ? '运单已创建' : '待创建运单'}</Descriptions.Item><Descriptions.Item label="商品">{detail.product_name} × {detail.quantity}</Descriptions.Item><Descriptions.Item label="卖家">{detail.sender_name} · {detail.sender_address}</Descriptions.Item><Descriptions.Item label="买家">{detail.recipient_name} · {shipment?.recipient_address ?? detail.recipient_address}</Descriptions.Item><Descriptions.Item label="最后扫描站点">{shipment?.last_scanned_station_id ? <StationName id={shipment.last_scanned_station_id} /> : '—'}</Descriptions.Item><Descriptions.Item label="当前阶段">{shipment ? <Tag color="blue">{stageText[shipment.stage]}</Tag> : '未创建运单'}</Descriptions.Item></Descriptions>
      {shipment?.stage === 'PENDING_PICKUP' && <Button style={{ marginTop: 16 }} onClick={() => setAddressOpen(true)}>编辑运单地址</Button>}
      <Title level={5} style={{ marginTop: 24 }}>物流轨迹</Title>{shipment ? <Timeline items={shipment.tracking_events.map(event => ({ children: <><Text strong>{eventText[event.event_type] ?? event.event_type}</Text><br /><Text type="secondary">{formatTime(event.occurred_at)}{event.station_id ? ` · ` : ''}</Text>{event.station_id && <StationName id={event.station_id} />}</> }))} /> : <Empty description="创建发货单后，物流轨迹会显示在这里" />}
      <Text type="secondary">建运单前可编辑订单；揽收前可修改运单地址；区域 Z 固定。</Text></>}
    </Drawer>
    <ModalForm<{ sender_address: string; recipient_address: string }> title="编辑运单地址" open={addressOpen} onOpenChange={setAddressOpen} initialValues={{ sender_address: shipment?.sender_address, recipient_address: shipment?.recipient_address }} onFinish={async values => {
      if (!shipment) return false
      const result = await mutate(`address:${shipment.id}:${JSON.stringify(values)}`, key => api.updateAddress(shipment.id, values, key), '运单地址已更新')
      if (result) { setAddressOpen(false); setShipment(result); if (detail) await openOrder(detail.id) }
      return Boolean(result)
    }}><ProFormText name="sender_address" label="发件地址" rules={[{ required: true }]} /><ProFormText name="recipient_address" label="收件地址" rules={[{ required: true }]} /></ModalForm>
  </PageContainer></>
}

function OrderShipment({ orderId, orderStatus, revision }: { orderId: string; orderStatus: string; revision: number }) {
  const [shipment, setShipment] = useState<OrderDetail['shipment']>(null)
  useEffect(() => { api.order(orderId).then(result => setShipment(result.shipment)).catch(() => setShipment(null)) }, [orderId, revision])
  if (!shipment) return orderStatus === 'PENDING_SHIPMENT' ? <Tag>未创建运单</Tag> : orderStatus === 'COMPLETED' ? <Tag color="green">已签收</Tag> : <Text type="secondary">加载中</Text>
  return <Space><Text code>{shipment.shipment_no}</Text><Tag color={shipment.stage === 'SIGNED' ? 'green' : 'blue'}>{stageText[shipment.stage]}</Tag></Space>
}
function StationName({ id }: { id: string }) {
  const [name, setName] = useState(id)
  useEffect(() => { api.stations().then(rows => setName(rows.find(row => row.id === id)?.name ?? id)).catch(() => setName(id)) }, [id])
  return <>{name}</>
}

function TasksPage({ revision, mutate, clock, goSimulation }: Shared & { clock?: string; goSimulation: () => void }) {
  const actionRef = useRef<ActionType | undefined>(undefined)
  const [detail, setDetail] = useState<TaskDetail>()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [messageApi, holder] = message.useMessage()
  useEffect(() => { actionRef.current?.reload() }, [revision])
  const openTask = async (id: string) => { try { setDetail(await api.task(id)); setDrawerOpen(true) } catch (error) { messageApi.error(apiError(error)) } }
  const columns: ProColumns<TaskItem>[] = [
    { title: '任务号', dataIndex: 'task_no', copyable: true },
    { title: '线路', dataIndex: 'route_code', valueEnum: { AB: { text: 'A → B' }, BC: { text: 'B → C' } } },
    { title: '任务状态', dataIndex: 'status', valueEnum: Object.fromEntries(Object.entries(taskStatusText).map(([key, text]) => [key, { text }])) },
    { title: '预计到达', dataIndex: 'expected_arrival_at', valueType: 'dateTime', search: false },
    { title: '实际到达', dataIndex: 'arrived_at', valueType: 'dateTime', search: false },
    { title: '延误提示', dataIndex: 'delay_status', search: false, render: (_, row) => row.delay_status === 'OVERDUE' ? <Tag color="error">{delayText(row)}</Tag> : row.delay_status === 'LATE_ARRIVAL' ? <Tag color="warning">{delayText(row)}</Tag> : '—' },
    { title: '操作', valueType: 'option', render: (_, row) => <a onClick={() => void openTask(row.id)}>查看详情</a> },
  ]
  return <>{holder}<PageContainer title="运输任务" subTitle="固定 A → B 与 B → C 两段线路，创建任务并跟踪运输状态。" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>创建运输任务</Button>}>
    <ProTable<TaskItem> actionRef={actionRef} rowKey="id" columns={columns} search={{ labelWidth: 90 }} pagination={{ pageSize: 20 }} request={async params => {
      try { const result = await api.tasks({ page: params.current ?? 1, page_size: params.pageSize ?? 20, task_no: params.task_no as string, route_code: params.route_code as string, status: params.status as string }); return { data: result.items, success: true, total: result.total } }
      catch (error) { messageApi.error(apiError(error)); return { data: [], success: false, total: 0 } }
    }} />
    <ModalForm title="创建运输任务" open={createOpen} onOpenChange={setCreateOpen} initialValues={{ route_code: 'AB' }} modalProps={{ destroyOnClose: true }} onFinish={async (values: { route_code: RouteCode; expected_arrival_at: Dayjs; shipment_ids: string[] }) => {
      const body = { route_code: values.route_code, expected_arrival_at: values.expected_arrival_at.format('YYYY-MM-DDTHH:mm:ssZ'), shipment_ids: values.shipment_ids.map(Number) }
      const result = await mutate(`create-task:${JSON.stringify(body)}`, key => api.createTask(body, key), '运输任务已创建；运单仍在起点站')
      if (result) { setCreateOpen(false); actionRef.current?.reload(); await openTask(result.id) }
      return Boolean(result)
    }}>
      <ProFormSelect name="route_code" label="运输线路" options={[{ label: 'A → B', value: 'AB' }, { label: 'B → C', value: 'BC' }]} rules={[{ required: true }]} />
      <ProFormDateTimePicker name="expected_arrival_at" label="预计到达时间（北京时间）" rules={[{ required: true }]} fieldProps={{ showTime: true, disabledDate: date => Boolean(clock && date.isBefore(dayjs(clock), 'day')) }} />
      <ProFormSelect name="shipment_ids" label="待出站运单" mode="multiple" dependencies={['route_code']} rules={[{ required: true, message: '至少选择一张运单' }]} request={async params => {
        const result = await api.candidates((params.route_code as RouteCode) ?? 'AB', 1)
        return result.items.map((item: Candidate) => ({ label: `${item.shipment_no} · ${stageText[item.stage]}`, value: item.id }))
      }} fieldProps={{ showSearch: true, optionFilterProp: 'label', placeholder: '选择一张或多张运单', maxTagCount: 'responsive' }} />
      <Text type="secondary">预计到达须晚于当前演示时间 {formatTime(clock)}。任务创建后不可编辑。</Text>
    </ModalForm>
    <Drawer width={680} title={detail?.task_no} open={drawerOpen} onClose={() => setDrawerOpen(false)} extra={<Button onClick={goSimulation}>打开演示控制</Button>}>
      {detail && <><Descriptions bordered size="small" column={1}><Descriptions.Item label="运输线路">{detail.route_code === 'AB' ? 'A → B' : 'B → C'}</Descriptions.Item><Descriptions.Item label="任务状态">{taskStatusText[detail.status]}</Descriptions.Item><Descriptions.Item label="预计到达">{formatTime(detail.expected_arrival_at)}</Descriptions.Item><Descriptions.Item label="实际发车">{formatTime(detail.departed_at)}</Descriptions.Item><Descriptions.Item label="实际到达">{formatTime(detail.arrived_at)}</Descriptions.Item><Descriptions.Item label="延误提示">{detail.delay_status === 'OVERDUE' ? <Tag color="error">{delayText(detail)}</Tag> : detail.delay_status === 'LATE_ARRIVAL' ? <Tag color="warning">{delayText(detail)}</Tag> : '—'}</Descriptions.Item><Descriptions.Item label="关联运单">{detail.shipments.map(item => <Tag key={item.id}>{item.shipment_no} · {stageText[item.stage]}</Tag>)}</Descriptions.Item></Descriptions><Text type="secondary">演示时间：{formatTime(detail.simulation_time)}。页面操作将按该时钟记录。</Text></>}
    </Drawer>
  </PageContainer></>
}

function SimulationPage({ revision, busy, mutate, clock }: Shared & { clock?: string }) {
  const [shipments, setShipments] = useState<Shipment[]>([])
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [shipment, setShipment] = useState<ShipmentDetail>()
  const [task, setTask] = useState<TaskDetail>()
  const [messageApi, holder] = message.useMessage()
  const refreshLists = useCallback(async () => {
    try {
      const [shipmentPage, taskPage] = await Promise.all([api.shipments({ page: 1, page_size: 100 }), api.tasks({ page: 1, page_size: 100 })])
      setShipments(shipmentPage.items); setTasks(taskPage.items)
      if (shipment?.id) setShipment(await api.shipment(shipment.id))
      if (task?.id) setTask(await api.task(task.id))
    } catch (error) { messageApi.error(apiError(error)) }
  }, [messageApi, shipment?.id, task?.id])
  useEffect(() => { void refreshLists() }, [revision, refreshLists])
  const runShipment = async (event: string) => {
    if (!shipment) return
    const result = await mutate(`shipment-event:${shipment.id}:${event}`, key => api.shipmentEvent(shipment.id, event, key), `${actionText[event]}已完成`)
    if (result) setShipment(result)
  }
  const runTask = async (action: 'depart' | 'arrive') => {
    if (!task) return
    const result = await mutate(`task-action:${task.id}:${action}`, key => api.taskAction(task.id, action, key), action === 'depart' ? '任务已发车' : '任务已到达，全部关联运单已入站')
    if (result) setTask(await api.task(task.id))
  }
  return <>{holder}<PageContainer title="演示控制" subTitle="用模拟事件推进物流链路，所有业务时间由后端演示时钟决定。">
    <Card title={<Space><ClockCircleOutlined />演示时钟</Space>} extra={<Space><Button disabled={busy} onClick={() => mutate(`advance:${clock}:30`, key => api.advance(30, key), '演示时钟已推进 30 分钟')}>推进 30 分钟</Button><Button type="primary" disabled={busy} onClick={() => mutate(`advance:${clock}:120`, key => api.advance(120, key), '演示时钟已推进 2 小时')}>推进 2 小时</Button></Space>} style={{ marginBottom: 16 }}>
      <Title level={3} style={{ marginTop: 0 }}>{formatTime(clock)}</Title><Text type="secondary">推进时钟只检查超时，不自动发车或到达。</Text>
    </Card>
    <div className="simulation-grid"><Card title="运单事件" extra={<TruckOutlined />}><Form layout="vertical"><Form.Item label="选择运单"><Select allowClear showSearch optionFilterProp="label" value={shipment?.id} placeholder="请选择运单" options={shipments.map(item => ({ value: item.id, label: `${item.shipment_no} · ${stageText[item.stage]}` }))} onChange={async id => { try { setShipment(id ? await api.shipment(id) : undefined) } catch (error) { messageApi.error(apiError(error)) } }} /></Form.Item></Form>
      {shipment && <><Space wrap style={{ marginBottom: 16 }}><Tag color="blue">{stageText[shipment.stage]}</Tag><Text type="secondary">最后扫描：<StationName id={shipment.last_scanned_station_id ?? ''} /></Text></Space><Space wrap>{(['PICKUP', 'ENTER_A', 'START_DELIVERY', 'SIGN'] as const).map(event => { const rule = allowed(shipment.allowed_actions, event); return <Button key={event} disabled={busy || !rule?.enabled} title={rule?.reason ?? undefined} onClick={() => void runShipment(event)}>{actionText[event]}</Button> })}</Space><Alert style={{ marginTop: 16 }} type="info" showIcon message="按钮状态取自运单 allowed_actions，后端会再次校验。" /></>}
    </Card><Card title="运输任务事件" extra={<SwapOutlined />}><Form layout="vertical"><Form.Item label="选择运输任务"><Select allowClear showSearch optionFilterProp="label" value={task?.id} placeholder="请选择任务" options={tasks.map(item => ({ value: item.id, label: `${item.task_no} · ${taskStatusText[item.status]}` }))} onChange={async id => { try { setTask(id ? await api.task(id) : undefined) } catch (error) { messageApi.error(apiError(error)) } }} /></Form.Item></Form>
      {task && <><Space wrap style={{ marginBottom: 16 }}><Tag color="blue">{taskStatusText[task.status]}</Tag><Tag>{task.route_code === 'AB' ? 'A → B' : 'B → C'}</Tag>{task.delay_status === 'OVERDUE' && <Tag color="error">{delayText(task)}</Tag>}{task.delay_status === 'LATE_ARRIVAL' && <Tag color="warning">{delayText(task)}</Tag>}</Space><Space wrap><Button disabled={busy || !allowed(task.allowed_actions, 'DEPART')?.enabled} title={allowed(task.allowed_actions, 'DEPART')?.reason ?? undefined} onClick={() => void runTask('depart')}>任务发车</Button><Button type="primary" disabled={busy || !allowed(task.allowed_actions, 'ARRIVE')?.enabled} title={allowed(task.allowed_actions, 'ARRIVE')?.reason ?? undefined} onClick={() => void runTask('arrive')}>到达并全部入站</Button></Space><Alert style={{ marginTop: 16 }} type="info" showIcon message="任务到达与全部关联运单入站由后端一次完成。" /></>}
    </Card></div>
  </PageContainer></>
}
