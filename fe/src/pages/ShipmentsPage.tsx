import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Empty, Form, Input, message, Modal, Radio, Select, Space, Spin, Table, Tabs, Timeline, Typography } from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { ModalForm, PageContainer, ProFormDateTimePicker, ProFormSelect, ProFormText, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import dayjs from 'dayjs'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type DestinationChange, type PathHistoryPage, type PathOptions, type PathVersion, type Shipment, type ShipmentDetail, type ShipmentPathUpdate, type ShipmentTaskHistory, type TransportRoute } from '../api'
import { actionText, allowed, apiError, eventText, formatTime, stageText, StatusTag, taskStatusText, type Shared, useNetwork, StationName } from '../shared'

const { Text } = Typography
type ShipmentEvent = 'PICKUP' | 'ARRIVE' | 'START_DELIVERY' | 'SIGN'
type PathForm = { mode: 'plan' | 'routes'; plan_id?: string; route_ids?: string[]; reason: string }
const pathStatusText: Record<string, string> = { WAITING_FIRST_ARRIVAL: '等待首次入站', NEEDS_PLANNING: '待规划', READY: '可按下一段运输', RESERVED: '等待任务发车', IN_TRANSIT: '当前段运输中', COMPLETED: '路径已完成', BLOCKED: '未来路径受阻' }
const pathLegStateText: Record<string, string> = { PENDING: '待运输', RESERVED: '待发车', IN_TRANSIT: '运输中', ARRIVED: '已到达' }

function ShipmentsPage({ revision }: Pick<Shared, 'revision'>) {
  const actionRef = useRef<ActionType | undefined>(undefined)
  const [loadError, setLoadError] = useState<string>()
  useEffect(() => { actionRef.current?.reload() }, [revision])
  const columns: ProColumns<Shipment>[] = [
    { title: '运单号', dataIndex: 'shipment_no', copyable: true, width: 170 },
    { title: '阶段', dataIndex: 'stage', valueEnum: Object.fromEntries(Object.entries(stageText).map(([key, text]) => [key, { text }])) },
    { title: '当前站点', dataIndex: 'last_scanned_station_id', search: false, render: (_, row) => row.last_scanned_station_id ? <StationName id={row.last_scanned_station_id} /> : '—' },
    { title: '目的站', dataIndex: 'destination_station_id', search: false, render: (_, row) => <StationName id={row.destination_station_id} /> },
    { title: '最近更新', dataIndex: 'updated_at', valueType: 'dateTime', search: false },
    { title: '操作', valueType: 'option', render: (_, row) => <Link to={'/shipments/' + row.id}>查看运单</Link> },
  ]
  return <PageContainer title="运单" subTitle="处理揽收、站点入站、运输安排和末端派送。">
    {loadError && <Alert type="error" showIcon message="运单列表加载失败" description={loadError} action={<Button size="small" onClick={() => actionRef.current?.reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <ProTable<Shipment> actionRef={actionRef} rowKey="id" columns={columns} search={{ labelWidth: 90 }} pagination={{ pageSize: 20, showSizeChanger: true }} request={async params => {
      try { const result = await api.shipments({ page: params.current ?? 1, page_size: params.pageSize ?? 20, shipment_no: params.shipment_no as string, stage: params.stage as string }); setLoadError(undefined); return { data: result.items, total: result.total, success: true } }
      catch (error) { const reason = apiError(error); setLoadError(reason); return { data: [], total: 0, success: false } }
    }} />
  </PageContainer>
}

function ShipmentDetailPage({ revision, busy, mutate }: Shared & { clock?: string }) {
  const { shipmentId = '' } = useParams()
  const navigate = useNavigate()
  const network = useNetwork(revision)
  const [detail, setDetail] = useState<ShipmentDetail>()
  const [history, setHistory] = useState<ShipmentTaskHistory[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyPage, setHistoryPage] = useState(1)
  const [destinationChanges, setDestinationChanges] = useState<DestinationChange[]>([])
  const [destinationChangesTotal, setDestinationChangesTotal] = useState(0)
  const [destinationChangesPage, setDestinationChangesPage] = useState(1)
  const [pathHistory, setPathHistory] = useState<PathVersion[]>([])
  const [pathHistoryTotal, setPathHistoryTotal] = useState(0)
  const [pathHistoryPage, setPathHistoryPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string>()
  const [historyError, setHistoryError] = useState<string>()
  const [destinationChangesError, setDestinationChangesError] = useState<string>()
  const [pathHistoryError, setPathHistoryError] = useState<string>()
  const [retry, setRetry] = useState(0)
  const [firstStation, setFirstStation] = useState<string>()
  const [eventToConfirm, setEventToConfirm] = useState<ShipmentEvent>()
  const [eventSaving, setEventSaving] = useState(false)
  const [addressOpen, setAddressOpen] = useState(false)
  const [taskOpen, setTaskOpen] = useState(false)
  const [destinationModalStep, setDestinationModalStep] = useState<'edit' | 'confirm'>()
  const [destinationDraft, setDestinationDraft] = useState<{ destination_station_id: string; reason: string }>()
  const [destinationForm] = Form.useForm<{ destination_station_id: string; reason: string }>()
  const [pathOptions, setPathOptions] = useState<PathOptions>()
  const [pathModalStep, setPathModalStep] = useState<'edit' | 'confirm'>()
  const [pathDraft, setPathDraft] = useState<PathForm>()
  const [pathForm] = Form.useForm<PathForm>()
  const pathMode = Form.useWatch('mode', pathForm)
  const selectedPlanId = Form.useWatch('plan_id', pathForm)
  const selectedPathRouteIds = Form.useWatch('route_ids', pathForm) ?? []
  const [messageApi, holder] = message.useMessage()

  useEffect(() => {
    let active = true
    setLoading(true); setLoadError(undefined)
    Promise.all([api.shipment(shipmentId), api.shipmentTasks(shipmentId, historyPage).catch(error => {
      if (active) setHistoryError(apiError(error))
      return undefined
    }), api.shipmentDestinationChanges(shipmentId, destinationChangesPage).catch(error => {
      if (active) setDestinationChangesError(apiError(error))
      return undefined
    }), api.shipmentPathHistory(shipmentId, pathHistoryPage).catch(error => {
      if (active) setPathHistoryError(apiError(error))
      return undefined
    })]).then(([shipment, tasks, changes]) => {
      if (active) {
        setDetail(shipment)
        if (tasks) { setHistory(tasks.items); setHistoryTotal(tasks.total); setHistoryError(undefined) }
        if (changes) { setDestinationChanges(changes.items); setDestinationChangesTotal(changes.total); setDestinationChangesError(undefined) }
      }
    }).catch(error => { if (active) setLoadError(apiError(error)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [shipmentId, revision, retry, historyPage, destinationChangesPage, pathHistoryPage])

  const runEvent = (event: ShipmentEvent) => {
    if (!detail) return
    if (event === 'ARRIVE' && !firstStation) { messageApi.error('请选择包裹实际入站的站点'); return }
    setEventToConfirm(event)
  }
  const confirmEvent = async () => {
    if (!detail || !eventToConfirm) return
    const event = eventToConfirm
    const label = actionText[event]
    setEventSaving(true)
    const result = await mutate('shipment-event:' + detail.id + ':' + event + ':' + (event === 'ARRIVE' ? firstStation : ''), key => api.shipmentEvent(detail.id, event, event === 'ARRIVE' ? firstStation : undefined, key), label + '已完成')
    setEventSaving(false)
    if (result) setEventToConfirm(undefined)
  }
  const openTask = async (values: { route_code: string; expected_arrival_at: string }) => {
    if (!detail) return false
    const body = { route_code: values.route_code, expected_arrival_at: values.expected_arrival_at.replace(' ', 'T') + '+08:00', shipment_ids: [Number(detail.id)] }
    const result = await mutate('create-task:' + JSON.stringify(body), key => api.createTask(body, key), '运输任务已创建')
    if (result) { setTaskOpen(false); navigate('/tasks/' + result.id) }
    return Boolean(result)
  }
  const actionEnabled = (name: string) => allowed(detail?.allowed_actions ?? [], name)?.enabled === true
  const destinationAction = allowed(detail?.allowed_actions ?? [], 'UPDATE_DESTINATION')
  const deliveryStations = network.stations.filter(station => station.enabled && station.allows_delivery && station.id !== detail?.destination_station_id)
  const closeDestinationModal = () => {
    setDestinationModalStep(undefined)
    setDestinationDraft(undefined)
    destinationForm.resetFields()
  }
  const confirmDestinationCorrection = async () => {
    if (!detail || !destinationDraft) return
    const body = {
      expected_destination_station_id: Number(detail.destination_station_id),
      destination_station_id: Number(destinationDraft.destination_station_id),
      reason: destinationDraft.reason.trim(),
    }
    const result = await mutate(
      'shipment-destination:' + detail.id + ':' + JSON.stringify(body),
      key => api.updateShipmentDestination(detail.id, body, key),
      '运单目的站已更正',
    )
    if (result) {
      setDetail(result)
      closeDestinationModal()
      setDestinationChangesPage(1)
      setRetry(value => value + 1)
    } else {
      // A 409 may mean the destination or shipment state changed while the dialog was open.
      // Refresh the current facts while keeping the user's form values for another confirmation.
      void api.shipment(detail.id).then(setDetail).catch(error => setLoadError(apiError(error)))
      setRetry(value => value + 1)
    }
  }

  return <>{holder}<PageContainer title={detail?.shipment_no ?? '运单详情'} subTitle="当前站点、运输区间、履约操作和关联任务历史。" extra={<Space wrap><Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/shipments')}>返回运单列表</Button></Space>}>
    {loadError && <Alert type="error" showIcon message="运单详情加载失败" description={loadError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} style={{ marginBottom: 16 }} />}
    {loading && !detail ? <Spin /> : detail && <>
      <div style={{ marginBottom: 16 }}><Space wrap><StatusTag tone={detail.stage === 'SIGNED' ? 'success' : 'info'}>{stageText[detail.stage]}</StatusTag>{detail.last_scanned_station_id && <Text>最近扫描：<StationName id={detail.last_scanned_station_id} /></Text>}{detail.active_transport_task && <Button type="link" onClick={() => navigate('/tasks/' + detail.active_transport_task!.id)}>查看当前任务 · <StationName id={detail.active_transport_task.origin_station_id} /> → <StationName id={detail.active_transport_task.destination_station_id} /></Button>}</Space></div>
      {detail.stage === 'IN_TRANSIT' && detail.active_transport_task && <Alert style={{ marginBottom: 16 }} type="info" showIcon message={<>运输区间：<StationName id={detail.active_transport_task.origin_station_id} /> → <StationName id={detail.active_transport_task.destination_station_id} /></>} description={<>最近扫描在 <StationName id={detail.last_scanned_station_id ?? detail.active_transport_task.origin_station_id} />；货物到达并入站后会显示当前所在站。</>} />}
      {detail.stage === 'AT_STATION' && detail.last_scanned_station_id && <Alert style={{ marginBottom: 16 }} type="info" showIcon message={<>当前所在站：<Text strong><StationName id={detail.last_scanned_station_id} /></Text></>} />}
      <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
        <Descriptions.Item label="运单阶段">{stageText[detail.stage]}</Descriptions.Item><Descriptions.Item label="订单编号">{detail.order_id}</Descriptions.Item>
        <Descriptions.Item label="运单目的站"><StationName id={detail.destination_station_id} /></Descriptions.Item><Descriptions.Item label="当前配送地址"><span className="jp-wrap-anywhere">{detail.recipient_address}</span></Descriptions.Item>
        <Descriptions.Item label="发件地址"><span className="jp-wrap-anywhere">{detail.sender_address}</span></Descriptions.Item><Descriptions.Item label="创建时间">{formatTime(detail.created_at)}</Descriptions.Item>
      </Descriptions>
      <div style={{ margin: '20px 0 28px' }}>
        <Text strong style={{ display: 'block', marginBottom: 12 }}>下一步操作</Text>
        <Space wrap>
          {detail.stage === 'PENDING_PICKUP' && <Button type="primary" disabled={busy || !actionEnabled('PICKUP')} onClick={() => runEvent('PICKUP')}>确认揽收</Button>}
          {detail.stage === 'PENDING_PICKUP' && <Button disabled={busy || !actionEnabled('UPDATE_ADDRESS')} onClick={() => setAddressOpen(true)}>编辑履约地址</Button>}
          <Button disabled={busy || !destinationAction?.enabled} onClick={() => { destinationForm.resetFields(); setDestinationModalStep('edit') }}>更正目的站</Button>
        </Space>
        {destinationAction && !destinationAction.enabled && <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>{destinationAction.reason ?? '当前运单暂不可更正目的站。'}</Text>}
        {!destinationAction && <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>后端未返回目的站更正资格，请刷新运单后重试。</Text>}
        {detail.stage === 'PICKED_UP' && <><Form.Item label="首次入站站点" style={{ marginBottom: 12, maxWidth: 360 }}><Select value={firstStation} onChange={setFirstStation} options={network.stations.filter(s => s.enabled && s.allows_first_arrival).map(s => ({ value: s.id, label: s.code + ' · ' + s.name }))} placeholder="选择包裹实际进入的站点" /></Form.Item><Button type="primary" disabled={busy || !firstStation || !actionEnabled('ARRIVE')} onClick={() => runEvent('ARRIVE')}>确认入站</Button></>}
        {detail.stage === 'AT_STATION' && actionEnabled('START_DELIVERY') && <Space wrap><Button type="primary" disabled={busy} onClick={() => runEvent('START_DELIVERY')}>开始派送</Button><Text type="secondary">运单已到达目的站，可以交给末端配送。</Text></Space>}
        {detail.stage === 'AT_STATION' && !actionEnabled('START_DELIVERY') && detail.active_transport_task && <Alert type="info" showIcon message="运单正在等待或进行站间运输" description={<Space wrap><span><StationName id={detail.active_transport_task.origin_station_id} /> → <StationName id={detail.active_transport_task.destination_station_id} /></span><Button type="link" onClick={() => navigate('/tasks/' + detail.active_transport_task!.id)}>查看运输任务</Button></Space>} />}
        {detail.stage === 'AT_STATION' && !actionEnabled('START_DELIVERY') && !detail.active_transport_task && actionEnabled('CREATE_TRANSPORT_TASK') && <Alert type="info" showIcon message="运单还没有到达目的站" description={<Space wrap><span>当前所在站：<StationName id={detail.last_scanned_station_id ?? ''} />；目的站：<StationName id={detail.destination_station_id} />。先创建一段运输任务，到达目的站后才能开始派送。</span><Button type="primary" onClick={() => setTaskOpen(true)}>选择线路并创建运输任务</Button></Space>} />}
        {detail.stage === 'AT_STATION' && !actionEnabled('START_DELIVERY') && !detail.active_transport_task && !actionEnabled('CREATE_TRANSPORT_TASK') && <Text type="secondary">{allowed(detail.allowed_actions, 'CREATE_TRANSPORT_TASK')?.reason ?? '当前运单暂不可创建运输任务。'}</Text>}
        {detail.stage === 'IN_TRANSIT' && detail.active_transport_task && <Alert type="info" showIcon message="运单在途，到达并入站后再继续操作。" description={<Button type="link" onClick={() => navigate('/tasks/' + detail.active_transport_task!.id)}>查看运输任务</Button>} />}
        {detail.stage === 'OUT_FOR_DELIVERY' && <Button type="primary" disabled={busy || !actionEnabled('SIGN')} onClick={() => runEvent('SIGN')}>确认买家签收</Button>}
        {detail.stage === 'SIGNED' && <Alert type="success" showIcon message="运单已签收，物流流程完成。" />}
      </div>
      <Tabs items={[
        { key: 'tracking', label: '物流轨迹', children: detail.tracking_events.length ? <Timeline items={detail.tracking_events.map(event => ({ children: <><Text strong>{eventText[event.event_type] ?? event.event_type}</Text><br /><Text type="secondary">{formatTime(event.occurred_at)}{event.station_id ? ' · ' : ''}</Text>{event.station_id && <StationName id={event.station_id} />}</> }))} /> : <Empty description="暂无物流轨迹" /> },
        { key: 'destinations', label: '目的站更正记录', children: destinationChangesError ? <Alert type="warning" showIcon message="更正记录暂不可用" description={destinationChangesError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} /> : <Table<DestinationChange> rowKey="id" dataSource={destinationChanges} pagination={{ current: destinationChangesPage, pageSize: 20, total: destinationChangesTotal, onChange: setDestinationChangesPage }} locale={{ emptyText: <Empty description="暂无目的站更正记录" /> }} columns={[
          { title: '原目的站', dataIndex: 'previous_destination_station_id', render: value => <StationName id={value} /> },
          { title: '新目的站', dataIndex: 'destination_station_id', render: value => <StationName id={value} /> },
          { title: '更正原因', dataIndex: 'reason' },
          { title: '操作时间', dataIndex: 'occurred_at', render: value => formatTime(value) },
        ]} /> },
        { key: 'tasks', label: '运输任务历史', children: historyError ? <Alert type="warning" showIcon message="任务历史暂不可用" description={historyError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} /> : history.length ? <Table<ShipmentTaskHistory> rowKey="id" pagination={{ current: historyPage, pageSize: 20, total: historyTotal, onChange: setHistoryPage }} dataSource={history} columns={[
          { title: '任务号', dataIndex: 'task_no', render: (value, row) => <Link to={'/tasks/' + row.id}>{value}</Link> },
          { title: '线路', render: (_, row) => <>{row.route_code} · <StationName id={row.origin_station_id} /> → <StationName id={row.destination_station_id} /></> },
          { title: '状态', dataIndex: 'status', render: value => taskStatusText[value] }, { title: '释放占用时间', dataIndex: 'released_at', render: value => formatTime(value) },
          { title: '取消时间', dataIndex: 'cancelled_at', render: value => formatTime(value) },
          { title: '取消原因', dataIndex: 'cancel_reason', render: value => value || '—' },
        ]} /> : <Empty description="该运单还没有关联运输任务" /> },
      ]} />
      <ModalForm<{ sender_address: string; recipient_address: string }> title="编辑运单履约地址" open={addressOpen} onOpenChange={setAddressOpen} initialValues={{ sender_address: detail.sender_address, recipient_address: detail.recipient_address }} modalProps={{ destroyOnHidden: true }} onFinish={async values => {
        const result = await mutate('address:' + detail.id + ':' + JSON.stringify(values), key => api.updateAddress(detail.id, values, key), '运单地址已更新')
        if (result) { setDetail(result); setAddressOpen(false) }
        return Boolean(result)
      }}><ProFormText name="sender_address" label="发件地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /><ProFormText name="recipient_address" label="收件地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /></ModalForm>
      <ModalForm<{ route_code: string; expected_arrival_at: string }> title="创建运输任务" open={taskOpen} onOpenChange={setTaskOpen} modalProps={{ destroyOnHidden: true }} onFinish={openTask}>
        <ProFormSelect name="route_code" label="出站线路" options={network.routes.filter(r => r.enabled && r.origin.enabled && r.destination.enabled && r.origin.id === detail.last_scanned_station_id).map(r => ({ value: r.code, label: r.code + ' · ' + r.origin.code + ' → ' + r.destination.code }))} rules={[{ required: true }]} fieldProps={{ placeholder: '选择从当前站出发的可用线路', notFoundContent: '当前站暂无可用出站线路' }} />
        <ProFormDateTimePicker name="expected_arrival_at" label="预计到达时间（北京时间）" rules={[{ required: true }]} fieldProps={{ showTime: true, disabledDate: date => date.isBefore(dayjs(), 'day') }} />
        <Text type="secondary">目的站可为中转站。提交时后端会再次验证线路和运单是否仍可用。</Text>
      </ModalForm>
      <Modal
        title={destinationModalStep === 'confirm' ? '确认更正目的站' : '更正目的站'}
        open={Boolean(destinationModalStep)}
        onCancel={() => destinationModalStep === 'confirm' ? setDestinationModalStep('edit') : closeDestinationModal()}
        onOk={() => {
          if (destinationModalStep === 'confirm') void confirmDestinationCorrection()
          else void destinationForm.validateFields().then(values => {
            setDestinationDraft(values)
            setDestinationModalStep('confirm')
          }).catch(() => undefined)
        }}
        okText={destinationModalStep === 'confirm' ? '确认更正' : '检查更正内容'}
        cancelText={destinationModalStep === 'confirm' ? '返回修改' : '取消'}
        confirmLoading={busy}
        okButtonProps={{ disabled: busy || !destinationAction?.enabled || network.stations.length === 0 }}
      >
        {destinationModalStep === 'edit' ? <>
          {network.error && <Alert type="error" showIcon message="站点列表加载失败" description={network.error} style={{ marginBottom: 16 }} />}
          <Alert type="info" showIcon message="更正目的站不会改变货物位置或收件地址。请确认新站能够负责该地址的配送。" style={{ marginBottom: 16 }} />
          <Form form={destinationForm} layout="vertical">
            <Form.Item label="当前目的站">
              <Text><StationName id={detail.destination_station_id} /></Text>
            </Form.Item>
            <Form.Item name="destination_station_id" label="新的目的站" rules={[{ required: true, message: '请选择新的目的站' }]}>
              <Select showSearch optionFilterProp="label" options={deliveryStations.map(station => ({ value: station.id, label: `${station.code} · ${station.name}` }))} placeholder="选择启用且允许派送的站点" notFoundContent="没有可选择的其他派送站点" />
            </Form.Item>
            <Form.Item name="reason" label="更正原因" rules={[{ required: true, whitespace: true, message: '请填写更正原因' }, { max: 500, message: '更正原因不能超过 500 个字符' }]}>
              <Input.TextArea maxLength={500} showCount rows={3} placeholder="说明为什么需要更正目的站" />
            </Form.Item>
          </Form>
        </> : destinationDraft && <>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="原目的站"><StationName id={detail.destination_station_id} /></Descriptions.Item>
            <Descriptions.Item label="更正为"><StationName id={destinationDraft.destination_station_id} /></Descriptions.Item>
            <Descriptions.Item label="更正原因">{destinationDraft.reason.trim()}</Descriptions.Item>
          </Descriptions>
          <Alert type="warning" showIcon message="这只更改物流安排，不会移动货物或修改订单、收件地址。" style={{ marginTop: 16 }} />
        </>}
      </Modal>
      <Modal
        title={'确认' + (eventToConfirm ? actionText[eventToConfirm] : '操作')}
        open={Boolean(eventToConfirm)}
        confirmLoading={eventSaving}
        okText="确认执行"
        cancelText="返回检查"
        onOk={() => void confirmEvent()}
        onCancel={() => setEventToConfirm(undefined)}
        okButtonProps={{ disabled: busy }}
      >
        <p>{detail.shipment_no} · {stageText[detail.stage]}</p>
        {eventToConfirm === 'ARRIVE' && <p>入站站点：<StationName id={firstStation ?? ''} /></p>}
        {eventToConfirm === 'START_DELIVERY' && <p>派送起点：<StationName id={detail.last_scanned_station_id ?? ''} />；目的站：<StationName id={detail.destination_station_id} />。</p>}
        {eventToConfirm === 'SIGN' && <p>确认包裹已交付给收件人。</p>}
      </Modal>
    </>}
  </PageContainer></>
}

export { ShipmentDetailPage }
export default ShipmentsPage
