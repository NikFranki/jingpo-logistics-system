import { fuzzySelectFilter } from '../fuzzySearch'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Empty, Form, Input, message, Modal, Select, Space, Spin, Table, Tabs, Tag, Timeline, Tooltip, Typography } from 'antd'
import { QuestionCircleOutlined } from '@ant-design/icons'
import { ModalForm, PageContainer, ProFormText, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, type DestinationChange, type ScheduleHistoryItem, type Shipment, type ShipmentDetail, type ShipmentTaskHistory } from '../api'
import { SchedulePlanner } from './SchedulePlanner'
import { actionText, allowed, apiError, eventText, formatTime, stageText, StatusTag, taskStatusText, type Shared, useNetwork, StationName } from '../shared'

const { Text } = Typography
type ShipmentEvent = 'PICKUP' | 'ARRIVE' | 'START_DELIVERY' | 'SIGN'
const associationStateText: Record<string, string> = { PLANNED: '未来待执行', ACTIVE: '当前执行段', RELEASED: '已解除' }

function fullAddress(address: ShipmentDetail, side: 'sender' | 'recipient') {
  const region = [address[`${side}_province_name`], address[`${side}_city_name`], address[`${side}_district_name`]].filter(Boolean).join('')
  const detail = address[`${side}_address`]
  return `${region}${region && detail ? ' ' : ''}${detail}`
}

function ShipmentsPage({ revision }: Pick<Shared, 'revision'>) {
  const actionRef = useRef<ActionType | undefined>(undefined)
  const [loadError, setLoadError] = useState<string>()
  useEffect(() => { actionRef.current?.reload() }, [revision])
  const columns: ProColumns<Shipment>[] = [
    { title: '运单号', dataIndex: 'shipment_no', copyable: true, width: 170 },
    { title: '阶段', dataIndex: 'stage', fieldProps: { showSearch: true, filterOption: fuzzySelectFilter }, valueEnum: Object.fromEntries(Object.entries(stageText).map(([key, text]) => [key, { text }])) },
    { title: '当前站点', dataIndex: 'last_scanned_station_id', search: false, render: (_, row) => row.last_scanned_station_id ? <StationName id={row.last_scanned_station_id} /> : '—' },
    { title: '目的站', dataIndex: 'destination_station_id', search: false, render: (_, row) => <StationName id={row.destination_station_id} /> },
    { title: '最近更新', dataIndex: 'updated_at', valueType: 'dateTime', search: false },
    { title: '操作', valueType: 'option', render: (_, row) => <Link to={'/shipments/' + row.id}>查看运单</Link> },
  ]
  return <PageContainer title="运单" subTitle="处理揽收、站点入站、运输安排和末端派送。">
    {loadError && <Alert type="error" showIcon message="运单列表加载失败" description={loadError} action={<Button size="small" onClick={() => actionRef.current?.reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <ProTable<Shipment> actionRef={actionRef} rowKey="id" columns={columns} search={{ labelWidth: 90 }} pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }} request={async params => {
      try { const result = await api.shipments({ page: params.current ?? 1, page_size: params.pageSize ?? 20, shipment_no: params.shipment_no as string, stage: params.stage as string }); setLoadError(undefined); return { data: result.items, total: result.total, success: true } }
      catch (error) { const reason = apiError(error); setLoadError(reason); return { data: [], total: 0, success: false } }
    }} />
  </PageContainer>
}

function ShipmentDetailPage({ revision, busy, mutate }: Shared) {
  const { shipmentId = '' } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const network = useNetwork(revision)
  const [detail, setDetail] = useState<ShipmentDetail>()
  const [history, setHistory] = useState<ShipmentTaskHistory[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyPage, setHistoryPage] = useState(1)
  const [destinationChanges, setDestinationChanges] = useState<DestinationChange[]>([])
  const [destinationChangesTotal, setDestinationChangesTotal] = useState(0)
  const [destinationChangesPage, setDestinationChangesPage] = useState(1)
  const [scheduleHistory, setScheduleHistory] = useState<ScheduleHistoryItem[]>([])
  const [scheduleHistoryTotal, setScheduleHistoryTotal] = useState(0)
  const [scheduleHistoryPage, setScheduleHistoryPage] = useState(1)
  const [scheduleHistoryError, setScheduleHistoryError] = useState<string>()
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string>()
  const [historyError, setHistoryError] = useState<string>()
  const [destinationChangesError, setDestinationChangesError] = useState<string>()
  const [retry, setRetry] = useState(0)
  const [firstStation, setFirstStation] = useState<string>()
  const [eventToConfirm, setEventToConfirm] = useState<ShipmentEvent>()
  const [eventSaving, setEventSaving] = useState(false)
  const [addressOpen, setAddressOpen] = useState(false)
  const [openScheduleOnLoad, setOpenScheduleOnLoad] = useState(false)
  const [destinationModalStep, setDestinationModalStep] = useState<'edit' | 'confirm'>()
  const [destinationDraft, setDestinationDraft] = useState<{ destination_station_id: string; reason: string }>()
  const [destinationForm] = Form.useForm<{ destination_station_id: string; reason: string }>()
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
    })]).then(([shipment, tasks, changes]) => {
      if (active) {
        setDetail(shipment)
        if (tasks) { setHistory(tasks.items); setHistoryTotal(tasks.total); setHistoryError(undefined) }
        if (changes) { setDestinationChanges(changes.items); setDestinationChangesTotal(changes.total); setDestinationChangesError(undefined) }
      }
    }).catch(error => { if (active) setLoadError(apiError(error)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [shipmentId, revision, retry, historyPage, destinationChangesPage])

  useEffect(() => {
    if (searchParams.get('schedule') !== '1' || !detail) return
    setOpenScheduleOnLoad(true)
    const next = new URLSearchParams(searchParams)
    next.delete('schedule')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams, detail])

  useEffect(() => {
    let active = true
    api.shipmentScheduleHistory(shipmentId, scheduleHistoryPage).then(result => {
      if (active) { setScheduleHistory(result.items); setScheduleHistoryTotal(result.total); setScheduleHistoryError(undefined) }
    }).catch(error => { if (active) setScheduleHistoryError(apiError(error)) })
    return () => { active = false }
  }, [shipmentId, revision, retry, scheduleHistoryPage])

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
  return <>{holder}<PageContainer title={detail?.shipment_no ?? '运单详情'} subTitle="当前站点、运输区间、履约操作和关联任务历史。" breadcrumb={{ items: [{ title: <Link to="/shipments">运单列表</Link> }, { title: detail?.shipment_no ?? '运单详情' }] }}>
    {loadError && <Alert type="error" showIcon message="运单详情加载失败" description={loadError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} style={{ marginBottom: 16 }} />}
    {loading && !detail ? <Spin /> : detail && <>
      <div style={{ marginBottom: 16 }}><Space wrap><StatusTag tone={detail.stage === 'SIGNED' ? 'success' : 'info'}>{stageText[detail.stage]}</StatusTag>{detail.last_scanned_station_id && <Text>最近扫描：<StationName id={detail.last_scanned_station_id} /></Text>}{detail.active_transport_task && <Button type="link" onClick={() => navigate('/tasks/' + detail.active_transport_task!.id)}>查看当前任务 · <StationName id={detail.active_transport_task.origin_station_id} /> → <StationName id={detail.active_transport_task.destination_station_id} /></Button>}</Space></div>
      {detail.stage === 'IN_TRANSIT' && detail.active_transport_task && <Alert style={{ marginBottom: 16 }} type="info" showIcon message={<>运输区间：<StationName id={detail.active_transport_task.origin_station_id} /> → <StationName id={detail.active_transport_task.destination_station_id} /></>} description={<>最近扫描在 <StationName id={detail.last_scanned_station_id ?? detail.active_transport_task.origin_station_id} />；货物到达并入站后会显示当前所在站。</>} />}
      {detail.stage === 'AT_STATION' && detail.last_scanned_station_id && <Alert style={{ marginBottom: 16 }} type="info" showIcon message={<>当前所在站：<Text strong><StationName id={detail.last_scanned_station_id} /></Text></>} />}
      <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
        <Descriptions.Item label="运单阶段">{stageText[detail.stage]}</Descriptions.Item><Descriptions.Item label="订单编号">{detail.order_id}</Descriptions.Item>
        <Descriptions.Item label="运单目的站"><StationName id={detail.destination_station_id} /></Descriptions.Item><Descriptions.Item label="出发地址"><span className="jp-wrap-anywhere">{fullAddress(detail, 'sender')}</span></Descriptions.Item>
        <Descriptions.Item label="收件地址"><span className="jp-wrap-anywhere">{fullAddress(detail, 'recipient')}</span></Descriptions.Item><Descriptions.Item label="创建时间">{formatTime(detail.created_at)}</Descriptions.Item>
      </Descriptions>
      <section style={{ marginTop: 24 }}>
        <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }} align="center" wrap>
          <Text strong>全程运输计划</Text>
        </Space>
        {detail.schedule ? <>
          {detail.schedule.configuration_risks.map((risk, index) => <Alert key={index} type="warning" showIcon message={risk.message ?? '计划中的线路或站点配置已停用'} style={{ marginBottom: 8 }} />)}
          {detail.schedule.legs.length ? <Table rowKey="schedule_leg_id" size="small" pagination={false} dataSource={detail.schedule.legs} scroll={{ x: 1050 }} columns={[
            { title: '段', width: 56, render: (_, leg) => leg.position + 1 },
            { title: '线路', width: 180, render: (_, leg) => <>{leg.route_code}<br /><Text type="secondary"><StationName id={leg.origin_station_id} /> → <StationName id={leg.destination_station_id} /></Text></> },
            { title: '执行状态', width: 130, render: (_, leg) => <>{leg.task_status ? taskStatusText[leg.task_status] : '尚无任务'}<br /><Text type="secondary">{associationStateText[leg.association_state ?? ''] ?? ''}</Text></> },
            { title: '确认计划', width: 170, render: (_, leg) => <>出发 {formatTime(leg.planned_departure_at)}<br />到达 {formatTime(leg.planned_arrival_at)}</> },
            { title: '最新预计', width: 190, render: (_, leg) => <>出发 {formatTime(leg.forecast_departure_at)}<br />到达 {formatTime(leg.forecast_arrival_at)}{leg.forecast_stale && <><br /><Tag color="orange">预测已过期</Tag></>}</> },
            { title: '实际时间', width: 170, render: (_, leg) => <>发车 {formatTime(leg.actual_departure_at)}<br />到达 {formatTime(leg.actual_arrival_at)}</> },
            { title: '任务', width: 150, render: (_, leg) => leg.task_id ? <Link to={`/tasks/${leg.task_id}`}>{leg.task_no ?? `任务 ${leg.task_id}`}</Link> : '—' },
            { title: '等待条件', width: 200, render: (_, leg) => <>{leg.ready_at ? `就绪时间 ${formatTime(leg.ready_at)}` : ''}{leg.waiting_members.length > 0 && <Text type="secondary" style={{ display: 'block' }}>等待运单：{leg.waiting_members.map(member => member.shipment_no ?? member.shipment_id).join('、')}</Text>}</> },
          ]} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未确认运输计划；先预览路线和逐站时间。" />}
        </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未确认运输计划；请选择线路和班次。" />}
        {detail.schedule?.status !== 'COMPLETED' && !['OUT_FOR_DELIVERY', 'SIGNED'].includes(detail.stage) && <SchedulePlanner shipment={detail} routes={network.routes} enabled={!busy} busy={busy} mutate={mutate} autoOpen={openScheduleOnLoad} onSaved={() => { setOpenScheduleOnLoad(false); setScheduleHistoryPage(1); setRetry(value => value + 1) }} />}
      </section>
      <div style={{ margin: '20px 0 28px' }}>
        <Text strong style={{ display: 'block', marginBottom: 12 }}>下一步操作</Text>
        <Space wrap>
          {detail.stage === 'PENDING_PICKUP' && <Button type="primary" disabled={busy || !actionEnabled('PICKUP')} onClick={() => runEvent('PICKUP')}>确认揽收</Button>}
          {detail.stage === 'PENDING_PICKUP' && <Button disabled={busy || !actionEnabled('UPDATE_ADDRESS')} onClick={() => setAddressOpen(true)}>编辑履约地址</Button>}
        </Space>
        <Space direction="vertical" size={12} style={{ display: 'flex', alignItems: 'flex-start', marginTop: 12 }}>
          <Button disabled={busy || !destinationAction?.enabled} onClick={() => { destinationForm.resetFields(); setDestinationModalStep('edit') }}>
            更正目的站<Tooltip title="运输途中更正时，当前段仍到原定站点；系统会以该段终点自动安排后续线路和班次，也可以手动调整。"><QuestionCircleOutlined aria-label="更正目的站说明" tabIndex={0} style={{ marginInlineStart: 6, color: '#8c8c8c', cursor: 'help' }} /></Tooltip>
          </Button>
          {detail.stage === 'AT_STATION' && actionEnabled('START_DELIVERY') && <Space wrap><Button type="primary" disabled={busy} onClick={() => runEvent('START_DELIVERY')}>开始派送</Button><Text type="secondary">运单已到达目的站，可以交给末端配送。</Text></Space>}
        </Space>
        {destinationAction && !destinationAction.enabled && <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>{destinationAction.reason ?? '当前运单暂不可更正目的站。'}</Text>}
        {!destinationAction && <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>后端未返回目的站更正资格，请刷新运单后重试。</Text>}
        {detail.stage === 'PICKED_UP' && <><Form.Item label="首次入站站点" style={{ marginBottom: 12, maxWidth: 360 }}><Select showSearch filterOption={fuzzySelectFilter} value={firstStation} onChange={setFirstStation} options={network.stations.filter(s => s.enabled && s.allows_first_arrival).map(s => ({ value: s.id, label: s.code + ' · ' + s.name }))} placeholder="选择包裹实际进入的站点" /></Form.Item><Button type="primary" disabled={busy || !firstStation || !actionEnabled('ARRIVE')} onClick={() => runEvent('ARRIVE')}>确认入站</Button></>}
        {detail.stage === 'AT_STATION' && !actionEnabled('START_DELIVERY') && detail.active_transport_task && <Alert type="info" showIcon message="运单正在等待或进行站间运输" description={<Space wrap><span><StationName id={detail.active_transport_task.origin_station_id} /> → <StationName id={detail.active_transport_task.destination_station_id} /></span><Button type="link" onClick={() => navigate('/tasks/' + detail.active_transport_task!.id)}>查看运输任务</Button></Space>} />}
        {detail.stage === 'AT_STATION' && !actionEnabled('START_DELIVERY') && !detail.active_transport_task && <Text type="secondary">{allowed(detail.allowed_actions, 'CREATE_TRANSPORT_TASK')?.reason ?? '下一段运输任务由已确认的全程计划自动生成。'}</Text>}
        {detail.stage === 'IN_TRANSIT' && detail.active_transport_task && <Alert type="info" showIcon message="运单在途，到达并入站后再继续操作。" description={<Button type="link" onClick={() => navigate('/tasks/' + detail.active_transport_task!.id)}>查看运输任务</Button>} />}
        {detail.stage === 'OUT_FOR_DELIVERY' && <Button type="primary" disabled={busy || !actionEnabled('SIGN')} onClick={() => runEvent('SIGN')}>确认买家签收</Button>}
        {detail.stage === 'SIGNED' && <Alert type="success" showIcon message="运单已签收，物流流程完成。" />}
      </div>
      <Tabs items={[
        { key: 'tracking', label: '物流轨迹', children: detail.tracking_events.length ? <Timeline items={detail.tracking_events.map(event => ({ children: <><Text strong>{eventText[event.event_type] ?? event.event_type}</Text><br /><Text type="secondary">{formatTime(event.occurred_at)}{event.station_id ? ' · ' : ''}</Text>{event.station_id && <StationName id={event.station_id} />}</> }))} /> : <Empty description="暂无物流轨迹" /> },
        { key: 'schedule-history', label: '运输计划历史', children: scheduleHistoryError ? <Alert type="warning" showIcon message="计划历史暂不可用" description={scheduleHistoryError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} /> : scheduleHistory.length ? <Table<ScheduleHistoryItem> rowKey="version" dataSource={scheduleHistory} pagination={{ current: scheduleHistoryPage, pageSize: 20, showSizeChanger: false, total: scheduleHistoryTotal, onChange: setScheduleHistoryPage }} columns={[
          { title: '版本', dataIndex: 'version', width: 80, render: value => `v${value}` },
          { title: '全程路线', render: (_, row) => row.legs.map(leg => leg.route_code).join(' → ') || '无后续段' },
          { title: '计划时间', render: (_, row) => row.legs.map(leg => `${leg.route_code} ${formatTime(leg.planned_departure_at)} → ${formatTime(leg.planned_arrival_at)}`).join('；') },
          { title: '审核原因', dataIndex: 'reason' },
          { title: '确认时间', dataIndex: 'occurred_at', render: value => formatTime(value) },
        ]} /> : <Empty description="尚无已确认计划版本" /> },
        { key: 'destinations', label: '目的站更正记录', children: destinationChangesError ? <Alert type="warning" showIcon message="更正记录暂不可用" description={destinationChangesError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} /> : <Table<DestinationChange> rowKey="id" dataSource={destinationChanges} pagination={{ current: destinationChangesPage, pageSize: 20, showSizeChanger: false, total: destinationChangesTotal, onChange: setDestinationChangesPage }} locale={{ emptyText: <Empty description="暂无目的站更正记录" /> }} columns={[
          { title: '原目的站', dataIndex: 'previous_destination_station_id', render: value => <StationName id={value} /> },
          { title: '新目的站', dataIndex: 'destination_station_id', render: value => <StationName id={value} /> },
          { title: '更正原因', dataIndex: 'reason' },
          { title: '操作时间', dataIndex: 'occurred_at', render: value => formatTime(value) },
        ]} /> },
        { key: 'tasks', label: '运输任务历史', children: historyError ? <Alert type="warning" showIcon message="任务历史暂不可用" description={historyError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} /> : history.length ? <Table<ShipmentTaskHistory> rowKey="id" pagination={{ current: historyPage, pageSize: 20, showSizeChanger: false, total: historyTotal, onChange: setHistoryPage }} dataSource={history} columns={[
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
        {detail.stage === 'IN_TRANSIT' && <Alert type="warning" showIcon message="货物正在运输途中" description="当前运输段会照常送到原定站点；本次只调整后续路线，抵达后再按新目的站重新安排。" style={{ marginBottom: 16 }} />}
        {detail.active_transport_task && detail.active_transport_task.status !== 'IN_TRANSIT' && <Alert type="warning" showIcon message="当前运输任务尚未发车" description="确认更正后会释放这票运单未发车的安排；其他运单共用的任务不受影响。" style={{ marginBottom: 16 }} />}
        {destinationModalStep === 'edit' ? <>
          {network.error && <Alert type="error" showIcon message="站点列表加载失败" description={network.error} style={{ marginBottom: 16 }} />}
          <Alert type="info" showIcon message="更正目的站不会改变货物位置或收件地址。请确认新站能够负责该地址的配送。" style={{ marginBottom: 16 }} />
          <Form form={destinationForm} layout="vertical">
            <Form.Item label="当前目的站">
              <Text><StationName id={detail.destination_station_id} /></Text>
            </Form.Item>
            <Form.Item name="destination_station_id" label="新的目的站" rules={[{ required: true, message: '请选择新的目的站' }]}>
              <Select showSearch filterOption={fuzzySelectFilter} options={deliveryStations.map(station => ({ value: station.id, label: `${station.code} · ${station.name}` }))} placeholder="选择启用且允许派送的站点" notFoundContent="没有可选择的其他派送站点" />
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
