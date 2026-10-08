import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Descriptions, Form, Input, Modal, Radio, Space, Table, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { api, type ScheduledTripOptions, type SchedulePreview, type SchedulePreviewInput, type ScheduleResponse, type ShipmentDetail, type ShipmentTransportPath, type TransportLine, type TransportRoute } from '../api'
import type { Mutate } from '../shared'
import { apiError, formatTime, StationName } from '../shared'

const { Text } = Typography
const legStateText: Record<string, string> = { ARRIVED: '已到达', IN_TRANSIT: '运输中' }
type FormValues = { line_id?: string; scheduled_trip_id?: string; reason?: string }
type Props = { shipment: ShipmentDetail; routes: TransportRoute[]; open: boolean; busy: boolean; mutate: Mutate; onClose: () => void; onSaved: () => void; autoPreview?: boolean }

export function SchedulePlanner({ shipment, routes, open, busy, mutate, onClose, onSaved, autoPreview = false }: Props) {
  const [form] = Form.useForm<FormValues>()
  const [options, setOptions] = useState<ShipmentTransportPath>()
  const [lines, setLines] = useState<TransportLine[]>([])
  const [trips, setTrips] = useState<ScheduledTripOptions['items']>([])
  const [recommendedLineId, setRecommendedLineId] = useState<string>()
  const [currentSchedule, setCurrentSchedule] = useState<ScheduleResponse | null>()
  const [preview, setPreview] = useState<SchedulePreview>()
  const [previewFresh, setPreviewFresh] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()
  const [acknowledged, setAcknowledged] = useState<string[]>([])
  const [autoPreviewPending, setAutoPreviewPending] = useState(false)
  const autoPreviewStarted = useRef(false)
  const selectedLineId = Form.useWatch('line_id', form)
  const reason = Form.useWatch('reason', form)
  const routeById = useMemo(() => new Map(routes.map(route => [route.id, route])), [routes])
  const warningCodes = preview?.warnings.map(warning => warning.code) ?? []
  const allWarningsAcknowledged = warningCodes.every(code => acknowledged.includes(code))

  useEffect(() => {
    if (!open || !selectedLineId) { setTrips([]); setError(undefined); return }
    let active = true
    setLoading(true)
    setTrips([])
    const from = dayjs().format('YYYY-MM-DD')
    const to = dayjs().add(30, 'day').format('YYYY-MM-DD')
    setError(undefined)
    api.shipmentScheduledTrips(shipment.id, from, to).then(result => {
      if (active) {
        const matching = result.items.filter(item => item.line_id === selectedLineId).sort((left, right) => left.departure_at.localeCompare(right.departure_at))
        setTrips(matching)
        if (matching.length) form.setFieldValue('scheduled_trip_id', matching[0].trip_id)
        if (autoPreview && matching.length === 1) {
          form.setFieldValue('scheduled_trip_id', matching[0].trip_id)
          setAutoPreviewPending(true)
        }
      }
    }).catch(reason => { if (active) { setTrips([]); setError(apiError(reason)) } }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [open, shipment.id, selectedLineId, autoPreview, form])

  useEffect(() => {
    if (!open) return
    autoPreviewStarted.current = false
    setAutoPreviewPending(false)
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
      const defaultLineId = lineOptions.recommended_line_id ?? (lineOptions.lines.length === 1 ? lineOptions.lines[0].id : undefined)
      setRecommendedLineId(lineOptions.recommended_line_id ?? undefined)
      setCurrentSchedule(schedule)
      form.setFieldsValue({ line_id: defaultLineId, scheduled_trip_id: undefined, reason: '' })
      setAutoPreviewPending(false)
    }).catch(reason => { if (active) setError(apiError(reason)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [open, shipment.id, shipment.schedule, shipment.destination_station_id, shipment.transport_path?.anchor_station_id, form, autoPreview])

  const requestBody = useCallback((values: FormValues): SchedulePreviewInput => {
    if (!options) throw new Error('路径选项尚未加载')
    const body: SchedulePreviewInput = {
      expected_path_version: shipment.path_version ?? options.version,
      expected_schedule_version: currentSchedule?.version ?? shipment.schedule?.version ?? 0,
      expected_destination_station_id: Number(shipment.destination_station_id),
    }
    if (!lines.some(item => item.id === values.line_id)) throw new Error('请选择一条可用运输线路')
    if (!values.scheduled_trip_id) throw new Error('请选择一个已排定的发车班次')
    body.scheduled_trip_id = Number(values.scheduled_trip_id)
    return body
  }, [options, lines, shipment.path_version, shipment.destination_station_id, currentSchedule?.version, shipment.schedule?.version])

  const runPreview = useCallback(async () => {
    if (!options) return
    let values: FormValues
    try { values = await form.validateFields() } catch { return }
    let body: SchedulePreviewInput
    try { body = requestBody(values) } catch (reason) { setError(reason instanceof Error ? reason.message : '请检查路线输入'); return }
    setLoading(true); setError(undefined)
    try {
      const next = await api.previewShipmentSchedule(shipment.id, body)
      setPreview(next); setPreviewFresh(true); setAcknowledged([])
    } catch (reason) { setError(apiError(reason)); setPreviewFresh(false) }
    finally { setLoading(false) }
  }, [options, form, shipment.id, requestBody, preview])

  useEffect(() => {
    if (!open || !autoPreviewPending || !options || autoPreviewStarted.current) return
    autoPreviewStarted.current = true
    setAutoPreviewPending(false)
    void runPreview()
  }, [open, autoPreviewPending, options, runPreview])

  const confirm = async () => {
    if (!preview || !previewFresh || !preview.can_confirm || !allWarningsAcknowledged) return
    const reason = String(form.getFieldValue('reason') ?? '').trim()
    if (!reason) { form.setFields([{ name: 'reason', errors: ['请填写计划确认原因'] }]); return }
    const result = await mutate(`schedule-confirm:${shipment.id}:${preview.schedule_version}:${preview.preview_token}`, key => api.confirmShipmentSchedule(shipment.id, { preview_token: preview.preview_token, reason, acknowledged_warning_codes: acknowledged }, key), '全程运输计划已确认，全部分段任务已生成')
    if (result) { onSaved(); onClose() }
  }

  return <Modal title="审核全程运输计划" open={open} onCancel={onClose} width={980} destroyOnHidden footer={null} styles={{ body: { maxHeight: 'calc(100vh - 180px)', overflowY: 'auto' } }}>
    {error && <Alert type="error" showIcon message="计划操作失败" description={error} style={{ marginBottom: 16 }} />}
    <Form form={form} layout="vertical" onValuesChange={changed => {
      if ('line_id' in changed) form.setFieldValue('scheduled_trip_id', undefined)
      if (preview && Object.keys(changed).some(key => key !== 'reason')) setPreviewFresh(false)
    }}>
      <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="运单">{shipment.shipment_no}</Descriptions.Item>
        <Descriptions.Item label="计划目的站"><StationName id={shipment.destination_station_id} /></Descriptions.Item>
        {options?.anchor_station_id ? <Descriptions.Item label="实际接续站"><StationName id={options.anchor_station_id} /></Descriptions.Item> : <Descriptions.Item label="计划起点">{lines.find(line => line.id === selectedLineId) ? <StationName id={lines.find(line => line.id === selectedLineId)!.origin_station_id} /> : '选择运输线路后带出'}</Descriptions.Item>}
        <Descriptions.Item label="冻结路段">{options?.legs.filter(leg => leg.state === 'ARRIVED' || leg.state === 'IN_TRANSIT').map(leg => `${leg.route_code}（${legStateText[leg.state]}）`).join(' → ') || '暂无'}</Descriptions.Item>
      </Descriptions>
      {lines.length > 0 ? <Form.Item name="line_id" label="运输线路" rules={[{ required: true, message: '请选择一条运输线路' }]}><Radio.Group style={{ display: 'grid', gap: 8 }} options={lines.map(line => ({ value: line.id, label: <span><Text strong>{line.stations.map(station => `${station.name}（${station.code}）`).join(' → ')}</Text>{recommendedLineId === line.id && <Tag color="blue" style={{ marginInlineStart: 8 }}>推荐</Tag>}<br /><Text type="secondary">{line.code} · {line.name} · {line.legs.length} 段 · 参考 {line.total_reference_minutes ?? '待补'} 分钟</Text></span> }))} /></Form.Item> : <Alert type="warning" showIcon message="没有匹配的启用运输线路" description="请先在网络配置的运输线路中配置当前起点到目的站的完整线路。" />}
      {selectedLineId && <>
        <Form.Item name="scheduled_trip_id" label="选择可搭乘车次" rules={[{ required: true, message: '请选择一个班次' }]}>
          {trips.length ? <Radio.Group style={{ display: 'grid', gap: 8 }} options={trips.map((trip, index) => ({ value: trip.trip_id, label: <span><Text strong>{trip.service_name}</Text>{index === 0 && <Tag color="blue" style={{ marginInlineStart: 8 }}>最近发车</Tag>}<br /><Text type="secondary">{dayjs(trip.departure_at).format('YYYY-MM-DD HH:mm')} → {dayjs(trip.arrival_at).format('YYYY-MM-DD HH:mm')}</Text></span> }))} /> : <Text type="secondary">{loading ? '正在查询当前可赶上的车次…' : '未来 31 天没有匹配起终站且尚未发车的班次。单次临时车次创建暂未开放，请检查线路班次配置。'}</Text>}
        </Form.Item>
      </>}
      {!preview && <Space style={{ marginTop: 16 }}><Button type="primary" loading={loading} disabled={busy || loading || !trips.some(trip => trip.trip_id === form.getFieldValue('scheduled_trip_id'))} onClick={() => void runPreview()}>生成班次预览</Button><Text type="secondary">各段时间直接采用已选班次的逐站时刻；这里只需审核确认。</Text></Space>}
        {preview && <>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginTop: 20, marginBottom: 10 }}><Text strong>逐站计划时间</Text><Button onClick={() => void runPreview()} loading={loading} disabled={busy || loading}>重新计算预览</Button></div>
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message={`未来路径：${preview.legs.length ? `${routeById.get(preview.legs[0].route_id)?.origin.code ?? ''} ${preview.legs.map(leg => `→ ${routeById.get(leg.route_id)?.destination.code ?? leg.destination_station_id}`).join(' ')}` : '无未来路段'}`} description={<>接续站：<StationName id={preview.anchor_station_id} />；目的站：<StationName id={preview.destination_station_id} />。冻结路段不会被本次审核覆盖。</>} />
        <Alert type={previewFresh ? 'info' : 'warning'} showIcon style={{ marginBottom: 12 }} message={previewFresh ? '这是当前所选班次对应的最新预览。更换班次后，请重新计算预览再确认。' : '班次或路线已修改，预览凭据已失效；重新计算后才能确认。'} />
        {(preview.missing.length > 0 || preview.replacements.length > 0) && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={preview.missing.length ? '参考时间缺失或计划尚未完整' : '确认时将替换未执行的专属安排'} description={<Space direction="vertical">{preview.missing.map((item, index) => <span key={index}>{String(item.message ?? item.code ?? '请补全计划时间')}</span>)}{preview.replacements.map(item => <span key={item.id}>将替换任务 {item.task_id}{item.task_no ? ` · ${item.task_no}` : ''}</span>)}</Space>} />}
        <div style={{ overflowX: 'auto' }}><Table size="small" pagination={false} rowKey={row => String(row.position)} dataSource={preview.legs} columns={[
          { title: '段', width: 54, render: (_, row) => row.position + 1 },
          { title: '站点区间 / 参考', render: (_, row) => <><Text strong>{row.route_code}</Text><br /><Text><StationName id={row.origin_station_id} /> → <StationName id={row.destination_station_id} /></Text><br /><Text type="secondary">运输参考 {row.travel_reference_minutes ?? '缺失'} 分钟；中转参考 {row.transfer_reference_minutes ?? '缺失'} 分钟</Text>{row.shared_members.length > 0 && <Alert type="warning" showIcon message={`拟共享任务，当前成员：${row.shared_members.join('、')}`} />}</> },
          { title: '计划出发（北京时间）', width: 180, render: (_, row) => formatTime(row.planned_departure_at) },
          { title: '计划到达（北京时间）', width: 180, render: (_, row) => formatTime(row.planned_arrival_at) },
        ]} /></div>
        {preview.warnings.map(warning => <Checkbox key={warning.code} checked={acknowledged.includes(warning.code)} onChange={event => setAcknowledged(current => event.target.checked ? [...current, warning.code] : current.filter(code => code !== warning.code))} style={{ display: 'block', margin: '8px 0' }}>我已审核并接受：{warning.message}</Checkbox>)}
        <Form.Item name="reason" label="计划确认原因" rules={[{ required: true, whitespace: true, message: '请填写计划确认原因' }, { max: 500, message: '最多 500 个字符' }]}><Input.TextArea maxLength={500} showCount rows={2} placeholder="例如：按本次订单时效确认全程班次" /></Form.Item>
        {!preview.can_confirm && <Alert type="error" showIcon style={{ marginBottom: 12 }} message="当前预览还不能确认" description="请补全所有时间，或先处理不能由单张运单替换的共享安排，再重新预览。" />}
        <Space style={{ display: 'flex', justifyContent: 'flex-end' }}><Button onClick={onClose}>取消</Button><Button type="primary" loading={busy} disabled={!previewFresh || !preview.can_confirm || !allWarningsAcknowledged || !reason?.trim()} onClick={() => void confirm()}>确认计划并生成全部任务</Button></Space>
      </>}
    </Form>
  </Modal>
}
