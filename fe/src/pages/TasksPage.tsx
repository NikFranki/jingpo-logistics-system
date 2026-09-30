import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Form, Space, Spin, Typography, message } from 'antd'
import { ArrowLeftOutlined, PlusOutlined } from '@ant-design/icons'
import { ModalForm, PageContainer, ProFormDateTimePicker, ProFormSelect, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import dayjs from 'dayjs'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type Candidate, type RouteCode, type TaskDetail, type TaskItem } from '../api'
import { apiError, delayText, formatTime, StatusTag, stageText, taskStatusText, TaskShipmentTag, type Shared, useNetwork, StationName } from '../shared'

const { Text } = Typography

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

export { TaskDetailPage }
export default TasksPage
