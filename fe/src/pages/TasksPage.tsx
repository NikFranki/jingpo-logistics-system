import { fuzzySelectFilter } from '../fuzzySearch'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Modal, Space, Spin, Table, Typography, message } from 'antd'
import { ModalForm, PageContainer, ProFormTextArea, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import { Link, useParams } from 'react-router-dom'
import { api, type CancelPreview, type TaskDetail, type TaskItem } from '../api'
import { apiError, delayText, formatTime, formatTimeWithSeconds, StatusTag, taskStatusText, TaskShipmentTag, type Shared, useNetwork, StationName } from '../shared'

const { Text } = Typography
const isScheduledTask = (source?: string) => source === 'SCHEDULE' || source === 'PLAN'

function TasksPage({ revision }: Pick<Shared, 'revision'>) {
  const network = useNetwork(revision)
  const actionRef = useRef<ActionType | undefined>(undefined)
  const [loadError, setLoadError] = useState<string>()
  useEffect(() => { actionRef.current?.reload() }, [revision])
  const columns: ProColumns<TaskItem>[] = [
    { title: '任务号', dataIndex: 'task_no', copyable: true },
    { title: '线路', dataIndex: 'route_code', fieldProps: { showSearch: true, filterOption: fuzzySelectFilter }, valueEnum: Object.fromEntries(network.routes.map(r => [r.code, { text: `${r.code} · ${r.origin.code} → ${r.destination.code}${r.enabled ? '' : '（停用）'}` }])) },
    { title: '计划来源', dataIndex: 'scheduling_source', search: false, render: value => value === 'SCHEDULE' || value === 'PLAN' ? '全程计划' : '旧版任务' },
    { title: '任务状态', dataIndex: 'status', fieldProps: { showSearch: true, filterOption: fuzzySelectFilter }, valueEnum: Object.fromEntries(Object.entries(taskStatusText).map(([key, text]) => [key, { text }])) },
    { title: '计划发车', dataIndex: 'planned_departure_at', valueType: 'dateTime', search: false, render: (_, row) => row.planned_departure_at ? formatTime(row.planned_departure_at) : '—' },
    { title: '预计到达', dataIndex: 'expected_arrival_at', search: false, render: (_, row) => formatTime(row.expected_arrival_at) },
    { title: '最新预测', dataIndex: 'forecast_arrival_at', valueType: 'dateTime', search: false, render: (_, row) => <>{formatTime(row.forecast_arrival_at)}{row.forecast_stale && <Text type="warning"> · 预测已过期</Text>}</> },
    { title: '实际到达', dataIndex: 'arrived_at', valueType: 'dateTime', search: false },
    { title: '取消时间', dataIndex: 'cancelled_at', valueType: 'dateTime', search: false },
    { title: '取消原因', dataIndex: 'cancel_reason', search: false, ellipsis: true },
    { title: '延误提示', dataIndex: 'delay_status', search: false, render: (_, row) => row.delay_status === 'OVERDUE' ? <StatusTag tone="error">{delayText(row)}</StatusTag> : row.delay_status === 'LATE_ARRIVAL' ? <StatusTag tone="warning">{delayText(row)}</StatusTag> : delayText(row) },
    { title: '操作', valueType: 'option', render: (_, row) => <Link to={`/tasks/${row.id}`}>查看详情</Link> },
  ]
  return <PageContainer title="运输任务" subTitle="查看由已确认运输计划生成的分段任务，并记录实际发车和到达。">
    {network.error && <Alert type="error" message={network.error} style={{ marginBottom: 16 }} />}{loadError && <Alert type="error" showIcon message="运输任务列表加载失败" description={loadError} action={<Button size="small" onClick={() => actionRef.current?.reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <ProTable<TaskItem> actionRef={actionRef} rowKey="id" columns={columns} search={{ labelWidth: 90 }} pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }} request={async params => {
      try { const result = await api.tasks({ page: params.current ?? 1, page_size: params.pageSize ?? 20, task_no: params.task_no as string, route_code: params.route_code as string, status: params.status as string }); setLoadError(undefined); return { data: result.items, success: true, total: result.total } }
      catch (error) { const reason = apiError(error); setLoadError(reason); return { data: [], success: false, total: 0 } }
    }} />
  </PageContainer>
}

function TaskDetailPage({ revision, busy, mutate }: Shared) {
  const { taskId = '' } = useParams()
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
    const result = await mutate('task-action:' + detail.id + ':' + action + ':' + detail.schedule_revision, key => api.taskAction(detail.id, action, key, action === 'depart' && isScheduledTask(detail.scheduling_source) ? detail.schedule_revision : undefined), action === 'depart' ? '任务已发车' : '任务已到达，全部关联运单已入站')
    setActionSaving(false)
    if (result) setActionToConfirm(undefined)
  }
  const cancelTask = async (values: { reason: string }) => {
    if (!detail) return false
    if (isScheduledTask(detail.scheduling_source)) {
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
    if (!rule || rule.enabled || !rule.reason) return undefined
    return rule.reason.replace(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})/g, formatTimeWithSeconds)
  }

  return <>{holder}<PageContainer title={detail?.task_no ?? '运输任务详情'} subTitle="查看运输安排、执行任务，或在发车前取消错误安排。" breadcrumb={{ items: [{ title: <Link to="/tasks">运输任务列表</Link> }, { title: detail?.task_no ?? '运输任务详情' }] }}>
    {loadError && <Alert type="error" showIcon message="运输任务详情加载失败" description={loadError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} style={{ marginBottom: 16 }} />}
    {loading && !detail ? <Spin /> : detail && <>
      {detail.status === 'ARRIVED' && <Alert style={{ marginBottom: 16 }} type="success" showIcon message={<>任务已到达：<Text strong><StationName id={detail.destination_station_id} /></Text></>} />}
      {detail.status === 'CANCELLED' && <Alert style={{ marginBottom: 16 }} type="warning" showIcon message="运输任务已取消，关联运单仍在起点站且已解除占用。" description={<>取消时间：{formatTime(detail.cancelled_at)}。取消原因：{detail.cancel_reason || '—'}</>} />}
      {(detail.status === 'WAITING_CARGO' || detail.status === 'WAITING_PREDECESSOR') && <Alert style={{ marginBottom: 16 }} type="info" showIcon message={taskStatusText[detail.status]} description={<>此任务已经生成，但目前不是实际在途运输。{detail.waiting_members?.length ? <>等待运单：{detail.waiting_members.map(member => member.shipment_no ?? member.shipment_id).join('、')}。</> : '待货物实际到达本段起点或前段实际到达后，才可发车。'}</>} />}
      {isScheduledTask(detail.scheduling_source) && (detail.status === 'PENDING_DEPARTURE' || detail.status === 'IN_TRANSIT') && <Alert style={{ marginBottom: 16 }} type="info" showIcon message="测试时可提前发车和确认到达" description="操作时间按服务器实际时间记录；发车仍要求运单已到达本段起点或前段已实际到达。" />}
      {detail.status === 'PENDING_DEPARTURE' && <><Space wrap style={{ marginBottom: 8 }}><Button type="primary" disabled={busy || !detail.allowed_actions.some(item => item.action === 'DEPART' && item.enabled)} onClick={() => runTaskAction('depart')}>确认发车</Button><Button danger disabled={busy || !detail.allowed_actions.some(item => item.action === 'CANCEL' && item.enabled)} onClick={() => setCancelOpen(true)}>取消任务</Button></Space>{['DEPART', 'CANCEL'].map(action => { const reason = disabledActionReason(action); return reason ? <Text key={action} type="secondary" style={{ display: 'block', marginBottom: 4 }}>{reason}</Text> : null })}</>}
      {(detail.status === 'WAITING_CARGO' || detail.status === 'WAITING_PREDECESSOR') && <Button danger disabled={busy || !detail.allowed_actions.some(item => item.action === 'CANCEL' && item.enabled)} onClick={() => setCancelOpen(true)} style={{ marginBottom: 8 }}>取消未来任务</Button>}
      {detail.status === 'IN_TRANSIT' && <><Button type="primary" disabled={busy || !detail.allowed_actions.some(item => item.action === 'ARRIVE' && item.enabled)} onClick={() => runTaskAction('arrive')} style={{ marginBottom: 8 }}>确认到达并全部入站</Button>{disabledActionReason('ARRIVE') && <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>{disabledActionReason('ARRIVE')}</Text>}</>}
      <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
        <Descriptions.Item label="运输线路"><StationName id={detail.origin_station_id} /> → <StationName id={detail.destination_station_id} /></Descriptions.Item>
        <Descriptions.Item label="计划来源">{isScheduledTask(detail.scheduling_source) ? '全程运输计划' : '历史任务'}</Descriptions.Item>
        <Descriptions.Item label="任务状态">{taskStatusText[detail.status]}</Descriptions.Item>
        {isScheduledTask(detail.scheduling_source) && <Descriptions.Item label="计划发车">{formatTime(detail.planned_departure_at)}</Descriptions.Item>}
        <Descriptions.Item label="预计到达">{formatTime(detail.expected_arrival_at)}</Descriptions.Item>
        {isScheduledTask(detail.scheduling_source) && <Descriptions.Item label="最新预测">出发 {formatTime(detail.forecast_departure_at)} · 到达 {formatTime(detail.forecast_arrival_at)}{detail.forecast_stale ? ' · 预测已过期' : ''}</Descriptions.Item>}
        <Descriptions.Item label="实际发车">{formatTime(detail.departed_at)}</Descriptions.Item>
        <Descriptions.Item label="实际到达">{formatTime(detail.arrived_at)}</Descriptions.Item>
        <Descriptions.Item label="延误提示">{detail.delay_status === 'OVERDUE' ? <StatusTag tone="error">{delayText(detail)}</StatusTag> : detail.delay_status === 'LATE_ARRIVAL' ? <StatusTag tone="warning">{delayText(detail)}</StatusTag> : delayText(detail)}</Descriptions.Item>
        {detail.status === 'CANCELLED' && <><Descriptions.Item label="取消时间">{formatTime(detail.cancelled_at)}</Descriptions.Item><Descriptions.Item label="取消原因">{detail.cancel_reason}</Descriptions.Item></>}
        <Descriptions.Item label="关联运单" span={2}><Space wrap>{detail.shipments.map(item => <Link key={item.id} to={'/shipments/' + item.id}><TaskShipmentTag item={item} /></Link>)}</Space></Descriptions.Item>
      </Descriptions>
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
        {actionToConfirm === 'depart' && isScheduledTask(detail.scheduling_source) && <Text type="secondary">将按当前任务计划版本 v{detail.schedule_revision} 提交；若共享成员或计划已变化，后端会要求刷新后重新审核。</Text>}
        {actionToConfirm === 'arrive' && <Text type="secondary">确认后，全部关联运单会一次性在目的站入站。</Text>}
      </Modal>
      <Modal title="审核取消影响" open={Boolean(cancelPreview)} confirmLoading={busy} okText="确认取消并解除后续安排" cancelText="返回检查" onCancel={() => setCancelPreview(undefined)} onOk={() => void confirmPlannedCancel()} okButtonProps={{ disabled: busy || cancelPreviewLoading || !cancelPreview }}>
        {cancelPreview && <>
          <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={`将影响 ${cancelPreview.impact.length} 条运输计划关联`} description="取消会解除该趟任务及相关下游待执行安排；尚未发车的货物仍停留在当前位置，系统不会自动重建计划。每张受影响运单需重新审核后续安排。" />
          <Table size="small" pagination={{ defaultPageSize: 8 }} rowKey="association_id" dataSource={cancelPreview.impact} columns={[
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
