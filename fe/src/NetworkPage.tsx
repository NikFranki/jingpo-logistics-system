import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { PageContainer } from '@ant-design/pro-components'
import { api, type RouteInput, type Station, type StationInput, type TransportRoute } from './api'

const { Text } = Typography
type Mutate = <T>(identity: string, action: (key: string) => Promise<T>, success: string) => Promise<T | undefined>
type Props = { revision: number; busy: boolean; mutate: Mutate }
type StationForm = StationInput
type RouteForm = Omit<RouteInput, 'origin_station_id' | 'destination_station_id'> & { origin_station_id: string; destination_station_id: string }

export default function NetworkPage({ revision, busy, mutate }: Props) {
  const [stations, setStations] = useState<Station[]>([])
  const [routes, setRoutes] = useState<TransportRoute[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const [stationModal, setStationModal] = useState(false)
  const [routeModal, setRouteModal] = useState(false)
  const [editingStation, setEditingStation] = useState<Station>()
  const [stationForm] = Form.useForm<StationForm>()
  const [routeForm] = Form.useForm<RouteForm>()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [stationRows, routeRows] = await Promise.all([api.stations(), api.routes()])
      setStations(stationRows)
      setRoutes(routeRows)
      setError(undefined)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '网络配置加载失败，请重试。')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { void refresh() }, [revision, refresh])

  const openCreateStation = () => {
    setEditingStation(undefined)
    stationForm.setFieldsValue({ code: '', name: '', enabled: true, allows_first_arrival: false, allows_delivery: false })
    setStationModal(true)
  }
  const openEditStation = (station: Station) => {
    setEditingStation(station)
    stationForm.setFieldsValue({ name: station.name, enabled: station.enabled, allows_first_arrival: station.allows_first_arrival, allows_delivery: station.allows_delivery })
    setStationModal(true)
  }
  const saveStation = async (values: StationForm) => {
    const result = editingStation
      ? await mutate(`station:${editingStation.id}:${JSON.stringify(values)}`, key => api.updateStation(editingStation.id, { name: values.name, enabled: values.enabled, allows_first_arrival: values.allows_first_arrival, allows_delivery: values.allows_delivery }, key), '站点配置已更新')
      : await mutate(`create-station:${JSON.stringify(values)}`, key => api.createStation(values, key), '站点已创建')
    if (result) { setStationModal(false); stationForm.resetFields() }
    return Boolean(result)
  }
  const saveRoute = async (values: RouteForm) => {
    const body: RouteInput = { ...values, origin_station_id: Number(values.origin_station_id), destination_station_id: Number(values.destination_station_id) }
    const result = await mutate(`create-route:${JSON.stringify(body)}`, key => api.createRoute(body, key), '运输线路已创建')
    if (result) { setRouteModal(false); routeForm.resetFields() }
    return Boolean(result)
  }
  const changeStation = (station: Station, change: Partial<Omit<StationInput, 'code'>>) =>
    mutate(`station:${station.id}:${JSON.stringify(change)}`, key => api.updateStation(station.id, change, key), '站点配置已更新')
  const changeRoute = (route: TransportRoute, change: { enabled?: boolean; delay_monitoring_enabled?: boolean }) =>
    mutate(`route:${route.id}:${JSON.stringify(change)}`, key => api.updateRoute(route.id, change, key), '线路配置已更新')

  const stationColumns: TableColumnsType<Station> = [
    { title: '编码', dataIndex: 'code', width: 150, render: value => <Text strong>{value}</Text> },
    { title: '站点名称', dataIndex: 'name', width: 220 },
    { title: '状态', dataIndex: 'enabled', width: 110, render: (_, row) => <Tag color={row.enabled ? 'green' : 'default'}>{row.enabled ? '启用中' : '已停用'}</Tag> },
    { title: '允许首次入站', dataIndex: 'allows_first_arrival', width: 150, render: (_, row) => <Switch size="small" checked={row.allows_first_arrival} disabled={busy || !row.enabled} onChange={value => void changeStation(row, { allows_first_arrival: value })} /> },
    { title: '允许派送', dataIndex: 'allows_delivery', width: 120, render: (_, row) => <Switch size="small" checked={row.allows_delivery} disabled={busy || !row.enabled} onChange={value => void changeStation(row, { allows_delivery: value })} /> },
    { title: '操作', key: 'action', fixed: 'right', width: 100, render: (_, row) => <Space><Button type="link" size="small" disabled={busy} onClick={() => openEditStation(row)}>编辑</Button>{row.enabled && <Popconfirm title="停用此站点？" description="若站点仍被运单或启用线路使用，后端会拒绝停用。" okText="停用" cancelText="取消" onConfirm={() => void changeStation(row, { enabled: false })}><Button type="link" danger size="small" disabled={busy}>停用</Button></Popconfirm>}</Space> },
  ]
  const routeColumns: TableColumnsType<TransportRoute> = [
    { title: '线路编码', dataIndex: 'code', width: 140, render: value => <Text strong>{value}</Text> },
    { title: '运输区间', key: 'route', render: (_, row) => <>{row.origin.code} · {row.origin.name} → {row.destination.code} · {row.destination.name}</> },
    { title: '线路状态', dataIndex: 'enabled', width: 120, render: (_, row) => <Switch checkedChildren="启用" unCheckedChildren="停用" checked={row.enabled} disabled={busy || (!row.enabled && (!row.origin.enabled || !row.destination.enabled))} onChange={value => void changeRoute(row, { enabled: value })} /> },
    { title: '延误监测', dataIndex: 'delay_monitoring_enabled', width: 130, render: (_, row) => <Switch size="small" checked={row.delay_monitoring_enabled} disabled={busy} onChange={value => void changeRoute(row, { delay_monitoring_enabled: value })} /> },
  ]

  return <><PageContainer title="网络配置" subTitle="维护物流站点、线路及它们可承担的业务。" extra={<Space wrap><Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新</Button><Button icon={<PlusOutlined />} onClick={openCreateStation}>新增站点</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => { routeForm.setFieldsValue({ code: '', origin_station_id: undefined, destination_station_id: undefined, enabled: true, delay_monitoring_enabled: false }); setRouteModal(true) }}>新增线路</Button></Space>}>
    {error && <Alert type="error" showIcon message="网络配置读取失败" description={error} action={<Button size="small" onClick={() => void refresh()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <Card title="站点" style={{ marginBottom: 16 }}>
      <Table<Station> rowKey="id" loading={loading} columns={stationColumns} dataSource={stations} pagination={false} scroll={{ x: 850 }} locale={{ emptyText: '还没有站点，先新增一个站点。' }} />
    </Card>
    <Card title="运输线路">
      <Table<TransportRoute> rowKey="id" loading={loading} columns={routeColumns} dataSource={routes} pagination={false} scroll={{ x: 760 }} locale={{ emptyText: '还没有线路，先配置站点再新增线路。' }} />
    </Card>
    <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>站点编码和线路起终点创建后不可修改。停用受在途任务、在站运单和未签收目的运单约束。</Text>
  </PageContainer>
  <Modal title={editingStation ? `编辑站点 ${editingStation.code}` : '新增站点'} open={stationModal} onCancel={() => setStationModal(false)} onOk={() => stationForm.submit()} confirmLoading={busy} destroyOnHidden>
    <Form form={stationForm} layout="vertical" initialValues={{ enabled: true, allows_first_arrival: false, allows_delivery: false }} onFinish={values => void saveStation(values)}>
      {!editingStation && <Form.Item name="code" label="站点编码" rules={[{ required: true, message: '请输入编码' }, { pattern: /^[A-Z0-9][A-Z0-9_-]{0,31}$/, message: '使用大写字母、数字、下划线或连字符，最多 32 位' }]}><Input maxLength={32} placeholder="例如 HUB_A" /></Form.Item>}
      <Form.Item name="name" label="站点名称" rules={[{ required: true, whitespace: true, message: '请输入站点名称' }, { max: 100, message: '最多 100 个字符' }]}><Input maxLength={100} /></Form.Item>
      <Form.Item name="enabled" label="启用站点" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="allows_first_arrival" label="允许首次入站" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="allows_delivery" label="允许最终派送" valuePropName="checked"><Switch /></Form.Item>
    </Form>
  </Modal>
  <Modal title="新增运输线路" open={routeModal} onCancel={() => setRouteModal(false)} onOk={() => routeForm.submit()} confirmLoading={busy} destroyOnHidden>
    <Form form={routeForm} layout="vertical" initialValues={{ enabled: true, delay_monitoring_enabled: false }} onFinish={values => void saveRoute(values)}>
      <Form.Item name="code" label="线路编码" rules={[{ required: true, message: '请输入编码' }, { pattern: /^[A-Z0-9][A-Z0-9_-]{0,31}$/, message: '使用大写字母、数字、下划线或连字符，最多 32 位' }]}><Input maxLength={32} placeholder="例如 ROUTE_AB" /></Form.Item>
      <Form.Item name="origin_station_id" label="起点站" rules={[{ required: true, message: '请选择起点站' }]}><Select options={stations.filter(item => item.enabled).map(item => ({ value: item.id, label: `${item.code} · ${item.name}` }))} placeholder="选择启用站点" /></Form.Item>
      <Form.Item name="destination_station_id" label="终点站" rules={[{ required: true, message: '请选择终点站' }]}><Select options={stations.filter(item => item.enabled).map(item => ({ value: item.id, label: `${item.code} · ${item.name}` }))} placeholder="选择启用站点" /></Form.Item>
      <Form.Item name="enabled" label="启用线路" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="delay_monitoring_enabled" label="启用延误监测" valuePropName="checked"><Switch /></Form.Item>
      <Text type="secondary">起点和终点必须不同；创建后线路编码与端点不可修改。</Text>
    </Form>
  </Modal>
  </>
}
