import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Form, InputNumber, Modal, Space, Switch, Table, TimePicker, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { ArrowLeftOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { PageContainer } from '@ant-design/pro-components'
import { useNavigate, useParams } from 'react-router-dom'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { api, type LineTimetable, type TransportLine } from './api'
import type { Mutate } from './shared'

const { Text } = Typography
type StopTimeForm = { arrival_time?: Dayjs | null; arrival_day_offset?: number; departure_time?: Dayjs | null; departure_day_offset?: number }
type TimetableForm = { stops?: StopTimeForm[] }
type Props = { revision: number; busy: boolean; mutate: Mutate }

function makeDefaultStops(line: TransportLine, departureTime: Dayjs): StopTimeForm[] {
  const firstDeparture = departureTime.hour() * 60 + departureTime.minute()
  const canCalculate = line.legs.every(leg => leg.travel_minutes != null)
  let cursor = firstDeparture
  return line.stations.map((station, position) => {
    const arrival = position === 0 || !canCalculate ? undefined : cursor
    const departure = position === line.stations.length - 1 ? undefined : position === 0 ? firstDeparture : canCalculate
      ? cursor + (line.transfer_overrides.find(item => item.station_id === Number(station.id))?.minutes ?? station.transfer_minutes ?? 0)
      : undefined
    if (canCalculate && position < line.legs.length) cursor = (departure ?? cursor) + line.legs[position].travel_minutes!
    const value = (minute?: number) => minute === undefined ? undefined : dayjs().startOf('day').add(minute, 'minute')
    return { arrival_time: value(arrival), arrival_day_offset: arrival === undefined ? undefined : Math.floor(arrival / 1440), departure_time: value(departure), departure_day_offset: departure === undefined ? undefined : Math.floor(departure / 1440) }
  })
}

export default function LineServicesPage({ revision, busy, mutate }: Props) {
  const { lineId = '' } = useParams()
  const navigate = useNavigate()
  const [line, setLine] = useState<TransportLine>()
  const [services, setServices] = useState<LineTimetable[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const [editing, setEditing] = useState<LineTimetable>()
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm<TimetableForm>()
  const initialized = useRef(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [lineResult, serviceResult] = await Promise.all([api.transportLine(lineId), api.lineTimetables(lineId)])
      setLine(lineResult)
      setServices(serviceResult)
      setError(undefined)
      if (!initialized.current) {
        const departureTime = dayjs().hour(8).minute(0).second(0)
        form.setFieldsValue({ stops: makeDefaultStops(lineResult, departureTime) })
        initialized.current = true
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : '线路班次加载失败。') }
    finally { setLoading(false) }
  }, [lineId, form])

  useEffect(() => { initialized.current = false }, [lineId])
  useEffect(() => { void refresh() }, [refresh, revision])

  const save = async (values: TimetableForm) => {
    if (!line) return
    const stopValues = values.stops ?? []
    if (stopValues.length !== line.stations.length) {
      setError('逐站时刻尚未完整，请检查每个站点。')
      return
    }
    const stops = stopValues.map((stop, position) => ({
      station_id: Number(line.stations[position].id),
      arrival_time: stop.arrival_time ? stop.arrival_time.format('HH:mm:ss') : null,
      arrival_day_offset: stop.arrival_time ? stop.arrival_day_offset ?? 0 : null,
      departure_time: stop.departure_time ? stop.departure_time.format('HH:mm:ss') : null,
      departure_day_offset: stop.departure_time ? stop.departure_day_offset ?? 0 : null,
    }))
    const firstDeparture = stopValues[0]?.departure_time
    if (!firstDeparture) {
      setError('请填写首站发车时间。')
      return
    }
    const departureTime = firstDeparture.format('HH:mm')
    const code = editing?.code ?? `SVC-${departureTime.replace(':', '')}`
    const name = `每日班次 ${departureTime}`
    const result = editing
      ? await mutate(`line-service:${editing.id}:${editing.version}:${departureTime}`, key => api.updateLineTimetable(line.id, editing.id, { expected_version: editing.version, name, valid_from: editing.valid_from, valid_until: editing.valid_until, weekdays: editing.weekdays, timezone: editing.timezone, capacity_snapshot: editing.capacity_snapshot, enabled: editing.enabled, stops }, key), '班次已更新')
      : await mutate(`line-service:${line.id}:${departureTime}`, key => api.createLineTimetable(line.id, { code, name, valid_from: dayjs().format('YYYY-MM-DD'), valid_until: null, weekdays: [1, 2, 3, 4, 5, 6, 7], timezone: 'Asia/Shanghai', capacity_snapshot: {}, enabled: true, stops }, key), '每日班次已添加')
    if (result) {
      setServices(current => (editing ? current.map(item => item.id === result.id ? result : item) : [...current, result]).sort((left, right) => (left.stops[0]?.departure_time ?? '').localeCompare(right.stops[0]?.departure_time ?? '')))
      setModalOpen(false)
      setEditing(undefined)
      setError(undefined)
    }
  }

  const toggle = async (service: LineTimetable, enabled: boolean) => {
    const result = await mutate(`line-service:${service.id}:${enabled}`, key => api.updateLineTimetable(lineId, service.id, { enabled, expected_version: service.version }, key), enabled ? '班次已启用' : '班次已停用')
    if (result) setServices(current => current.map(item => item.id === result.id ? result : item))
  }

  const edit = (service: LineTimetable) => {
    setEditing(service)
    form.setFieldsValue({
      stops: service.stops.map(stop => ({
        arrival_time: stop.arrival_time ? dayjs(`2000-01-01T${stop.arrival_time}`) : null,
        arrival_day_offset: stop.arrival_day_offset ?? 0,
        departure_time: stop.departure_time ? dayjs(`2000-01-01T${stop.departure_time}`) : null,
        departure_day_offset: stop.departure_day_offset ?? 0,
      })),
    })
    setModalOpen(true)
  }

  const create = () => {
    if (!line) return
    setEditing(undefined)
    const departure = dayjs().hour(8).minute(0).second(0)
    form.resetFields()
    form.setFieldsValue({ stops: makeDefaultStops(line, departure) })
    setModalOpen(true)
  }

  const closeModal = () => {
    setModalOpen(false)
    setEditing(undefined)
    form.resetFields()
  }

  const columns: TableColumnsType<LineTimetable> = [
    { title: '班次', width: 180, render: (_, service) => <><Text strong>{service.code}</Text><br />{service.name}</> },
    { title: '首站发车', width: 110, render: (_, service) => service.stops[0]?.departure_time?.slice(0, 5) ?? '—' },
    { title: '逐站到发（北京时间）', render: (_, service) => <div style={{ display: 'grid', gap: 4, minWidth: 380 }}>{service.stops.map((stop, index) => {
      const station = line?.stations.find(item => item.id === stop.station_id)
      const formatTime = (time: string | null, offset: number | null) => time ? `${time.slice(0, 5)}${offset ? ` +${offset}天` : ''}` : '—'
      return <div key={stop.station_id} style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 1fr) minmax(90px, auto) minmax(90px, auto)', gap: 12, alignItems: 'center' }}>
        <Text type="secondary">{index + 1}. {station?.name ?? stop.station_id}</Text>
        <Text>到 {formatTime(stop.arrival_time, stop.arrival_day_offset)}</Text>
        <Text>发 {formatTime(stop.departure_time, stop.departure_day_offset)}</Text>
      </div>
    })}</div> },
    { title: '启用', width: 80, render: (_, service) => <Switch checked={service.enabled} disabled={busy} onChange={value => void toggle(service, value)} /> },
    { title: '操作', width: 100, render: (_, service) => <Button type="link" disabled={busy} onClick={() => edit(service)}>编辑</Button> },
  ]

  return <PageContainer title="线路每日班次" subTitle={line ? `${line.code} · ${line.name} · ${line.stations.map(item => item.name).join(' → ')}` : '正在读取运输线路'} extra={<Space><Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/network/transport-lines')}>返回线路</Button><Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新</Button></Space>}>
    {error && <Alert type="error" showIcon message="线路班次操作失败" description={error} style={{ marginBottom: 16 }} />}
    <Card title="班次规则" extra={<Text type="secondary">每天重复 · 上海时区</Text>}>
      <Button type="primary" icon={<PlusOutlined />} disabled={!line || busy} onClick={create} style={{ marginBottom: 16 }}>新增每日班次</Button>
      <Table<LineTimetable> rowKey="id" loading={loading} columns={columns} dataSource={services} pagination={{ pageSize: 10, showSizeChanger: true }} scroll={{ x: 1150 }} locale={{ emptyText: '该线路还没有班次规则。' }} />
      <Text type="secondary">已启用的每日班次数：{services.filter(service => service.enabled).length}。载运率需运力口径与实际占用统计支持，当前不展示。</Text>
      {line && <Modal title={editing ? '编辑每日班次' : '新增每日班次'} open={modalOpen} onCancel={closeModal} footer={null} width={900} destroyOnClose>
        <Form form={form} layout="vertical" onFinish={values => void save(values)}>
          <Form.List name="stops">{fields => <div style={{ display: 'grid', gap: 8 }}>{fields.map((field, index) => {
            const station = line.stations[index]
            const first = index === 0
            const last = index === line.stations.length - 1
            return <Card key={field.key} size="small" title={`${index + 1}. ${station.name}`}>
              <Space wrap>
                {!first && <><Form.Item name={[field.name, 'arrival_time']} label="到达时间" rules={[{ required: true, message: '请输入到达时间' }]}><TimePicker format="HH:mm" minuteStep={5} /></Form.Item><Form.Item name={[field.name, 'arrival_day_offset']} label="到达 + 天"><InputNumber min={0} max={30} precision={0} /></Form.Item></>}
                {!last && <><Form.Item name={[field.name, 'departure_time']} label="发车时间" rules={[{ required: true, message: '请输入发车时间' }]}><TimePicker format="HH:mm" minuteStep={5} /></Form.Item><Form.Item name={[field.name, 'departure_day_offset']} label="发车 + 天"><InputNumber min={0} max={30} precision={0} /></Form.Item></>}
              </Space>
            </Card>
          })}</div>}</Form.List>
          <Space style={{ marginTop: 8 }}>
            <Button type="primary" htmlType="submit" loading={busy}>{editing ? '保存班次' : '创建班次'}</Button>
            <Button onClick={closeModal}>取消</Button>
          </Space>
        </Form>
      </Modal>}
    </Card>
  </PageContainer>
}
