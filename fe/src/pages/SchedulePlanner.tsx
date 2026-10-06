import { fuzzySelectFilter } from '../fuzzySearch'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Checkbox, DatePicker, Descriptions, Form, Input, Modal, Radio, Select, Space, Table, Tag, Typography } from 'antd'
import type { Dayjs } from 'dayjs'
import { api, type PathOptions, type SchedulePreview, type SchedulePreviewInput, type ScheduleResponse, type ShipmentDetail, type TransportRoute } from '../api'
import type { Mutate } from '../shared'
import { apiError, chinaDatePickerValue, chinaTimeStamp, formatTime, StationName } from '../shared'
import { RouteSequenceEditor } from '../RouteSequenceEditor'

const { Text } = Typography
const legStateText: Record<string, string> = { ARRIVED: '已到达', IN_TRANSIT: '运输中' }
type FormValues = { mode: 'plan' | 'routes'; origin_station_id?: string; plan_id?: string; route_ids?: string[]; first_departure_at?: Dayjs; planned_origin_arrival_at?: Dayjs; legs?: { route_id: string; planned_departure_at?: Dayjs | null; planned_arrival_at?: Dayjs | null }[]; reason?: string }
type Props = { shipment: ShipmentDetail; routes: TransportRoute[]; open: boolean; busy: boolean; mutate: Mutate; onClose: () => void; onSaved: () => void; autoPreview?: boolean }

export function SchedulePlanner({ shipment, routes, open, busy, mutate, onClose, onSaved, autoPreview = false }: Props) {
  const [form] = Form.useForm<FormValues>()
  const [options, setOptions] = useState<PathOptions>()
  const [recommendedLineId, setRecommendedLineId] = useState<string>()
  const [currentSchedule, setCurrentSchedule] = useState<ScheduleResponse | null>()
  const [preview, setPreview] = useState<SchedulePreview>()
  const [previewFresh, setPreviewFresh] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()
  const [acknowledged, setAcknowledged] = useState<string[]>([])
  const [autoPreviewPending, setAutoPreviewPending] = useState(false)
  const autoPreviewStarted = useRef(false)
  const mode = Form.useWatch('mode', form)
  const selectedPlanId = Form.useWatch('plan_id', form)
  const reason = Form.useWatch('reason', form)
  const routeById = useMemo(() => new Map(routes.map(route => [route.id, route])), [routes])
  const warningCodes = preview?.warnings.map(warning => warning.code) ?? []
  const allWarningsAcknowledged = warningCodes.every(code => acknowledged.includes(code))

  useEffect(() => {
    if (!open) return
    autoPreviewStarted.current = false
    setAutoPreviewPending(false)
    let active = true
    setLoading(true); setError(undefined); setPreview(undefined); setPreviewFresh(false); setAcknowledged([])
    Promise.all([
      api.shipmentPathOptions(shipment.id),
      api.shipmentSchedule(shipment.id).catch(() => shipment.schedule ?? null),
      api.shipmentLineOptions(shipment.id).catch(() => undefined),
      shipment.transport_path?.anchor_station_id
        ? Promise.resolve([])
        : api.pathPlans({ enabled: true, destination_station_id: Number(shipment.destination_station_id) }),
    ]).then(([pathOptions, schedule, lineOptions, destinationPlans]) => {
      if (!active) return
      const plans = pathOptions.path.anchor_station_id ? pathOptions.plans : destinationPlans.filter(item => item.usable)
      const recommendedPlanId = lineOptions?.recommended_line_id && plans.some(item => item.id === lineOptions.recommended_line_id)
        ? lineOptions.recommended_line_id
        : plans.length === 1 ? plans[0].id : undefined
      setOptions({ ...pathOptions, plans })
      setRecommendedLineId(lineOptions?.recommended_line_id ?? undefined)
      setCurrentSchedule(schedule)
      form.setFieldsValue({ mode: plans.length ? 'plan' : 'routes', origin_station_id: pathOptions.path.anchor_station_id ?? undefined, plan_id: recommendedPlanId, route_ids: [], reason: '' })
      setAutoPreviewPending(autoPreview && Boolean(recommendedPlanId))
    }).catch(reason => { if (active) setError(apiError(reason)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [open, shipment.id, shipment.schedule, shipment.destination_station_id, shipment.transport_path?.anchor_station_id, form, autoPreview])

  const requestBody = useCallback((values: FormValues, useEditedLegs: boolean): SchedulePreviewInput => {
    if (!options) throw new Error('路径选项尚未加载')
    const body: SchedulePreviewInput = {
      expected_path_version: shipment.path_version ?? options.path.version,
      expected_schedule_version: currentSchedule?.version ?? shipment.schedule?.version ?? 0,
      expected_destination_station_id: Number(shipment.destination_station_id),
    }
    const selectedPlan = values.mode === 'plan' ? options.plans.find(item => item.id === values.plan_id) : undefined
    if (!options.path.anchor_station_id) {
      const plannedOriginId = selectedPlan?.origin_station_id ?? values.origin_station_id
      if (!plannedOriginId) throw new Error('请选择候选路径，或指定计划起点')
      body.origin_station_id = Number(plannedOriginId)
    }
    if (values.mode === 'plan') {
      if (!values.plan_id) throw new Error('请选择完整路径方案')
      if (!selectedPlan) throw new Error('该路径方案已不可用，请刷新页面后重新选择')
      body.plan_id = Number(values.plan_id)
      body.expected_plan_version = selectedPlan.version
    } else {
      const routeIds = (values.route_ids ?? []).filter(Boolean).map(Number)
      if (!routeIds.length) throw new Error('请逐段添加完整路径')
      body.route_ids = routeIds
    }
    if (!useEditedLegs && values.first_departure_at) body.first_departure_at = chinaTimeStamp(values.first_departure_at)
    if (values.planned_origin_arrival_at) body.planned_origin_arrival_at = chinaTimeStamp(values.planned_origin_arrival_at)
    const sameSource = preview && ((values.mode === 'plan' && String(values.plan_id) === preview.source_plan_id)
      || (values.mode === 'routes' && (values.route_ids ?? []).map(String).join(',') === preview.legs.map(leg => leg.route_id).join(',')))
    if (useEditedLegs && sameSource) body.legs = (values.legs ?? []).map(leg => ({ route_id: Number(leg.route_id), planned_departure_at: chinaTimeStamp(leg.planned_departure_at), planned_arrival_at: chinaTimeStamp(leg.planned_arrival_at) }))
    return body
  }, [options, shipment.path_version, shipment.destination_station_id, currentSchedule?.version, shipment.schedule?.version, preview])

  const runPreview = useCallback(async () => {
    if (!options) return
    let values: FormValues
    try { values = await form.validateFields() } catch { return }
    let body: SchedulePreviewInput
    try { body = requestBody(values, Boolean(preview)) } catch (reason) { setError(reason instanceof Error ? reason.message : '请检查路线输入'); return }
    setLoading(true); setError(undefined)
    try {
      const next = await api.previewShipmentSchedule(shipment.id, body)
      setPreview(next); setPreviewFresh(true); setAcknowledged([])
      form.setFieldsValue({
        legs: next.legs.map(leg => ({ route_id: leg.route_id, planned_departure_at: chinaDatePickerValue(leg.planned_departure_at), planned_arrival_at: chinaDatePickerValue(leg.planned_arrival_at) })),
      })
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
      if (preview && Object.keys(changed).some(key => key !== 'reason')) setPreviewFresh(false)
      if ('origin_station_id' in changed || ('mode' in changed && changed.mode === 'routes')) form.setFieldsValue({ plan_id: undefined, route_ids: [] })
    }}>
      <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="运单">{shipment.shipment_no}</Descriptions.Item>
        <Descriptions.Item label="计划目的站"><StationName id={shipment.destination_station_id} /></Descriptions.Item>
        {shipment.earliest_handover_at && <Descriptions.Item label="最早始发站就绪">{formatTime(shipment.earliest_handover_at)}</Descriptions.Item>}
        {shipment.latest_delivery_at && <Descriptions.Item label="最晚目的站到达">{formatTime(shipment.latest_delivery_at)}</Descriptions.Item>}
        {options?.path.anchor_station_id ? <Descriptions.Item label="实际接续站"><StationName id={options.path.anchor_station_id} /></Descriptions.Item> : <Descriptions.Item label="计划起点">{mode === 'plan' && options?.plans.find(item => item.id === selectedPlanId) ? <StationName id={options.plans.find(item => item.id === selectedPlanId)!.origin_station_id} /> : '选择候选路线后带出'}</Descriptions.Item>}
        <Descriptions.Item label="冻结路段">{options?.path.legs.filter(leg => leg.state === 'ARRIVED' || leg.state === 'IN_TRANSIT').map(leg => `${leg.route_code}（${legStateText[leg.state]}）`).join(' → ') || '暂无'}</Descriptions.Item>
      </Descriptions>
      {(options?.plans.length ?? 0) > 0 && <Form.Item name="mode" label="选择运输路线"><Radio.Group options={[{ label: '候选完整路线', value: 'plan' }, { label: '手动逐段配置', value: 'routes' }]} /></Form.Item>}
      {mode === 'plan' && <Form.Item name="plan_id" rules={[{ required: true, message: '请选择一条完整路线' }]}><Radio.Group style={{ display: 'grid', gap: 8 }} options={options?.plans.map(item => {
        const routeLabels = item.route_ids.map(id => routeById.get(id)).filter(Boolean).map(route => `${route!.origin.name} → ${route!.destination.name}`)
        const stationPath = item.route_ids.map(id => routeById.get(id)).filter(Boolean)
        const pathLabel = stationPath.length ? [stationPath[0]!.origin.name, ...stationPath.map(route => route!.destination.name)].join(' → ') : item.route_ids.map(id => routeById.get(id)?.code ?? id).join(' → ')
        const recommended = recommendedLineId === item.id
        return { value: item.id, label: <span><Text strong>{pathLabel}</Text>{recommended && <Tag color="blue" style={{ marginInlineStart: 8 }}>推荐</Tag>}<br /><Text type="secondary">{item.code} · {item.name} · {routeLabels.length} 段</Text></span> }
      })} /></Form.Item>}
      {!options?.path.anchor_station_id && (mode === 'routes' || !options?.plans.length) && <Form.Item name="origin_station_id" label="计划起点" rules={[{ required: true, message: '请选择计划起点' }]}><Select showSearch filterOption={fuzzySelectFilter} placeholder="选择首次入站前的计划起点" options={routes.map(route => route.origin).concat(routes.map(route => route.destination)).filter((station, index, all) => all.findIndex(item => item.id === station.id) === index && station.enabled && station.allows_first_arrival).map(station => ({ value: station.id, label: `${station.code} · ${station.name}` }))} /></Form.Item>}
      {(mode === 'routes' || !options?.plans.length) && <RouteSequenceEditor name="route_ids" routes={routes} title="完整运输路径" emptyText="从计划起点或接续站开始，逐段添加线路。" anchorStationId={options?.path.anchor_station_id ?? undefined} destinationStationId={shipment.destination_station_id} />}
      {!preview && !options?.path.anchor_station_id && <Form.Item name="planned_origin_arrival_at" label="计划起点入站时间（可选）"><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} /></Form.Item>}
      {!preview && <Form.Item name="first_departure_at" label="首段计划出发时间（可选；默认由后端建议）"><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} /></Form.Item>}
      {!preview && <Space style={{ marginTop: 16 }}><Button type="primary" loading={loading} disabled={busy || loading} onClick={() => void runPreview()}>生成全程时间预览</Button><Text type="secondary">预览只计算方案，不会创建任务。</Text></Space>}
      {preview && <>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginTop: 20, marginBottom: 10 }}><Text strong>逐站计划时间</Text><Button onClick={() => void runPreview()} loading={loading} disabled={busy || loading}>重新计算预览</Button></div>
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message={`未来路径：${preview.legs.length ? `${routeById.get(preview.legs[0].route_id)?.origin.code ?? ''} ${preview.legs.map(leg => `→ ${routeById.get(leg.route_id)?.destination.code ?? leg.destination_station_id}`).join(' ')}` : '无未来路段'}`} description={<>接续站：<StationName id={preview.anchor_station_id} />；目的站：<StationName id={preview.destination_station_id} />。冻结路段不会被本次审核覆盖。</>} />
        <Alert type={previewFresh ? 'info' : 'warning'} showIcon style={{ marginBottom: 12 }} message={previewFresh ? '这是当前时间输入对应的最新预览。修改任一时间后，请重新计算预览再确认。' : '时间或路线已修改，预览凭据已失效；重新计算后才能确认。'} />
        {(preview.missing.length > 0 || preview.replacements.length > 0) && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={preview.missing.length ? '参考时间缺失或计划尚未完整' : '确认时将替换未执行的专属安排'} description={<Space direction="vertical">{preview.missing.map((item, index) => <span key={index}>{String(item.message ?? item.code ?? '请补全计划时间')}</span>)}{preview.replacements.map(item => <span key={item.id}>将替换任务 {item.task_id}{item.task_no ? ` · ${item.task_no}` : ''}</span>)}</Space>} />}
        <Form.List name="legs">{fields => <div style={{ overflowX: 'auto' }}><Table size="small" pagination={false} rowKey={row => String(row.key)} dataSource={fields.map((field, index) => ({ ...field, previewLeg: preview.legs[index] }))} columns={[
          { title: '段', width: 54, render: (_, row) => row.previewLeg.position + 1 },
          { title: '站点区间 / 参考', render: (_, row) => <><Text strong>{row.previewLeg.route_code}</Text><br /><Text><StationName id={row.previewLeg.origin_station_id} /> → <StationName id={row.previewLeg.destination_station_id} /></Text><br /><Text type="secondary">运输参考 {row.previewLeg.travel_reference_minutes ?? '缺失'} 分钟；中转参考 {row.previewLeg.transfer_reference_minutes ?? '缺失'} 分钟</Text>{row.previewLeg.shared_members.length > 0 && <Alert type="warning" showIcon message={`拟共享任务，当前成员：${row.previewLeg.shared_members.join('、')}`} />}</> },
          { title: '计划出发（北京时间）', width: 220, render: (_, row) => <Form.Item name={[row.name, 'planned_departure_at']} style={{ margin: 0 }}><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} /></Form.Item> },
          { title: '计划到达（北京时间）', width: 220, render: (_, row) => <Form.Item name={[row.name, 'planned_arrival_at']} style={{ margin: 0 }}><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} /></Form.Item> },
        ]} /></div>}</Form.List>
        {preview.warnings.map(warning => <Checkbox key={warning.code} checked={acknowledged.includes(warning.code)} onChange={event => setAcknowledged(current => event.target.checked ? [...current, warning.code] : current.filter(code => code !== warning.code))} style={{ display: 'block', margin: '8px 0' }}>我已审核并接受：{warning.message}</Checkbox>)}
        <Form.Item name="reason" label="计划确认原因" rules={[{ required: true, whitespace: true, message: '请填写计划确认原因' }, { max: 500, message: '最多 500 个字符' }]}><Input.TextArea maxLength={500} showCount rows={2} placeholder="例如：按本次订单时效确认全程班次" /></Form.Item>
        {!preview.can_confirm && <Alert type="error" showIcon style={{ marginBottom: 12 }} message="当前预览还不能确认" description="请补全所有时间，或先处理不能由单张运单替换的共享安排，再重新预览。" />}
        <Space style={{ display: 'flex', justifyContent: 'flex-end' }}><Button onClick={onClose}>取消</Button><Button type="primary" loading={busy} disabled={!previewFresh || !preview.can_confirm || !allWarningsAcknowledged || !reason?.trim()} onClick={() => void confirm()}>确认计划并生成全部任务</Button></Space>
      </>}
    </Form>
  </Modal>
}
