import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Card, Checkbox, Form, Modal, Popover, Select, Space, Spin, Tag, Typography } from 'antd'
import { QuestionCircleOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, type ScheduledTripOptions, type SchedulePreview, type SchedulePreviewInput, type ScheduleResponse, type ShipmentDetail, type ShipmentTransportPath, type TransportLine, type TransportRoute } from '../api'
import type { Mutate } from '../shared'
import { apiError, formatTime, StationName } from '../shared'

const { Text } = Typography
type FormValues = { line_id?: string; scheduled_trip_id?: string }
type Props = { shipment: ShipmentDetail; routes: TransportRoute[]; enabled: boolean; busy: boolean; mutate: Mutate; onSaved: () => void }

export function SchedulePlanner({ shipment, routes, enabled, busy, mutate, onSaved }: Props) {
  const [form] = Form.useForm<FormValues>()
  const [options, setOptions] = useState<ShipmentTransportPath>()
  const [lines, setLines] = useState<TransportLine[]>([])
  const [trips, setTrips] = useState<ScheduledTripOptions['items']>([])
  const [recommendedLineId, setRecommendedLineId] = useState<string>()
  const [currentSchedule, setCurrentSchedule] = useState<ScheduleResponse | null>()
  const [preview, setPreview] = useState<SchedulePreview>()
  const [previewFresh, setPreviewFresh] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()
  const [acknowledged, setAcknowledged] = useState<string[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const previewRequest = useRef(0)
  const selectedLineId = Form.useWatch('line_id', form)
  const selectedTripId = Form.useWatch('scheduled_trip_id', form)
  const routeById = useMemo(() => new Map(routes.map(route => [route.id, route])), [routes])
  const warningCodes = preview?.warnings.map(warning => warning.code) ?? []
  const allWarningsAcknowledged = warningCodes.every(code => acknowledged.includes(code))

  useEffect(() => {
    if (!enabled || !selectedLineId) { setTrips([]); setError(undefined); return }
    let active = true
    setLoading(true)
    setTrips([])
    const from = dayjs().format('YYYY-MM-DD')
    const to = dayjs().add(30, 'day').format('YYYY-MM-DD')
    setError(undefined)
    api.shipmentScheduledTrips(shipment.id, from, to).then(result => {
      if (active) {
        const matching = result.items.filter(item => String(item.line_id) === String(selectedLineId)).sort((left, right) => left.departure_at.localeCompare(right.departure_at))
        setTrips(matching)
        const currentTripId = currentSchedule?.scheduled_trip_id ?? shipment.schedule?.scheduled_trip_id
        const preferredTrip = matching.find(trip => String(trip.trip_id) === String(currentTripId)) ?? matching[0]
        if (preferredTrip) form.setFieldValue('scheduled_trip_id', String(preferredTrip.trip_id))
      }
    }).catch(reason => { if (active) { setTrips([]); setError(apiError(reason)) } }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [enabled, shipment.id, selectedLineId, currentSchedule?.scheduled_trip_id, shipment.schedule?.scheduled_trip_id, form])

  useEffect(() => {
    if (!enabled) return
    let active = true
    setLoading(true); setError(undefined); setPreview(undefined); setPreviewFresh(false); setAcknowledged([])
    Promise.all([
      api.shipmentPath(shipment.id),
      api.shipmentSchedule(shipment.id).catch(() => shipment.schedule ?? null),
      api.shipmentLineOptions(shipment.id),
    ]).then(([pathOptions, schedule, lineOptions]) => {
      if (!active) return
      setOptions(pathOptions)
      setLines(lineOptions.lines)
      const currentLineId = schedule?.line_id == null ? undefined : String(schedule.line_id)
      const currentLine = lineOptions.lines.find(line => String(line.id) === currentLineId)
      const defaultLineId = currentLine?.id ?? lineOptions.recommended_line_id ?? (lineOptions.lines.length === 1 ? lineOptions.lines[0].id : undefined)
      setRecommendedLineId(lineOptions.recommended_line_id ?? undefined)
      setCurrentSchedule(schedule)
      form.setFieldsValue({ line_id: defaultLineId == null ? undefined : String(defaultLineId), scheduled_trip_id: undefined })
    }).catch(reason => { if (active) setError(apiError(reason)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [enabled, shipment.id, shipment.schedule, shipment.destination_station_id, shipment.transport_path?.anchor_station_id, form])

  const requestBody = useCallback((values: FormValues): SchedulePreviewInput => {
    if (!options) throw new Error('路径选项尚未加载')
    const body: SchedulePreviewInput = {
      expected_path_version: shipment.path_version ?? options.version,
      expected_schedule_version: currentSchedule?.version ?? shipment.schedule?.version ?? 0,
      expected_destination_station_id: Number(shipment.destination_station_id),
    }
    const selectedTrip = trips.find(trip => String(trip.trip_id) === String(values.scheduled_trip_id))
    if (!selectedTrip) throw new Error('请选择一个已排定的发车班次')
    if (String(selectedTrip.line_id) !== String(values.line_id)) throw new Error('所选车次与运输线路不匹配，请重新选择')
    body.scheduled_trip_id = Number(values.scheduled_trip_id)
    return body
  }, [options, trips, shipment.path_version, shipment.destination_station_id, currentSchedule?.version, shipment.schedule?.version])

  const runPreview = useCallback(async () => {
    if (!options) return
    const requestId = ++previewRequest.current
    let values: FormValues
    try { values = await form.validateFields() } catch { return }
    let body: SchedulePreviewInput
    try { body = requestBody(values) } catch (reason) { setError(reason instanceof Error ? reason.message : '请检查路线输入'); return }
    setPreviewLoading(true); setError(undefined)
    try {
      const next = await api.previewShipmentSchedule(shipment.id, body)
      if (requestId === previewRequest.current) { setPreview(next); setPreviewFresh(true); setAcknowledged([]) }
    } catch (reason) {
      if (requestId === previewRequest.current) { setError(apiError(reason)); setPreviewFresh(false) }
    } finally {
      if (requestId === previewRequest.current) setPreviewLoading(false)
    }
  }, [options, form, shipment.id, requestBody])

  useEffect(() => {
    if (!enabled || !modalOpen || !selectedTripId || !options || !trips.some(trip => String(trip.trip_id) === String(selectedTripId))) {
      if (!selectedTripId) { previewRequest.current += 1; setPreview(undefined); setPreviewFresh(false); setPreviewLoading(false) }
      return
    }
    void runPreview()
  }, [enabled, modalOpen, selectedTripId, options, trips, runPreview])

  const confirm = async () => {
    if (!preview || !previewFresh || !preview.can_confirm || !allWarningsAcknowledged) return
    const reason = '按所选班次确认全程运输计划'
    const result = await mutate(`schedule-confirm:${shipment.id}:${preview.schedule_version}:${preview.preview_token}`, key => api.confirmShipmentSchedule(shipment.id, { preview_token: preview.preview_token, reason, acknowledged_warning_codes: acknowledged }, key), '全程运输计划已确认，全部分段任务已生成')
    if (result) { setModalOpen(false); onSaved() }
  }

  const selectedTrip = trips.find(trip => String(trip.trip_id) === String(selectedTripId))
  const tripDetails = selectedTrip ? (() => {
    const stationIds = selectedTrip.legs.length ? [selectedTrip.legs[0].origin_station_id, ...selectedTrip.legs.map(leg => leg.destination_station_id)] : []
    return <Space direction="vertical" size={6}>{stationIds.map((stationId, position) => <div key={stationId}><StationName id={stationId} /> 到 {formatTime(position > 0 ? selectedTrip.legs[position - 1]?.planned_arrival_at : null)} · 发 {formatTime(position < selectedTrip.legs.length ? selectedTrip.legs[position]?.planned_departure_at : null)}</div>)}</Space>
  })() : '先选择班次'

  const currentLine = lines.find(line => String(line.id) === String(currentSchedule?.line_id))
  const currentDeparture = currentSchedule?.legs.find(leg => leg.planned_departure_at)?.planned_departure_at
  const currentArrival = [...(currentSchedule?.legs ?? [])].reverse().find(leg => leg.planned_arrival_at)?.planned_arrival_at
  const scheduleStatusLabel: Record<string, string> = { NOT_CONFIRMED: '待安排', CONFIRMED: '已确认', NEEDS_RECONFIRMATION: '需要重新审核', BLOCKED: '计划受阻', COMPLETED: '已完成' }
  const openPlanner = () => {
    const activeLineId = currentSchedule?.line_id == null ? undefined : String(currentSchedule.line_id)
    const currentLineIsAvailable = lines.some(line => String(line.id) === activeLineId)
    const lineId = currentLineIsAvailable ? activeLineId : recommendedLineId ?? form.getFieldValue('line_id')
    form.setFieldsValue({ line_id: lineId == null ? undefined : String(lineId), scheduled_trip_id: currentSchedule?.scheduled_trip_id == null ? trips[0]?.trip_id : String(currentSchedule.scheduled_trip_id) })
    setPreview(undefined)
    setPreviewFresh(false)
    setAcknowledged([])
    setError(undefined)
    setModalOpen(true)
  }

  return <>
    <Card size="small" style={{ marginTop: 16 }}>
      <Space style={{ display: 'flex', justifyContent: 'space-between' }} align="center" wrap>
        <Space direction="vertical" size={2}>
          <Space><Text strong>全程运输计划</Text><Tag color={currentSchedule?.status === 'CONFIRMED' ? 'green' : currentSchedule?.status === 'BLOCKED' ? 'red' : 'blue'}>{scheduleStatusLabel[currentSchedule?.status ?? ''] ?? (loading ? '加载中' : '待安排')}</Tag></Space>
          <Text type="secondary">{currentLine ? currentLine.stations.map(station => station.name).join(' → ') : currentSchedule?.legs.length ? `${currentSchedule.legs[0].route_code} · ${currentSchedule.legs.length} 段` : '尚未选择运输班次'}{currentDeparture || currentArrival ? ` · ${formatTime(currentDeparture)} → ${formatTime(currentArrival)}` : ''}</Text>
        </Space>
        <Button type={currentSchedule?.status === 'CONFIRMED' ? 'default' : 'primary'} disabled={!enabled || loading} onClick={openPlanner}>{currentSchedule?.status === 'CONFIRMED' ? '修改班次' : '安排运输计划'}</Button>
      </Space>
    </Card>
    <Modal title={currentSchedule?.status === 'CONFIRMED' ? '修改运输班次' : '安排运输计划'} open={modalOpen} onCancel={() => setModalOpen(false)} footer={null} width={920} destroyOnHidden styles={{ body: { maxHeight: 'calc(100vh - 180px)', overflowY: 'auto' } }}>
      {error && <Alert type="error" showIcon message="计划操作失败" description={error} style={{ marginBottom: 16 }} />}
      {loading && !lines.length && <Spin tip="正在加载运输线路和班次…" />}
      <Form form={form} layout="vertical" disabled={!enabled} onValuesChange={changed => {
        setError(undefined)
        if ('line_id' in changed) form.setFieldValue('scheduled_trip_id', undefined)
        if (preview && Object.keys(changed).length) setPreviewFresh(false)
      }}>
        {lines.length > 0 ? <Form.Item name="line_id" label="运输线路" rules={[{ required: true, message: '请选择一条运输线路' }]}><Select showSearch optionFilterProp="label" placeholder="请选择运输线路" options={lines.map(line => ({ value: String(line.id), label: `${line.stations.map(station => station.name).join(' → ')} · ${line.code} · ${line.name}`, line }))} optionRender={option => {
          const line = option.data.line
          return <Space direction="vertical" size={0}><Text strong>{line.stations.map(station => `${station.name}（${station.code}）`).join(' → ')}{recommendedLineId === line.id && <Tag color="blue" style={{ marginInlineStart: 8 }}>推荐</Tag>}</Text><Text type="secondary">{line.code} · {line.name} · {line.legs.length} 段 · 参考 {line.total_reference_minutes ?? '待补'} 分钟</Text></Space>
        }} /></Form.Item> : !loading && <Alert type="warning" showIcon message="没有匹配的启用运输线路" description="请先在网络配置的运输线路中配置当前起点到目的站的完整线路。" />}
        {selectedLineId && <>
          <Space align="start" style={{ display: 'flex' }}>
            <Form.Item name="scheduled_trip_id" label="选择可搭乘车次" rules={[{ required: true, message: '请选择一个班次' }]} style={{ flex: 1 }}>
              <Select showSearch optionFilterProp="label" placeholder={loading ? '正在查询可搭乘班次…' : '请选择班次'} disabled={!trips.length} options={trips.map((trip, index) => ({ value: String(trip.trip_id), label: `${formatTime(trip.departure_at).slice(0, 10)} · ${trip.service_name} · ${formatTime(trip.departure_at)}${index === 0 ? ' · 最近发车' : ''}` }))} />
            </Form.Item>
            <Popover title="班次站点时刻" content={tripDetails} trigger="click"><Button aria-label="查看班次路线和时刻" type="text" icon={<QuestionCircleOutlined />} style={{ marginTop: 30 }} disabled={!selectedTrip} /></Popover>
          </Space>
          {!trips.length && <Text type="secondary">{loading ? '正在查询当前可赶上的车次…' : '未来 31 天没有匹配起终站且尚未发车的班次。请检查线路班次配置。'}</Text>}
        </>}
        {previewLoading && !preview && <Alert type="info" showIcon style={{ marginTop: 16 }} message="正在核验所选车次，可以稍候直接确认。" />}
        {preview && <>
          <Alert type="info" showIcon style={{ marginBottom: 12 }} message={`未来路径：${preview.legs.length ? `${routeById.get(preview.legs[0].route_id)?.origin.code ?? ''} ${preview.legs.map(leg => `→ ${routeById.get(leg.route_id)?.destination.code ?? leg.destination_station_id}`).join(' ')}` : '无未来路段'}`} description={<>接续站：<StationName id={preview.anchor_station_id} />；目的站：<StationName id={preview.destination_station_id} />。冻结路段不会被本次审核覆盖。</>} />
          {!previewFresh && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={previewLoading ? '正在自动更新所选车次的审核结果…' : '所选班次已变化，正在准备新的审核结果。'} />}
          {(preview.missing.length > 0 || preview.replacements.length > 0) && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={preview.missing.length ? '参考时间缺失或计划尚未完整' : '确认时将替换未执行的专属安排'} description={<Space direction="vertical">{preview.missing.map((item, index) => <span key={index}>{String(item.message ?? item.code ?? '请补全计划时间')}</span>)}{preview.replacements.map(item => <span key={item.id}>将替换任务 {item.task_id}{item.task_no ? ` · ${item.task_no}` : ''}</span>)}</Space>} />}
          {preview.legs.some(leg => leg.shared_members.length > 0) && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="拟共享已有运输任务" description={preview.legs.filter(leg => leg.shared_members.length > 0).map(leg => `${leg.route_code}：${leg.shared_members.join('、')}`).join('；')} />}
          {preview.warnings.map(warning => <Checkbox key={warning.code} checked={acknowledged.includes(warning.code)} onChange={event => setAcknowledged(current => event.target.checked ? [...current, warning.code] : current.filter(code => code !== warning.code))} style={{ display: 'block', margin: '8px 0' }}>我已审核并接受：{warning.message}</Checkbox>)}
          {!preview.can_confirm && <Alert type="error" showIcon style={{ marginBottom: 12 }} message="当前预览还不能确认" description="请补全所有时间，或先处理不能由单张运单替换的共享安排，再重新预览。" />}
          <Space style={{ display: 'flex', justifyContent: 'flex-end' }}><Button onClick={() => setModalOpen(false)}>取消</Button><Button type="primary" loading={busy || previewLoading} disabled={!previewFresh || !preview.can_confirm || !allWarningsAcknowledged} onClick={() => void confirm()}>{currentSchedule?.status === 'CONFIRMED' ? '确认修改班次' : '确认班次'}</Button></Space>
        </>}
      </Form>
    </Modal>
  </>
}
