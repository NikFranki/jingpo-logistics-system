import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Form, Modal, Space, Spin, Table, Typography, message } from 'antd'
import { ArrowLeftOutlined, PlusOutlined } from '@ant-design/icons'
import { ModalForm, PageContainer, ProFormDateTimePicker, ProFormSelect, ProFormTextArea, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import dayjs from 'dayjs'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Candidate, type CancelPreview, type RouteCode, type TaskDetail, type TaskItem } from '../api'
import { apiError, delayText, formatTime, StatusTag, stageText, taskStatusText, TaskShipmentTag, type Shared, useNetwork, StationName } from '../shared'

const { Text } = Typography

function TasksPage({ revision, mutate, clock }: Pick<Shared, 'revision' | 'mutate'> & { clock?: string }) {
  const network = useNetwork(revision)
  const [taskForm] = Form.useForm()
  const actionRef = useRef<ActionType | undefined>(undefined)
  const [createOpen, setCreateOpen] = useState(false)
  const [loadError, setLoadError] = useState<string>()
  const [messageApi, holder] = message.useMessage()
  const [candidateOptions, setCandidateOptions] = useState<Candidate[]>([])
  const [candidateTotal, setCandidateTotal] = useState(0)
  const [candidatePage, setCandidatePage] = useState(0)
  const [candidateLoading, setCandidateLoading] = useState(false)
  const [candidateVersionLoading, setCandidateVersionLoading] = useState(false)
  const [candidatePathVersions, setCandidatePathVersions] = useState<Record<string, number>>({})
  const candidateRequestId = useRef(0)
  const candidateVersionRequestId = useRef(0)
  const navigate = useNavigate()
  const selectedRoute = Form.useWatch('route_code', taskForm)
  useEffect(() => { actionRef.current?.reload() }, [revision])
  useEffect(() => {
    const requestId = ++candidateRequestId.current
    setCandidateOptions([])
    setCandidateTotal(0)
    setCandidatePage(0)
    setCandidatePathVersions({})
    if (!selectedRoute) return

    let active = true
    setCandidateLoading(true)
    api.candidates(selectedRoute as RouteCode, 1).then(result => {
      if (!active || requestId !== candidateRequestId.current) return
      setCandidateOptions(result.items)
      setCandidateTotal(result.total)
      setCandidatePage(result.page)
    }).catch(error => {
      if (active && requestId === candidateRequestId.current) messageApi.error(apiError(error))
    }).finally(() => {
      if (active && requestId === candidateRequestId.current) setCandidateLoading(false)
    })
    return () => { active = false }
  }, [selectedRoute, messageApi])

  const loadCandidatePathVersions = async (shipmentIds: string[]) => {
    const requestId = ++candidateVersionRequestId.current
    const retained = Object.fromEntries(shipmentIds.filter(id => candidatePathVersions[id] !== undefined).map(id => [id, candidatePathVersions[id]]))
    setCandidatePathVersions(retained)
    const pendingIds = shipmentIds.filter(id => candidatePathVersions[id] === undefined)
    if (!pendingIds.length) { setCandidateVersionLoading(false); return }
    setCandidateVersionLoading(true)
    try {
      const entries = await Promise.all(pendingIds.map(async id => [id, (await api.shipmentPath(id)).version] as const))
      if (requestId === candidateVersionRequestId.current) setCandidatePathVersions(current => ({ ...current, ...Object.fromEntries(entries) }))
    } catch (error) {
      if (requestId === candidateVersionRequestId.current) messageApi.error(apiError(error))
    } finally {
      if (requestId === candidateVersionRequestId.current) setCandidateVersionLoading(false)
    }
  }

  const loadMoreCandidates = async (event: React.UIEvent<HTMLDivElement>) => {
    const holder = event.currentTarget
    const hasMore = candidateOptions.length < candidateTotal
    if (!selectedRoute || candidateLoading || !hasMore || holder.scrollTop + holder.clientHeight < holder.scrollHeight - 24) return

    const requestId = candidateRequestId.current
    const nextPage = candidatePage + 1
    setCandidateLoading(true)
    try {
      const result = await api.candidates(selectedRoute as RouteCode, nextPage)
      if (requestId !== candidateRequestId.current) return
      setCandidateOptions(current => [...current, ...result.items])
      setCandidateTotal(result.total)
      setCandidatePage(result.page)
    } catch (error) {
      if (requestId === candidateRequestId.current) messageApi.error(apiError(error))
    } finally {
      if (requestId === candidateRequestId.current) setCandidateLoading(false)
    }
  }
  const columns: ProColumns<TaskItem>[] = [
    { title: '任务号', dataIndex: 'task_no', copyable: true },
    { title: '线路', dataIndex: 'route_code', valueEnum: Object.fromEntries(network.routes.map(r => [r.code, { text: `${r.code} · ${r.origin.code} → ${r.destination.code}${r.enabled ? '' : '（停用）'}` }])) },
    { title: '计划来源', dataIndex: 'scheduling_source', search: false, render: value => value === 'PLAN' ? '全程计划' : '旧版单段任务' },
    { title: '任务状态', dataIndex: 'status', valueEnum: Object.fromEntries(Object.entries(taskStatusText).map(([key, text]) => [key, { text }])) },
    { title: '计划发车', dataIndex: 'planned_departure_at', valueType: 'dateTime', search: false, render: (_, row) => row.planned_departure_at ? formatTime(row.planned_departure_at) : '—' },
    { title: '预计到达', dataIndex: 'expected_arrival_at', valueType: 'dateTime', search: false },
    { title: '最新预测', dataIndex: 'forecast_arrival_at', valueType: 'dateTime', search: false, render: (_, row) => <>{formatTime(row.forecast_arrival_at)}{row.forecast_stale && <Text type="warning"> · 预测已过期</Text>}</> },
    { title: '实际到达', dataIndex: 'arrived_at', valueType: 'dateTime', search: false },
    { title: '取消时间', dataIndex: 'cancelled_at', valueType: 'dateTime', search: false },
    { title: '取消原因', dataIndex: 'cancel_reason', search: false, ellipsis: true },
    { title: '延误提示', dataIndex: 'delay_status', search: false, render: (_, row) => row.delay_status === 'OVERDUE' ? <StatusTag tone="error">{delayText(row)}</StatusTag> : row.delay_status === 'LATE_ARRIVAL' ? <StatusTag tone="warning">{delayText(row)}</StatusTag> : delayText(row) },
    { title: '操作', valueType: 'option', render: (_, row) => <Link to={`/tasks/${row.id}`}>查看详情</Link> },
  ]
  return <>{holder}<PageContainer title="运输任务" subTitle="V7 运单由计划审核一次生成全程任务；旧运单仍可按线路创建单段任务。" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => { taskForm.resetFields(); setCandidatePathVersions({}); setCreateOpen(true) }}>创建旧版单段任务</Button>}>
    {network.error && <Alert type="error" message={network.error} style={{ marginBottom: 16 }} />}{loadError && <Alert type="error" showIcon message="运输任务列表加载失败" description={loadError} action={<Button size="small" onClick={() => actionRef.current?.reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <ProTable<TaskItem> actionRef={actionRef} rowKey="id" columns={columns} search={{ labelWidth: 90 }} pagination={{ pageSize: 20 }} request={async params => {
      try { const result = await api.tasks({ page: params.current ?? 1, page_size: params.pageSize ?? 20, task_no: params.task_no as string, route_code: params.route_code as string, status: params.status as string }); setLoadError(undefined); return { data: result.items, success: true, total: result.total } }
      catch (error) { const reason = apiError(error); setLoadError(reason); return { data: [], success: false, total: 0 } }
    }} />
    <ModalForm title="创建运输任务" dateFormatter="string" open={createOpen} onOpenChange={setCreateOpen} form={taskForm} onValuesChange={changes => {
      if ('route_code' in changes) {
        taskForm.setFieldValue('shipment_ids', [])
        setCandidatePathVersions({})
        candidateVersionRequestId.current += 1
        setCandidateVersionLoading(false)
      }
    }} modalProps={{ destroyOnHidden: true }} onFinish={async (values: { route_code: RouteCode; expected_arrival_at: string; shipment_ids: string[] }) => {
      // ProForm submits dateTime as a formatted string; this field is explicitly Beijing time.
      if (candidateVersionLoading || values.shipment_ids.some(id => candidatePathVersions[id] === undefined)) {
        messageApi.error('正在读取所选运单的路径版本，请稍后再提交。')
        return false
      }
      const expected_path_versions = Object.fromEntries(values.shipment_ids.map(id => [id, candidatePathVersions[id]]))
      const body = { route_code: values.route_code, expected_path_versions, expected_arrival_at: `${values.expected_arrival_at.replace(' ', 'T')}+08:00`, shipment_ids: values.shipment_ids.map(Number) }
      const result = await mutate(`create-task:${JSON.stringify(body)}`, key => api.createTask(body, key), '运输任务已创建；运单仍在起点站')
      if (result) { setCreateOpen(false); actionRef.current?.reload(); navigate(`/tasks/${result.id}`) }
      return Boolean(result)
    }}>
      <ProFormSelect name="route_code" label="下一段线路分组" options={network.routes.filter(r => r.enabled && r.origin.enabled && r.destination.enabled).map(r => ({ label: `${r.code} · ${r.origin.code} → ${r.destination.code}`, value: r.code }))} fieldProps={{ notFoundContent: '暂无可用线路，请先配置站点、线路和运单路径' }} rules={[{ required: true }]} />
      <ProFormDateTimePicker name="expected_arrival_at" label="预计到达时间（北京时间）" rules={[{ required: true }]} fieldProps={{ showTime: true, disabledDate: date => Boolean(clock && date.isBefore(dayjs(clock), 'day')) }} />
      <ProFormSelect name="shipment_ids" label="下一段为该线路的运单" mode="multiple" dependencies={['route_code']} options={candidateOptions.map(item => ({ label: `${item.shipment_no} · ${stageText[item.stage]}`, value: item.id }))} rules={[{ required: true, message: '至少选择一张运单' }]} fieldProps={{ loading: candidateLoading || candidateVersionLoading, showSearch: true, optionFilterProp: 'label', placeholder: '系统只列出下一段匹配该线路的运单', maxCount: 100, maxTagCount: 'responsive', onPopupScroll: loadMoreCandidates, onChange: (ids: string[]) => void loadCandidatePathVersions(ids), notFoundContent: candidateLoading ? '正在加载运单…' : '当前线路暂无可选运单' }} />
      <Text type="secondary">候选按路径的下一段线路分组；提交会带上每张运单当前看到的路径版本，后端冲突时整批不创建。若提示路径版本冲突，可刷新版本后核对并重试。预计到达须晚于当前演示时间 {formatTime(clock)}。</Text>
      <Button type="link" size="small" disabled={!selectedRoute || !taskForm.getFieldValue('shipment_ids')?.length || candidateVersionLoading} loading={candidateVersionLoading} onClick={() => void loadCandidatePathVersions(taskForm.getFieldValue('shipment_ids') ?? [])}>刷新已选运单路径版本</Button>
    </ModalForm>
  </PageContainer></>
}

function TaskDetailPage({ revision, busy, mutate }: Shared) {
  const { taskId = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<TaskDetail>()
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string>()
  const [retry, setRetry] = useState(0)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [actionToConfirm, setActionToConfirm] = useState<'depart' | 'arrive'>()
  const [actionSaving, setActionSaving] = useState(false)
  const [cancelPreview, setCancelPreview] = useState<CancelPreview>()
  const [cancelPreviewLoading, setCancelPreviewLoading] = useState(false)
  const [cancelReason, setCancelReason] = useState('')
  const [cancelImpactTasks, setCancelImpactTasks] = useState<Record<string, TaskDetail>>({})
  const [messageApi, holder] = message.useMessage()

  useEffect(() => {
    let active = true
    setLoading(true)
    setLoadError(undefined)
    api.task(taskId).then(result => { if (active) setDetail(result) }).catch(error => { if (active) setLoadError(apiError(error)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [taskId, revision, retry])

  const runTaskAction = (action: 'depart' | 'arrive') => {
    if (!detail) return
    setActionToConfirm(action)
  }
  const confirmTaskAction = async () => {
    if (!detail || !actionToConfirm) return
    const action = actionToConfirm
    setActionSaving(true)
    const result = await mutate('task-action:' + detail.id + ':' + action + ':' + detail.schedule_revision, key => api.taskAction(detail.id, action, key, action === 'depart' && detail.scheduling_source === 'PLAN' ? detail.schedule_revision : undefined), action === 'depart' ? '任务已发车' : '任务已到达，全部关联运单已入站')
    setActionSaving(false)
    if (result) setActionToConfirm(undefined)
  }
  const cancelTask = async (values: { reason: string }) => {
    if (!detail) return false
    if (detail.scheduling_source === 'PLAN') {
      setCancelReason(values.reason.trim()); setCancelPreviewLoading(true)
      try {
        const impact = await api.previewTaskCancel(detail.id, values.reason.trim())
        setCancelPreview(impact)
        const taskIds = [...new Set(impact.impact.map(item => item.task_id).filter(id => id !== detail.id))]
        const taskRows = await Promise.all(taskIds.map(async id => [id, await api.task(id)] as const))
        setCancelImpactTasks(Object.fromEntries(taskRows))
      }
      catch (error) { messageApi.error(apiError(error)) }
      finally { setCancelPreviewLoading(false) }
      return false
    }
    const result = await mutate('cancel-task:' + detail.id + ':' + values.reason.trim(), key => api.cancelTask(detail.id, values.reason.trim(), key), '运输任务已取消，运单已解除占用')
    if (result) setCancelOpen(false)
    return Boolean(result)
  }
  const confirmPlannedCancel = async () => {
    if (!detail || !cancelPreview) return
    const body = { reason: cancelReason, expected_schedule_revision: cancelPreview.schedule_revision, cancel_token: cancelPreview.cancel_token }
    const result = await mutate(`cancel-planned:${detail.id}:${cancelPreview.schedule_revision}:${cancelReason}`, key => api.confirmTaskCancel(detail.id, body, key), '运输任务及受影响的未来安排已解除')
    if (result) { setCancelPreview(undefined); setCancelOpen(false) }
  }
  const disabledActionReason = (action: string) => {
    const rule = detail?.allowed_actions.find(item => item.action === action)
    return rule && !rule.enabled ? rule.reason : undefined
  }

  return <>{holder}<PageContainer title={detail?.task_no ?? '运输任务详情'} subTitle="查看运输安排、执行任务，或在发车前取消错误安排。" extra={<Space wrap><Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tasks')}>返回运输任务</Button>{detail && <Button onClick={() => navigate('/simulation')}>打开演示时钟</Button>}</Space>}>
    {loadError && <Alert type="error" showIcon message="运输任务详情加载失败" description={loadError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} style={{ marginBottom: 16 }} />}
    {loading && !detail ? <Spin /> : detail && <>
      {detail.status === 'ARRIVED' && <Alert style={{ marginBottom: 16 }} type="success" showIcon message={<>任务已到达：<Text strong><StationName id={detail.destination_station_id} /></Text></>} />}
      {detail.status === 'CANCELLED' && <Alert style={{ marginBottom: 16 }} type="warning" showIcon message="运输任务已取消，关联运单仍在起点站且已解除占用。" description={<>取消时间：{formatTime(detail.cancelled_at)}。取消原因：{detail.cancel_reason || '—'}</>} />}
      {(detail.status === 'WAITING_CARGO' || detail.status === 'WAITING_PREDECESSOR') && <Alert style={{ marginBottom: 16 }} type="info" showIcon message={taskStatusText[detail.status]} description={<>此任务已经生成，但目前不是实际在途运输。{detail.waiting_members?.length ? <>等待运单：{detail.waiting_members.map(member => member.shipment_no ?? member.shipment_id).join('、')}。</> : '待货物实际到达本段起点并满足计划时间后，才可发车。'}</>} />}
      {detail.status === 'PENDING_DEPARTURE' && <><Space wrap style={{ marginBottom: 8 }}><Button type="primary" disabled={busy || !detail.allowed_actions.some(item => item.action === 'DEPART' && item.enabled)} onClick={() => runTaskAction('depart')}>确认发车</Button><Button danger disabled={busy || !detail.allowed_actions.some(item => item.action === 'CANCEL' && item.enabled)} onClick={() => setCancelOpen(true)}>取消任务</Button></Space>{['DEPART', 'CANCEL'].map(action => { const reason = disabledActionReason(action); return reason ? <Text key={action} type="secondary" style={{ display: 'block', marginBottom: 4 }}>{reason}</Text> : null })}</>}
      {(detail.status === 'WAITING_CARGO' || detail.status === 'WAITING_PREDECESSOR') && <Button danger disabled={busy || !detail.allowed_actions.some(item => item.action === 'CANCEL' && item.enabled)} onClick={() => setCancelOpen(true)} style={{ marginBottom: 8 }}>取消未来任务</Button>}
      {detail.status === 'IN_TRANSIT' && <><Button type="primary" disabled={busy || !detail.allowed_actions.some(item => item.action === 'ARRIVE' && item.enabled)} onClick={() => runTaskAction('arrive')} style={{ marginBottom: 8 }}>确认到达并全部入站</Button>{disabledActionReason('ARRIVE') && <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>{disabledActionReason('ARRIVE')}</Text>}</>}
      <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
        <Descriptions.Item label="运输线路"><StationName id={detail.origin_station_id} /> → <StationName id={detail.destination_station_id} /></Descriptions.Item>
        <Descriptions.Item label="计划来源">{detail.scheduling_source === 'PLAN' ? '全程运输计划' : '旧版单段任务'}</Descriptions.Item>
        <Descriptions.Item label="任务状态">{taskStatusText[detail.status]}</Descriptions.Item>
        {detail.scheduling_source === 'PLAN' && <Descriptions.Item label="计划发车">{formatTime(detail.planned_departure_at)}</Descriptions.Item>}
        <Descriptions.Item label="预计到达">{formatTime(detail.expected_arrival_at)}</Descriptions.Item>
        {detail.scheduling_source === 'PLAN' && <Descriptions.Item label="最新预测">出发 {formatTime(detail.forecast_departure_at)} · 到达 {formatTime(detail.forecast_arrival_at)}{detail.forecast_stale ? ' · 预测已过期' : ''}</Descriptions.Item>}
        <Descriptions.Item label="实际发车">{formatTime(detail.departed_at)}</Descriptions.Item>
        <Descriptions.Item label="实际到达">{formatTime(detail.arrived_at)}</Descriptions.Item>
        <Descriptions.Item label="延误提示">{detail.delay_status === 'OVERDUE' ? <StatusTag tone="error">{delayText(detail)}</StatusTag> : detail.delay_status === 'LATE_ARRIVAL' ? <StatusTag tone="warning">{delayText(detail)}</StatusTag> : delayText(detail)}</Descriptions.Item>
        {detail.status === 'CANCELLED' && <><Descriptions.Item label="取消时间">{formatTime(detail.cancelled_at)}</Descriptions.Item><Descriptions.Item label="取消原因">{detail.cancel_reason}</Descriptions.Item></>}
        <Descriptions.Item label="关联运单" span={2}><Space wrap>{detail.shipments.map(item => <Link key={item.id} to={'/shipments/' + item.id}><TaskShipmentTag item={item} /></Link>)}</Space></Descriptions.Item>
      </Descriptions>
      <Text type="secondary" style={{ display: 'block', marginTop: 16 }}>演示时间：{formatTime(detail.simulation_time)}。页面操作将按该时钟记录。</Text>
      <ModalForm<{ reason: string }> title="取消运输任务" open={cancelOpen} onOpenChange={open => { setCancelOpen(open); if (!open) { setCancelPreview(undefined); setCancelImpactTasks({}) } }} onValuesChange={() => { setCancelPreview(undefined); setCancelImpactTasks({}) }} submitter={{ submitButtonProps: { loading: cancelPreviewLoading } }} modalProps={{ destroyOnHidden: true }} onFinish={cancelTask}>
        <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={'将解除 ' + detail.shipments.length + ' 张运单的任务占用，货物仍留在起点站。'} />
        <ProFormTextArea name="reason" label="取消原因" rules={[{ required: true, whitespace: true, message: '请填写取消原因' }, { max: 500, message: '最多 500 个字符' }]} fieldProps={{ maxLength: 500, showCount: true, autoSize: { minRows: 3, maxRows: 6 } }} />
      </ModalForm>
      <Modal
        title={actionToConfirm === 'depart' ? '确认任务发车' : '确认任务到达并入站'}
        open={Boolean(actionToConfirm)}
        confirmLoading={actionSaving}
        okText={actionToConfirm === 'depart' ? '确认发车' : '确认到达'}
        cancelText="返回检查"
        onOk={() => void confirmTaskAction()}
        onCancel={() => setActionToConfirm(undefined)}
        okButtonProps={{ disabled: busy }}
      >
        <p>{detail.task_no} · <StationName id={detail.origin_station_id} /> → <StationName id={detail.destination_station_id} /></p>
        <p>本次操作影响 {detail.shipments.length} 张关联运单。</p>
        {actionToConfirm === 'depart' && detail.scheduling_source === 'PLAN' && <Text type="secondary">将按当前任务计划版本 v{detail.schedule_revision} 提交；若共享成员或计划已变化，后端会要求刷新后重新审核。</Text>}
        {actionToConfirm === 'arrive' && <Text type="secondary">确认后，全部关联运单会一次性在目的站入站。</Text>}
      </Modal>
      <Modal title="审核取消影响" open={Boolean(cancelPreview)} confirmLoading={busy} okText="确认取消并解除后续安排" cancelText="返回检查" onCancel={() => setCancelPreview(undefined)} onOk={() => void confirmPlannedCancel()} okButtonProps={{ disabled: busy || cancelPreviewLoading || !cancelPreview }}>
        {cancelPreview && <>
          <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={`将影响 ${cancelPreview.impact.length} 条运输计划关联`} description="取消会解除该趟任务及相关下游待执行安排；尚未发车的货物仍停留在当前位置，系统不会自动重建计划。每张受影响运单需重新审核后续安排。" />
          <Table size="small" pagination={{ pageSize: 8 }} rowKey="association_id" dataSource={cancelPreview.impact} columns={[
            { title: '受影响运单', dataIndex: 'shipment_id', render: id => {
              const shipment = [detail, ...Object.values(cancelImpactTasks)].flatMap(task => task.shipments).find(item => item.id === id)
              return shipment ? <Link to={`/shipments/${shipment.id}`}>{shipment.shipment_no}</Link> : `运单 ${id}`
            } },
            { title: '受影响任务', dataIndex: 'task_id', render: id => {
              const task = id === detail.id ? detail : cancelImpactTasks[id]
              return task ? <Link to={`/tasks/${task.id}`}>{task.task_no} · <StationName id={task.origin_station_id} /> → <StationName id={task.destination_station_id} /></Link> : `任务 ${id}`
            } },
            { title: '当前状态', dataIndex: 'task_status', render: value => taskStatusText[value] ?? value },
            { title: '关联状态', dataIndex: 'association_state', render: value => value === 'PLANNED' ? '未来待执行' : value === 'ACTIVE' ? '当前执行段' : value },
          ]} />
        </>}
      </Modal>
    </>}
  </PageContainer></>
}

export { TaskDetailPage }
export default TasksPage
