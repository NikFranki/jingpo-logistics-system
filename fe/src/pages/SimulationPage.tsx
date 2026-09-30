import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Empty, Form, Select, Space, Tag, Typography, message } from 'antd'
import { ClockCircleOutlined, SwapOutlined, TruckOutlined } from '@ant-design/icons'
import { PageContainer } from '@ant-design/pro-components'
import { api, type Shipment, type ShipmentDetail, type TaskDetail, type TaskItem } from '../api'
import { actionText, allowed, apiError, delayText, formatTime, stageText, StatusTag, taskStatusText, type Shared, StationName, useNetwork } from '../shared'

const { Text, Title } = Typography

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

export default SimulationPage
