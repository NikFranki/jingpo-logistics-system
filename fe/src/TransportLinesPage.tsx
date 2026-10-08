import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { PageContainer } from '@ant-design/pro-components'
import { useNavigate } from 'react-router-dom'
import { api, type Station, type TransportLine, type TransportLineInput, type TransportLineUpdate } from './api'

const { Text } = Typography
type Mutate = <T>(identity: string, action: (key: string) => Promise<T>, success: string) => Promise<T | undefined>
type Props = { revision: number; busy: boolean; mutate: Mutate }
type LineForm = { code?: string; name: string; station_ids: string[]; travel_minutes?: Record<string, number>; transfer_minutes?: Record<string, number>; enabled: boolean }

export default function TransportLinesPage({ revision, busy, mutate }: Props) {
  const navigate = useNavigate()
  const [stations, setStations] = useState<Station[]>([])
  const [lines, setLines] = useState<TransportLine[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<TransportLine>()
  const [form] = Form.useForm<LineForm>()
  const stationIds = Form.useWatch('station_ids', form) ?? []

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [stationRows, result] = await Promise.all([api.stations(), api.transportLines({ page, page_size: pageSize })])
      setStations(stationRows)
      setLines(result.items)
      setTotal(result.total)
      setError(undefined)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '运输线路加载失败，请重试。')
    } finally { setLoading(false) }
  }, [page, pageSize])
  useEffect(() => { void refresh() }, [refresh, revision])

  const openCreate = () => {
    setEditing(undefined)
    form.setFieldsValue({ code: '', name: '', station_ids: [], travel_minutes: {}, transfer_minutes: {}, enabled: true })
    setModalOpen(true)
  }
  const openEdit = (line: TransportLine) => {
    setEditing(line)
    form.setFieldsValue({
      name: line.name,
      station_ids: line.station_ids,
      travel_minutes: Object.fromEntries(line.legs.map(leg => [leg.position, leg.travel_minutes ?? undefined])),
      transfer_minutes: Object.fromEntries(line.transfer_overrides.map(item => [item.station_id, item.minutes])),
      enabled: line.enabled,
    })
    setModalOpen(true)
  }
  const save = async (values: LineForm) => {
    const ids = values.station_ids.map(Number)
    const body: TransportLineInput = {
      code: values.code ?? editing?.code ?? '',
      name: values.name,
      station_ids: ids,
      legs: ids.slice(0, -1).map((_, index) => ({ travel_minutes: values.travel_minutes?.[String(index)] ?? null })),
      transfer_overrides: ids.slice(1, -1).flatMap(id => {
        const minutes = values.transfer_minutes?.[String(id)]
        return minutes === undefined ? [] : [{ station_id: id, minutes }]
      }),
      enabled: values.enabled,
    }
    const result = editing
      ? await mutate(`transport-line:${editing.id}:${JSON.stringify(body)}`, key => {
        const update: TransportLineUpdate = { expected_version: editing.version, name: body.name, station_ids: body.station_ids, legs: body.legs, transfer_overrides: body.transfer_overrides, enabled: body.enabled }
        return api.updateTransportLine(editing.id, update, key)
      }, '运输线路已更新')
      : await mutate(`create-transport-line:${JSON.stringify(body)}`, key => api.createTransportLine(body, key), '运输线路已创建')
    if (result) { setModalOpen(false); form.resetFields() }
    return Boolean(result)
  }
  const toggle = (line: TransportLine, enabled: boolean) => {
    const body: TransportLineUpdate = { expected_version: line.version, enabled }
    return mutate(`transport-line:${line.id}:${JSON.stringify(body)}`, key => api.updateTransportLine(line.id, body, key), enabled ? '运输线路已启用' : '运输线路已停用')
  }

  const columns: TableColumnsType<TransportLine> = [
    { title: '编码 / 名称', width: 220, render: (_, line) => <><Text strong>{line.code}</Text><br />{line.name}</> },
    { title: '完整站点顺序', render: (_, line) => <>{line.stations.map(station => station.name).join(' → ')}<br /><Text type="secondary">{line.legs.length} 段 · 参考总时长 {line.total_reference_minutes ?? '未完整配置'} 分钟</Text></> },
    { title: '状态', width: 130, render: (_, line) => <Space><Tag color={line.usable ? 'green' : 'default'}>{line.usable ? '可用' : line.reason || '不可用'}</Tag><Switch size="small" checked={line.enabled} disabled={busy} onChange={value => void toggle(line, value)} /></Space> },
    { title: '操作', width: 190, render: (_, line) => <Space><Button type="link" disabled={busy} onClick={() => navigate(`/network/transport-lines/${line.id}/services`)}>每日班次</Button><Button type="link" disabled={busy} onClick={() => openEdit(line)}>编辑线路</Button></Space> },
  ]

  return <PageContainer title="运输线路" subTitle="维护站点顺序与分段耗时，并为每条线路配置每日班次；运单按日期选择具体发车计划。" extra={<Space><Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增运输线路</Button></Space>}>
    {error && <Alert type="error" showIcon message="运输线路或班次操作失败" description={error} action={<Button size="small" onClick={() => { setError(undefined); void refresh() }}>重试</Button>} style={{ marginBottom: 16 }} />}
    <Card><Table<TransportLine> rowKey="id" loading={loading} columns={columns} dataSource={lines} pagination={{ current: page, pageSize, total, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: count => `共 ${count} 条运输线路`, onChange: (nextPage, nextPageSize) => { setPage(nextPage); setPageSize(nextPageSize) } }} scroll={{ x: 900 }} locale={{ emptyText: '还没有运输线路，请新增完整线路。' }} /></Card>
    <Modal title={editing ? `编辑运输线路 ${editing.code}` : '新增运输线路'} open={modalOpen} onCancel={() => setModalOpen(false)} onOk={() => form.submit()} confirmLoading={busy} destroyOnHidden width={720}>
      <Form form={form} layout="vertical" initialValues={{ enabled: true }} onFinish={values => void save(values)}>
        {!editing && <Form.Item name="code" label="线路编码" rules={[{ required: true, message: '请输入线路编码' }, { pattern: /^[A-Z0-9][A-Z0-9_-]{0,31}$/, message: '使用大写字母、数字、下划线或连字符，最多 32 位' }]}><Input maxLength={32} /></Form.Item>}
        <Form.Item name="name" label="线路名称" rules={[{ required: true, whitespace: true }, { max: 100 }]}><Input maxLength={100} /></Form.Item>
        <Form.Item name="station_ids" label="按顺序选择站点" rules={[{ required: true, type: 'array', min: 2, message: '至少选择起点和终点' }]}><Select mode="multiple" showSearch optionFilterProp="label" options={stations.map(station => ({ value: station.id, label: `${station.code} · ${station.name}${station.enabled ? '' : '（停用）'}` }))} placeholder="依次选择起点、中转站和终点" /></Form.Item>
        {stationIds.slice(0, -1).map((originId: string, index: number) => {
          const origin = stations.find(station => station.id === originId)
          const destination = stations.find(station => station.id === stationIds[index + 1])
          return <Form.Item key={`${originId}-${destination?.id}`} name={['travel_minutes', String(index)]} label={`${origin?.name ?? '起点'} → ${destination?.name ?? '终点'} 参考运输时长（分钟）`}><InputNumber min={1} max={525600} precision={0} style={{ width: '100%' }} placeholder="留空时沿用分段默认值" /></Form.Item>
        })}
        {stationIds.slice(1, -1).map((stationId: string) => {
          const station = stations.find(item => item.id === stationId)
          return <Form.Item key={stationId} name={['transfer_minutes', stationId]} label={`${station?.name ?? '中转站'} 中转参考时长（分钟）`}><InputNumber min={0} max={525600} precision={0} style={{ width: '100%' }} placeholder={`留空时沿用站点默认值${station?.transfer_minutes == null ? '（当前未设置）' : `（${station.transfer_minutes} 分钟）`}`} /></Form.Item>
        })}
        <Form.Item name="enabled" label="启用线路" valuePropName="checked"><Switch /></Form.Item>
        {editing && <Text type="secondary">线路版本 v{editing.version}。修改站点顺序时，必须为每一段重新确认参考耗时。</Text>}
      </Form>
    </Modal>
  </PageContainer>
}
