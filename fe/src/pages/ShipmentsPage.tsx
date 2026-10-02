import { fuzzySelectFilter } from '../fuzzySearch'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Empty, Form, Input, message, Modal, Radio, Select, Space, Spin, Table, Tabs, Tag, Timeline, Typography } from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { ModalForm, PageContainer, ProFormDateTimePicker, ProFormSelect, ProFormText, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import dayjs from 'dayjs'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type DestinationChange, type PathLeg, type PathOptions, type PathVersion, type ScheduleHistoryItem, type Shipment, type ShipmentDetail, type ShipmentPathUpdate, type ShipmentTaskHistory, type TransportRoute } from '../api'
import { RouteSequenceEditor } from '../RouteSequenceEditor'
import { SchedulePlanner } from './SchedulePlanner'
import { actionText, allowed, apiError, eventText, formatTime, stageText, StatusTag, taskStatusText, type Shared, useNetwork, StationName } from '../shared'

const { Text } = Typography
type ShipmentEvent = 'PICKUP' | 'ARRIVE' | 'START_DELIVERY' | 'SIGN'
type PathForm = { mode: 'plan' | 'routes'; plan_id?: string; route_ids?: string[]; reason: string }
const pathStatusText: Record<string, string> = { WAITING_FIRST_ARRIVAL: '等待首次入站', NEEDS_PLANNING: '待规划', READY: '可按下一段运输', RESERVED: '等待任务发车', IN_TRANSIT: '当前段运输中', COMPLETED: '路径已完成', BLOCKED: '未来路径受阻' }
const pathLegStateText: Record<string, string> = { PENDING: '待运输', RESERVED: '待发车', IN_TRANSIT: '运输中', ARRIVED: '已到达' }
const scheduleStatusText: Record<string, string> = { NOT_CONFIRMED: '待审核', CONFIRMED: '已确认', NEEDS_RECONFIRMATION: '需要重新审核', BLOCKED: '计划受阻', COMPLETED: '运输计划已完成' }
const associationStateText: Record<string, string> = { PLANNED: '未来待执行', ACTIVE: '当前执行段', RELEASED: '已解除' }

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
  const [scheduleHistory, setScheduleHistory] = useState<ScheduleHistoryItem[]>([])
  const [scheduleHistoryTotal, setScheduleHistoryTotal] = useState(0)
  const [scheduleHistoryPage, setScheduleHistoryPage] = useState(1)
  const [scheduleHistoryError, setScheduleHistoryError] = useState<string>()
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
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const [destinationModalStep, setDestinationModalStep] = useState<'edit' | 'confirm'>()
  const [destinationDraft, setDestinationDraft] = useState<{ destination_station_id: string; reason: string }>()
  const [destinationForm] = Form.useForm<{ destination_station_id: string; reason: string }>()
  const [taskForm] = Form.useForm<{ route_code: string; expected_arrival_at: string }>()
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
    })]).then(([shipment, tasks, changes, pathVersions]) => {
      if (active) {
        setDetail(shipment)
        if (tasks) { setHistory(tasks.items); setHistoryTotal(tasks.total); setHistoryError(undefined) }
        if (changes) { setDestinationChanges(changes.items); setDestinationChangesTotal(changes.total); setDestinationChangesError(undefined) }
        if (pathVersions) { setPathHistory(pathVersions.items); setPathHistoryTotal(pathVersions.total); setPathHistoryError(undefined) }
      }
    }).catch(error => { if (active) setLoadError(apiError(error)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [shipmentId, revision, retry, historyPage, destinationChangesPage, pathHistoryPage])

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
  const openTask = async (values: { route_code: string; expected_arrival_at: string }) => {
    if (!detail) return false
    if (!currentPath?.version || !values.route_code) {
      messageApi.error('当前运单没有可用的完整路径，请先完成路径规划。')
      return false
    }
    const body = { route_code: values.route_code, expected_path_versions: { [detail.id]: currentPath.version }, expected_arrival_at: values.expected_arrival_at.replace(' ', 'T') + '+08:00', shipment_ids: [Number(detail.id)] }
    const result = await mutate('create-task:' + JSON.stringify(body), key => api.createTask(body, key), '运输任务已创建')
    if (result) { setTaskOpen(false); navigate('/tasks/' + result.id) }
    return Boolean(result)
  }
  const openNextTask = () => {
    taskForm.setFieldsValue({ route_code: currentPath?.next_route_code ?? undefined, expected_arrival_at: undefined })
    setTaskOpen(true)
  }
  const actionEnabled = (name: string) => allowed(detail?.allowed_actions ?? [], name)?.enabled === true
  const destinationAction = allowed(detail?.allowed_actions ?? [], 'UPDATE_DESTINATION')
  const pathAction = allowed(detail?.allowed_actions ?? [], 'UPDATE_PATH')
  const currentPath = detail?.transport_path
  const deliveryStations = network.stations.filter(station => station.enabled && station.allows_delivery && station.id !== detail?.destination_station_id)
  const routeById = new Map(network.routes.map(route => [route.id, route]))
  const activePathPlan = pathOptions?.plans.find(plan => plan.id === selectedPlanId)
  const selectedRouteRows = (pathMode === 'plan' ? activePathPlan?.route_ids : selectedPathRouteIds)
    ?.map(id => routeById.get(id)).filter((route): route is TransportRoute => Boolean(route)) ?? []
  const pathRouteChainValid = (() => {
    const anchorId = pathOptions?.path.anchor_station_id
    const destinationId = detail?.destination_station_id
    if (!anchorId || !destinationId) return false
    if (!selectedRouteRows.length) return anchorId === destinationId
    return selectedRouteRows[0].origin.id === anchorId
      && selectedRouteRows.every((route, index) => index === 0 || selectedRouteRows[index - 1].destination.id === route.origin.id)
      && selectedRouteRows[selectedRouteRows.length - 1].destination.id === destinationId
  })()
  const draftPlan = pathDraft?.mode === 'plan' ? pathOptions?.plans.find(plan => plan.id === pathDraft.plan_id) : undefined
  const draftRouteRows = (pathDraft?.mode === 'plan' ? draftPlan?.route_ids : pathDraft?.route_ids)
    ?.map(id => routeById.get(id)).filter((route): route is TransportRoute => Boolean(route)) ?? []
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
  const openPathPlanner = async () => {
    if (!detail) return
    try {
      const options = await api.shipmentPathOptions(detail.id)
      setPathOptions(options)
      pathForm.setFieldsValue({ mode: options.plans.length ? 'plan' : 'routes', plan_id: undefined, route_ids: [], reason: '' })
      setPathModalStep('edit')
    } catch (error) {
      messageApi.error(apiError(error))
    }
  }
  const closePathPlanner = () => {
    setPathModalStep(undefined)
    setPathDraft(undefined)
    setPathOptions(undefined)
    pathForm.resetFields()
  }
  const confirmPathUpdate = async () => {
    if (!detail || !pathOptions || !pathDraft || !pathOptions.path.anchor_station_id) return
    const base = {
      expected_version: pathOptions.path.version,
      expected_anchor_station_id: Number(pathOptions.path.anchor_station_id),
      reason: pathDraft.reason.trim(),
    }
    let body: ShipmentPathUpdate
    if (pathDraft.mode === 'plan') {
      const plan = pathOptions.plans.find(item => item.id === pathDraft.plan_id)
      if (!plan) { messageApi.error('选中的路径方案已不可用，请重新加载后选择'); return }
      body = { ...base, plan_id: Number(plan.id), expected_plan_version: plan.version }
    } else {
      body = { ...base, route_ids: (pathDraft.route_ids ?? []).map(Number) }
    }
    const result = await mutate(
      'shipment-path:' + detail.id + ':' + JSON.stringify(body),
      key => api.updateShipmentPath(detail.id, body, key),
      '运单未来路径已更新',
    )
    if (result) {
      closePathPlanner()
      setPathHistoryPage(1)
      setRetry(value => value + 1)
    } else {
      // Refresh the version, anchor and options after a conflict while keeping the proposed edits.
      void api.shipmentPathOptions(detail.id).then(setPathOptions).catch(error => messageApi.error(apiError(error)))
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
      {detail.scheduling_mode === 'REVIEWED' ? <section style={{ marginTop: 24 }}>
        <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }} align="center" wrap>
          <Text strong>全程运输计划</Text>
          <Button type="primary" disabled={busy || ['OUT_FOR_DELIVERY', 'SIGNED'].includes(detail.stage) || detail.schedule?.status === 'COMPLETED'} onClick={() => setScheduleOpen(true)}>{detail.schedule?.version ? '调整未来安排' : '预览并审核计划'}</Button>
        </Space>
        {detail.schedule ? <>
          <Alert type={detail.schedule.status === 'BLOCKED' || detail.schedule.status === 'NEEDS_RECONFIRMATION' ? 'warning' : detail.schedule.status === 'COMPLETED' ? 'success' : 'info'} showIcon message={`${scheduleStatusText[detail.schedule.status] ?? detail.schedule.status} · 计划版本 v${detail.schedule.version}`} description={<>
            {detail.schedule.origin_station_id && <>计划起点：<StationName id={detail.schedule.origin_station_id} />。 </>}
            {detail.schedule.reason && <>{detail.schedule.reason} </>}
            <Text type="secondary">计划时间是审核基准；最新预测会随运输进度变化，实际发车和到达仍由操作记录。</Text>
          </>} style={{ marginBottom: 12 }} />
          {detail.schedule.configuration_risks.map((risk, index) => <Alert key={index} type="warning" showIcon message={risk.message ?? '计划中的线路或站点配置已停用'} style={{ marginBottom: 8 }} />)}
          {detail.schedule.legs.length ? <Table rowKey="path_leg_id" size="small" pagination={false} dataSource={detail.schedule.legs} scroll={{ x: 1050 }} columns={[
            { title: '段', width: 56, render: (_, leg) => leg.position + 1 },
            { title: '线路', width: 180, render: (_, leg) => <>{leg.route_code}<br /><Text type="secondary"><StationName id={leg.origin_station_id} /> → <StationName id={leg.destination_station_id} /></Text></> },
            { title: '执行状态', width: 130, render: (_, leg) => <>{leg.task_status ? taskStatusText[leg.task_status] : '尚无任务'}<br /><Text type="secondary">{associationStateText[leg.association_state ?? ''] ?? ''}</Text></> },
            { title: '确认计划', width: 170, render: (_, leg) => <>出发 {formatTime(leg.planned_departure_at)}<br />到达 {formatTime(leg.planned_arrival_at)}</> },
            { title: '最新预计', width: 190, render: (_, leg) => <>出发 {formatTime(leg.forecast_departure_at)}<br />到达 {formatTime(leg.forecast_arrival_at)}{leg.forecast_stale && <><br /><Tag color="orange">预测已过期</Tag></>}</> },
            { title: '实际时间', width: 170, render: (_, leg) => <>发车 {formatTime(leg.actual_departure_at)}<br />到达 {formatTime(leg.actual_arrival_at)}</> },
            { title: '任务', width: 150, render: (_, leg) => leg.task_id ? <Link to={`/tasks/${leg.task_id}`}>{leg.task_no ?? `任务 ${leg.task_id}`}</Link> : '—' },
            { title: '等待条件', width: 200, render: (_, leg) => <>{leg.ready_at ? `就绪时间 ${formatTime(leg.ready_at)}` : ''}{leg.waiting_members.length > 0 && <Text type="secondary" style={{ display: 'block' }}>等待运单：{leg.waiting_members.map(member => member.shipment_no ?? member.shipment_id).join('、')}</Text>}</> },
          ]} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未确认运输计划；先预览路线和逐站时间。" />}
        </> : <Alert type="warning" showIcon message="后端没有返回运输计划状态" description="请确认服务已升级至 V7，然后刷新运单。" />}
      </section> : <section style={{ marginTop: 24 }}>
        <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }} align="center" wrap>
          <Text strong>完整路径与当前进度</Text>
          <Button disabled={busy || !pathAction?.enabled} onClick={() => void openPathPlanner()}>{currentPath?.version ? '调整未来路径' : '规划完整路径'}</Button>
        </Space>
        {!currentPath && <Alert type="info" showIcon message="后端尚未返回运单路径信息。" description={pathAction?.reason ?? '请确认后端已升级至 V6。'} />}
        {currentPath && <>
          <Alert type={currentPath.status === 'BLOCKED' || currentPath.status === 'NEEDS_PLANNING' ? 'warning' : currentPath.status === 'COMPLETED' ? 'success' : 'info'} showIcon message={`${pathStatusText[currentPath.status] ?? currentPath.status} · 路径版本 v${currentPath.version}`} description={<>
            {currentPath.anchor_station_id && <>当前接续站：<StationName id={currentPath.anchor_station_id} />。 </>}
            {currentPath.next_route_code && <>下一段：<Text strong>{currentPath.next_route_code}</Text>。 </>}
            {currentPath.reason && <>{currentPath.reason} </>}
            <Text type="secondary">路径是运输安排；只有对应运输任务发车、到达后，才会形成实际物流进度。</Text>
          </>} style={{ marginBottom: 12 }} />
          {currentPath.legs.length ? <Table<PathLeg> size="small" rowKey="id" pagination={false} dataSource={currentPath.legs} columns={[
            { title: '顺序', dataIndex: 'position', width: 72, render: value => value + 1 },
            { title: '线路', dataIndex: 'route_code', width: 140 },
            { title: '运输区间', render: (_, leg) => <><StationName id={leg.origin_station_id} /> → <StationName id={leg.destination_station_id} /></> },
            { title: '当前进度', dataIndex: 'state', width: 120, render: value => pathLegStateText[value] ?? value },
            { title: '任务', dataIndex: 'task_id', width: 140, render: value => value ? <Link to={`/tasks/${value}`}>查看任务</Link> : '尚未创建' },
          ]} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={currentPath.status === 'WAITING_FIRST_ARRIVAL' ? '首次入站后才确定完整路径' : '当前没有已绑定路径段'} />}
        </>}
        {pathAction && !pathAction.enabled && <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>{pathAction.reason ?? '当前阶段暂不能调整路径。'}</Text>}
      </section>}
      <div style={{ margin: '20px 0 28px' }}>
        <Text strong style={{ display: 'block', marginBottom: 12 }}>下一步操作</Text>
        <Space wrap>
          {detail.stage === 'PENDING_PICKUP' && <Button type="primary" disabled={busy || !actionEnabled('PICKUP')} onClick={() => runEvent('PICKUP')}>确认揽收</Button>}
          {detail.stage === 'PENDING_PICKUP' && <Button disabled={busy || !actionEnabled('UPDATE_ADDRESS')} onClick={() => setAddressOpen(true)}>编辑履约地址</Button>}
          <Button disabled={busy || !destinationAction?.enabled} onClick={() => { destinationForm.resetFields(); setDestinationModalStep('edit') }}>更正目的站</Button>
        </Space>
        {destinationAction && !destinationAction.enabled && <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>{destinationAction.reason ?? '当前运单暂不可更正目的站。'}</Text>}
        {!destinationAction && <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>后端未返回目的站更正资格，请刷新运单后重试。</Text>}
        {detail.stage === 'PICKED_UP' && <><Form.Item label="首次入站站点" style={{ marginBottom: 12, maxWidth: 360 }}><Select showSearch filterOption={fuzzySelectFilter} value={firstStation} onChange={setFirstStation} options={network.stations.filter(s => s.enabled && s.allows_first_arrival).map(s => ({ value: s.id, label: s.code + ' · ' + s.name }))} placeholder="选择包裹实际进入的站点" /></Form.Item><Button type="primary" disabled={busy || !firstStation || !actionEnabled('ARRIVE')} onClick={() => runEvent('ARRIVE')}>确认入站</Button></>}
        {detail.stage === 'AT_STATION' && actionEnabled('START_DELIVERY') && <Space wrap><Button type="primary" disabled={busy} onClick={() => runEvent('START_DELIVERY')}>开始派送</Button><Text type="secondary">运单已到达目的站，可以交给末端配送。</Text></Space>}
        {detail.stage === 'AT_STATION' && !actionEnabled('START_DELIVERY') && detail.active_transport_task && <Alert type="info" showIcon message="运单正在等待或进行站间运输" description={<Space wrap><span><StationName id={detail.active_transport_task.origin_station_id} /> → <StationName id={detail.active_transport_task.destination_station_id} /></span><Button type="link" onClick={() => navigate('/tasks/' + detail.active_transport_task!.id)}>查看运输任务</Button></Space>} />}
        {detail.stage === 'AT_STATION' && !actionEnabled('START_DELIVERY') && !detail.active_transport_task && actionEnabled('CREATE_TRANSPORT_TASK') && <Alert type="info" showIcon message="运单已准备好进入下一段运输" description={<Space wrap><span>下一段线路：<Text strong>{currentPath?.next_route_code ?? '尚未确定'}</Text>；到达目的站后才能开始派送。</span><Button type="primary" onClick={openNextTask}>按下一段创建运输任务</Button></Space>} />}
        {detail.stage === 'AT_STATION' && !actionEnabled('START_DELIVERY') && !detail.active_transport_task && !actionEnabled('CREATE_TRANSPORT_TASK') && <Text type="secondary">{allowed(detail.allowed_actions, 'CREATE_TRANSPORT_TASK')?.reason ?? '当前运单暂不可创建运输任务。'}</Text>}
        {detail.stage === 'IN_TRANSIT' && detail.active_transport_task && <Alert type="info" showIcon message="运单在途，到达并入站后再继续操作。" description={<Button type="link" onClick={() => navigate('/tasks/' + detail.active_transport_task!.id)}>查看运输任务</Button>} />}
        {detail.stage === 'OUT_FOR_DELIVERY' && <Button type="primary" disabled={busy || !actionEnabled('SIGN')} onClick={() => runEvent('SIGN')}>确认买家签收</Button>}
        {detail.stage === 'SIGNED' && <Alert type="success" showIcon message="运单已签收，物流流程完成。" />}
      </div>
      <Tabs items={[
        { key: 'tracking', label: '物流轨迹', children: detail.tracking_events.length ? <Timeline items={detail.tracking_events.map(event => ({ children: <><Text strong>{eventText[event.event_type] ?? event.event_type}</Text><br /><Text type="secondary">{formatTime(event.occurred_at)}{event.station_id ? ' · ' : ''}</Text>{event.station_id && <StationName id={event.station_id} />}</> }))} /> : <Empty description="暂无物流轨迹" /> },
        { key: 'path-history', label: '路径安排历史', children: pathHistoryError ? <Alert type="warning" showIcon message="路径历史暂不可用" description={pathHistoryError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} /> : pathHistory.length ? <Table<PathVersion> rowKey="version" dataSource={pathHistory} pagination={{ current: pathHistoryPage, pageSize: 20, showSizeChanger: false, total: pathHistoryTotal, onChange: setPathHistoryPage }} columns={[
          { title: '版本', dataIndex: 'version', width: 80, render: value => `v${value}` },
          { title: '目的站', dataIndex: 'destination_station_id', render: value => <StationName id={value} /> },
          { title: '来源方案', render: (_, row) => row.source_plan_id ? `方案 ${row.source_plan_id} · v${row.source_plan_version}` : '手动安排' },
          { title: '当时路径', render: (_, row) => row.legs.map(leg => leg.route_code).join(' → ') || '无后续段' },
          { title: '调整原因', dataIndex: 'reason' },
          { title: '记录时间', dataIndex: 'occurred_at', render: value => formatTime(value) },
        ]} /> : <Empty description="尚无路径安排历史；未绑定前的旧任务和轨迹仍见各自历史记录。" /> },
        ...(detail.scheduling_mode === 'REVIEWED' ? [{ key: 'schedule-history', label: '运输计划历史', children: scheduleHistoryError ? <Alert type="warning" showIcon message="计划历史暂不可用" description={scheduleHistoryError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} /> : scheduleHistory.length ? <Table<ScheduleHistoryItem> rowKey="version" dataSource={scheduleHistory} pagination={{ current: scheduleHistoryPage, pageSize: 20, showSizeChanger: false, total: scheduleHistoryTotal, onChange: setScheduleHistoryPage }} columns={[
          { title: '版本', dataIndex: 'version', width: 80, render: value => `v${value}` },
          { title: '全程路线', render: (_, row) => row.legs.map(leg => leg.route_code).join(' → ') || '无后续段' },
          { title: '计划时间', render: (_, row) => row.legs.map(leg => `${leg.route_code} ${formatTime(leg.planned_departure_at)} → ${formatTime(leg.planned_arrival_at)}`).join('；') },
          { title: '审核原因', dataIndex: 'reason' },
          { title: '确认时间', dataIndex: 'occurred_at', render: value => formatTime(value) },
        ]} /> : <Empty description="尚无已确认计划版本" /> }] : []),
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
      <ModalForm<{ route_code: string; expected_arrival_at: string }> title="创建运输任务" open={taskOpen} onOpenChange={setTaskOpen} form={taskForm} modalProps={{ destroyOnHidden: true }} onFinish={openTask}>
        <ProFormSelect name="route_code" label="路径下一段线路" options={network.routes.filter(r => r.enabled && r.origin.enabled && r.destination.enabled && r.code === currentPath?.next_route_code).map(r => ({ value: r.code, label: r.code + ' · ' + r.origin.code + ' → ' + r.destination.code }))} rules={[{ required: true }]} fieldProps={{ showSearch: true, filterOption: fuzzySelectFilter, placeholder: '按完整路径自动确定', notFoundContent: '当前路径没有可创建的下一段' }} />
        <ProFormDateTimePicker name="expected_arrival_at" label="预计到达时间（北京时间）" rules={[{ required: true }]} fieldProps={{ showTime: true, disabledDate: date => date.isBefore(dayjs(), 'day') }} />
        <Text type="secondary">此任务只创建当前下一段。提交时会带上路径版本，后端会再次检查线路、运单位置和版本是否仍有效。</Text>
      </ModalForm>
      <Modal title={pathModalStep === 'confirm' ? '确认未来路径安排' : '规划或调整未来路径'} open={Boolean(pathModalStep)} onCancel={() => pathModalStep === 'confirm' ? setPathModalStep('edit') : closePathPlanner()} onOk={() => {
        if (pathModalStep === 'confirm') void confirmPathUpdate()
        else void pathForm.validateFields().then(values => {
          if (!pathRouteChainValid) { messageApi.error('所选路径必须从接续站连续到运单目的站'); return }
          setPathDraft(values)
          setPathModalStep('confirm')
        }).catch(() => undefined)
      }} okText={pathModalStep === 'confirm' ? '确认保存路径' : '检查路径安排'} cancelText={pathModalStep === 'confirm' ? '返回修改' : '取消'} confirmLoading={busy} okButtonProps={{ disabled: busy || !pathAction?.enabled || !pathOptions?.path.anchor_station_id }} width={680}>
        {pathModalStep === 'edit' && pathOptions && <>
          <Descriptions bordered size="small" column={1} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="冻结前缀">{pathOptions.path.legs.filter(leg => leg.state === 'ARRIVED' || leg.state === 'IN_TRANSIT').map(leg => `${leg.route_code} (${pathLegStateText[leg.state]})`).join(' → ') || '暂无已完成或运输中的路径段'}</Descriptions.Item>
            <Descriptions.Item label="接续站"><StationName id={pathOptions.path.anchor_station_id ?? ''} /></Descriptions.Item>
            <Descriptions.Item label="最终目的站"><StationName id={detail.destination_station_id} /></Descriptions.Item>
          </Descriptions>
          {pathOptions.plans.length === 0 && <Alert type="info" showIcon message="当前没有匹配的可用方案，可按顺序选择线路组成完整路径。" style={{ marginBottom: 16 }} />}
          <Form form={pathForm} layout="vertical">
            {pathOptions.plans.length > 0 && <Form.Item name="mode" label="路径来源"><Radio.Group options={[{ label: '使用完整路径方案', value: 'plan' }, { label: '手动选择线路', value: 'routes' }]} /></Form.Item>}
            {pathMode === 'plan' && <Form.Item name="plan_id" label="完整路径方案" rules={[{ required: true, message: '请选择一个路径方案' }]}><Select showSearch filterOption={fuzzySelectFilter} options={pathOptions.plans.map(plan => ({ value: plan.id, label: `${plan.code} · ${plan.name} · ${plan.route_ids.map(id => routeById.get(id)?.code ?? id).join(' → ')}` }))} placeholder="选择一次性采用的完整方案" /></Form.Item>}
            {(pathMode === 'routes' || pathOptions.plans.length === 0) && <RouteSequenceEditor name="route_ids" routes={network.routes} title="逐段配置未来路径" emptyText="从接续站开始添加线路，直到运单目的站。" anchorStationId={pathOptions.path.anchor_station_id ?? undefined} destinationStationId={detail.destination_station_id} />}
            <Form.Item name="reason" label="调整原因" rules={[{ required: true, whitespace: true, message: '请填写安排原因' }, { max: 500, message: '最多 500 个字符' }]}><Input.TextArea maxLength={500} showCount rows={3} placeholder="说明为什么采用或调整这条路径" /></Form.Item>
          </Form>
          {pathMode === 'plan' && activePathPlan && <Alert type={pathRouteChainValid ? 'success' : 'warning'} showIcon message={`方案路径：${selectedRouteRows.length ? `${selectedRouteRows[0].origin.code} ${selectedRouteRows.map(route => `→ ${route.destination.code}`).join(' ')}` : '—'}`} description={pathRouteChainValid ? '方案从接续站连续到运单目的站。' : '此方案与当前运单的接续站或目的站不匹配。'} />}
        </>}
        {pathModalStep === 'confirm' && pathDraft && pathOptions && <>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="冻结前缀">{pathOptions.path.legs.filter(leg => leg.state === 'ARRIVED' || leg.state === 'IN_TRANSIT').map(leg => `${leg.route_code} (${pathLegStateText[leg.state]})`).join(' → ') || '无'}</Descriptions.Item>
            <Descriptions.Item label="接续站"><StationName id={pathOptions.path.anchor_station_id ?? ''} /></Descriptions.Item>
            <Descriptions.Item label="未来路径">{draftRouteRows.map(route => route.code).join(' → ') || '无后续段（接续站即目的站）'}</Descriptions.Item>
            <Descriptions.Item label="最终目的站"><StationName id={detail.destination_station_id} /></Descriptions.Item>
            <Descriptions.Item label="路径前提">v{pathOptions.path.version} · 接续站 <StationName id={pathOptions.path.anchor_station_id ?? ''} /></Descriptions.Item>
            <Descriptions.Item label="调整原因">{pathDraft.reason.trim()}</Descriptions.Item>
          </Descriptions>
          <Alert type="warning" showIcon message="保存只调整接续站之后的未来安排；已到达和正在运输的路径段、运输任务及物流轨迹保持不变。" style={{ marginTop: 16 }} />
        </>}
      </Modal>
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
      {detail.scheduling_mode === 'REVIEWED' && <SchedulePlanner shipment={detail} routes={network.routes} open={scheduleOpen} busy={busy} mutate={mutate} onClose={() => setScheduleOpen(false)} onSaved={() => { setScheduleHistoryPage(1); setRetry(value => value + 1) }} />}
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
