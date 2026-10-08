import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Space, Switch, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { PageContainer } from '@ant-design/pro-components'
import { api, type Station, type StationInput } from './api'
import type { Mutate } from './shared'

const { Text } = Typography
type Props = { revision: number; busy: boolean; mutate: Mutate }

export default function NetworkPage({ revision, busy, mutate }: Props) {
  const [stations, setStations] = useState<Station[]>([])
  const [search, setSearch] = useState('')
  const [stationFilter, setStationFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const [modalOpen, setModalOpen] = useState(false)
  const [editingStation, setEditingStation] = useState<Station>()
  const [form] = Form.useForm<StationInput>()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setStations(await api.stations())
      setError(undefined)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '站点列表加载失败，请重试。')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [revision, refresh])

  const rows = useMemo(() => {
    const query = stationFilter.trim().toLocaleLowerCase()
    return query ? stations.filter(station => station.name.toLocaleLowerCase().includes(query)) : stations
  }, [stations, stationFilter])

  const openCreate = () => {
    setEditingStation(undefined)
    form.setFieldsValue({ code: '', name: '', enabled: true, allows_first_arrival: false, allows_delivery: false, transfer_minutes: undefined })
    setModalOpen(true)
  }

  const openEdit = (station: Station) => {
    setEditingStation(station)
    form.setFieldsValue({ name: station.name, enabled: station.enabled, allows_first_arrival: station.allows_first_arrival, allows_delivery: station.allows_delivery, transfer_minutes: station.transfer_minutes ?? undefined })
    setModalOpen(true)
  }

  const saveStation = async (values: StationInput) => {
    const result = editingStation
      ? await mutate(`station:${editingStation.id}:${JSON.stringify(values)}`, key => api.updateStation(editingStation.id, { name: values.name, enabled: values.enabled, allows_first_arrival: values.allows_first_arrival, allows_delivery: values.allows_delivery, ...(values.transfer_minutes === undefined ? {} : { transfer_minutes: values.transfer_minutes }) }, key), '站点配置已更新')
      : await mutate(`create-station:${JSON.stringify(values)}`, key => api.createStation(values, key), '站点已创建')
    if (result) { setModalOpen(false); form.resetFields() }
  }

  const changeStation = (station: Station, change: Partial<Omit<StationInput, 'code'>>) =>
    mutate(`station:${station.id}:${JSON.stringify(change)}`, key => api.updateStation(station.id, change, key), '站点配置已更新')

  const columns: TableColumnsType<Station> = [
    { title: '编码', dataIndex: 'code', width: 150, render: value => <Text strong>{value}</Text> },
    { title: '站点名称', dataIndex: 'name', width: 220 },
    { title: '默认中转参考', dataIndex: 'transfer_minutes', width: 150, render: value => value == null ? '未设置' : `${value} 分钟` },
    { title: '状态', dataIndex: 'enabled', width: 110, render: (_, row) => <Tag color={row.enabled ? 'green' : 'default'}>{row.enabled ? '启用中' : '已停用'}</Tag> },
    { title: '允许首次入站', dataIndex: 'allows_first_arrival', width: 150, render: (_, row) => <Switch size="small" checked={row.allows_first_arrival} disabled={busy || !row.enabled} onChange={value => void changeStation(row, { allows_first_arrival: value })} /> },
    { title: '允许派送', dataIndex: 'allows_delivery', width: 120, render: (_, row) => <Switch size="small" checked={row.allows_delivery} disabled={busy || !row.enabled} onChange={value => void changeStation(row, { allows_delivery: value })} /> },
    { title: '操作', key: 'action', fixed: 'right', width: 100, render: (_, row) => <Space><Button type="link" size="small" disabled={busy} onClick={() => openEdit(row)}>编辑</Button>{row.enabled && <Popconfirm title="停用此站点？" description="若站点仍被运单或启用线路使用，后端会拒绝停用。" okText="停用" cancelText="取消" onConfirm={() => void changeStation(row, { enabled: false })}><Button type="link" danger size="small" disabled={busy}>停用</Button></Popconfirm>}</Space> },
  ]

  return <><PageContainer title="站点管理" subTitle="维护物流站点、站点能力和默认中转参考时长。" extra={<Space><Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增站点</Button></Space>}>
    {error && <Alert type="error" showIcon message="站点配置读取失败" description={error} action={<Button size="small" onClick={() => void refresh()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <Card style={{ marginBottom: 16 }}>
      <Form layout="inline" onFinish={() => setStationFilter(search.trim())} style={{ display: 'flex', flexWrap: 'wrap', gap: '12px 16px' }}>
        <Form.Item label="站点名称" style={{ margin: 0 }}><Input value={search} onChange={event => setSearch(event.target.value)} onPressEnter={() => setStationFilter(search.trim())} allowClear placeholder="请输入站点名称" style={{ width: 320, maxWidth: '60vw' }} /></Form.Item>
        <Space style={{ marginLeft: 'auto' }}><Button onClick={() => { setSearch(''); setStationFilter('') }}>重置</Button><Button type="primary" htmlType="submit">查询</Button></Space>
      </Form>
    </Card>
    <Card title="站点列表">
      <Table<Station> rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={{ pageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: total => `共 ${total} 个站点` }} scroll={{ x: 1000 }} locale={{ emptyText: stationFilter ? '没有匹配的站点。' : '还没有站点，先新增一个站点。' }} />
    </Card>
    <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>站点编码创建后不可修改。停用受在途任务、在站运单和未签收目的运单约束。</Text>
  </PageContainer>
  <Modal title={editingStation ? `编辑站点 ${editingStation.code}` : '新增站点'} open={modalOpen} onCancel={() => setModalOpen(false)} onOk={() => form.submit()} confirmLoading={busy} destroyOnHidden>
    <Form form={form} layout="vertical" initialValues={{ enabled: true, allows_first_arrival: false, allows_delivery: false }} onFinish={values => void saveStation(values)}>
      {!editingStation && <Form.Item name="code" label="站点编码" rules={[{ required: true, message: '请输入编码' }, { pattern: /^[A-Z0-9][A-Z0-9_-]{0,31}$/, message: '使用大写字母、数字、下划线或连字符，最多 32 位' }]}><Input maxLength={32} placeholder="例如 HUB_A" /></Form.Item>}
      <Form.Item name="name" label="站点名称" rules={[{ required: true, whitespace: true, message: '请输入站点名称' }, { max: 100, message: '最多 100 个字符' }]}><Input maxLength={100} /></Form.Item>
      <Form.Item name="transfer_minutes" label="默认中转参考时长（分钟）" rules={editingStation?.transfer_minutes != null ? [{ required: true, message: '已设置的参考时长不能清空' }] : []}><InputNumber min={0} max={525600} precision={0} style={{ width: '100%' }} placeholder="可留空，留空时需人工补全计划" /></Form.Item>
      <Form.Item name="enabled" label="启用站点" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="allows_first_arrival" label="允许首次入站" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="allows_delivery" label="允许最终派送" valuePropName="checked"><Switch /></Form.Item>
    </Form>
  </Modal>
  </>
}
