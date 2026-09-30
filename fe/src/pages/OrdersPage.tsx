import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Empty, Space, Spin, Tag, Timeline, Typography } from 'antd'
import { ArrowLeftOutlined, PlusOutlined } from '@ant-design/icons'
import { ModalForm, PageContainer, ProFormDigit, ProFormSelect, ProFormText, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type Order, type OrderDetail, type OrderInput, type ShipmentDetail } from '../api'
import { apiError, blankOrder, eventText, formatTime, ShipmentLocation, stageLabel, stageText, stages, StatusTag, type Shared, StationName, useNetwork } from '../shared'

const { Text, Title } = Typography

function OrdersPage({ revision, mutate }: Pick<Shared, 'revision' | 'mutate'>) {
  const actionRef = useRef<ActionType | undefined>(undefined)
  const [formOpen, setFormOpen] = useState(false)
  const [loadError, setLoadError] = useState<string>()
  const navigate = useNavigate()
  useEffect(() => { actionRef.current?.reload() }, [revision])
  const columns: ProColumns<Order>[] = [
    { title: '订单号', dataIndex: 'order_no', copyable: true, width: 150 },
    { title: '运单号', dataIndex: 'shipment_no', hideInTable: true, hideInSearch: false },
    { title: '商品', dataIndex: 'product_name', search: false, render: (_, row) => `${row.product_name} × ${row.quantity}` },
    { title: '订单状态', dataIndex: 'status', search: false, valueEnum: { PENDING_SHIPMENT: { text: '待创建运单' }, SHIPMENT_CREATED: { text: '运单已创建' }, COMPLETED: { text: '已完成' } } },
    { title: '运单 / 运输阶段', dataIndex: 'stage', valueType: 'select', valueEnum: stages, fieldProps: { placeholder: '全部运输阶段' }, render: (_, row) => <OrderShipment orderId={row.id} orderStatus={row.status} revision={revision} /> },
    { title: '创建时间', dataIndex: 'created_at', valueType: 'dateTime', search: false },
    { title: '操作', valueType: 'option', render: (_, row) => <a onClick={() => navigate(`/orders/${row.id}`)}>查看详情</a> },
  ]
  return <PageContainer title="订单与运单" subTitle="创建订单、生成发货单，并查看从揽收到签收的完整轨迹。" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setFormOpen(true)}>创建模拟订单</Button>}>
    {loadError && <Alert type="error" showIcon message="订单列表加载失败" description={loadError} action={<Button size="small" onClick={() => actionRef.current?.reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <ProTable<Order> actionRef={actionRef} rowKey="id" columns={columns} scroll={{ x: 980 }} search={{ labelWidth: 96 }} options={{ reload: true, density: true, setting: true }} pagination={{ pageSize: 20, showSizeChanger: true }} request={async params => {
      try {
        const result = await api.orders({ page: params.current ?? 1, page_size: params.pageSize ?? 20, order_no: params.order_no as string, shipment_no: params.shipment_no as string, stage: params.stage as string })
        setLoadError(undefined)
        return { data: result.items, success: true, total: result.total }
      } catch (error) { const reason = apiError(error); setLoadError(reason); return { data: [], success: false, total: 0 } }
    }} />
    <ModalForm<OrderInput> title="创建模拟订单" open={formOpen} onOpenChange={setFormOpen} initialValues={blankOrder} modalProps={{ destroyOnClose: true }} submitter={{ searchConfig: { submitText: '创建订单' } }} onFinish={async values => {
      const saved = await mutate(`create-order:${JSON.stringify(values)}`, key => api.createOrder(values, key), '订单已创建')
      if (saved) { actionRef.current?.reload(); navigate(`/orders/${saved.id}`) }
      return Boolean(saved)
    }}>
      <ProFormText name="product_name" label="商品名称" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} />
      <ProFormDigit name="quantity" label="商品数量" min={1} precision={0} rules={[{ required: true }]} />
      <ProFormText name="sender_name" label="卖家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} />
      <ProFormText name="sender_address" label="卖家详细地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} />
      <ProFormText name="recipient_name" label="买家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} />
      <ProFormText name="recipient_address" label="买家详细地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} />
      <Text type="secondary">配送区域固定为 Z，订单号由后端生成。</Text>
    </ModalForm>
  </PageContainer>
}

function OrderDetailPage({ revision, busy, mutate }: Shared) {
  const { orderId = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<OrderDetail>()
  const [shipment, setShipment] = useState<ShipmentDetail>()
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string>()
  const [retry, setRetry] = useState(0)
  const [editOrderOpen, setEditOrderOpen] = useState(false)
  const [shipmentOpen, setShipmentOpen] = useState(false)
  const network = useNetwork(revision)
  const [addressOpen, setAddressOpen] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    setLoadError(undefined)
    Promise.resolve().then(async () => {
      const order = await api.order(orderId)
      const shipmentDetail = order.shipment ? await api.shipment(order.shipment.id) : undefined
      if (active) { setDetail(order); setShipment(shipmentDetail) }
    }).catch(error => { if (active) setLoadError(apiError(error)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [orderId, revision, retry])

  const title = detail ? `${detail.order_no}${shipment ? ` / ${shipment.shipment_no}` : ''}` : '订单详情'
  return <PageContainer title={title} subTitle="订单信息、运单当前位置与完整物流轨迹。" extra={<Space wrap><Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/orders')}>返回订单列表</Button>{detail && !detail.shipment && <Button onClick={() => setEditOrderOpen(true)}>编辑订单</Button>}{detail && !detail.shipment && <Button type="primary" loading={busy} onClick={() => setShipmentOpen(true)}>创建发货单</Button>}</Space>}>
    {loadError && <Alert type="error" showIcon message="订单详情加载失败" description={loadError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} style={{ marginBottom: 16 }} />}
    {loading && !detail ? <Spin /> : detail && <>
      {shipment && <ShipmentLocation shipment={shipment} />}
      <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
        <Descriptions.Item label="订单状态">{detail.status === 'COMPLETED' ? '已完成' : detail.shipment ? '运单已创建' : '待创建运单'}</Descriptions.Item>
        <Descriptions.Item label="商品">{detail.product_name} × {detail.quantity}</Descriptions.Item>
        <Descriptions.Item label="下单发件地址"><span className="jp-wrap-anywhere">{detail.sender_name} · {detail.sender_address}</span></Descriptions.Item>
        <Descriptions.Item label="下单收件地址"><span className="jp-wrap-anywhere">{detail.recipient_name} · {detail.recipient_address}</span></Descriptions.Item>
        {shipment && <><Descriptions.Item label="运单发件地址"><span className="jp-wrap-anywhere">{shipment.sender_address}</span></Descriptions.Item><Descriptions.Item label="运单目的站"><StationName id={shipment.destination_station_id} /></Descriptions.Item><Descriptions.Item label="当前配送地址"><span className="jp-wrap-anywhere">{shipment.recipient_address}</span></Descriptions.Item></>}
        <Descriptions.Item label="最近扫描站点">{shipment?.last_scanned_station_id ? <StationName id={shipment.last_scanned_station_id} /> : '—'}</Descriptions.Item>
        <Descriptions.Item label="当前阶段">{shipment ? <StatusTag tone="info">{stageText[shipment.stage]}</StatusTag> : '未创建运单'}</Descriptions.Item>
        {shipment?.stage === 'AT_STATION' && <Descriptions.Item label="当前所在站">{shipment.last_scanned_station_id && <StationName id={shipment.last_scanned_station_id} />}</Descriptions.Item>}
        {shipment?.active_transport_task && <Descriptions.Item label={shipment.stage === 'IN_TRANSIT' ? '当前运输' : '待发车线路'}><StationName id={shipment.active_transport_task.origin_station_id} /> → <StationName id={shipment.active_transport_task.destination_station_id} /></Descriptions.Item>}
      </Descriptions>
      {shipment?.stage === 'PENDING_PICKUP' && <Button style={{ marginTop: 16 }} onClick={() => setAddressOpen(true)}>编辑运单地址</Button>}
      <Title level={5} style={{ marginTop: 28 }}>物流轨迹</Title>
      {shipment ? <Timeline items={shipment.tracking_events.map(event => ({ children: <><Text strong>{eventText[event.event_type] ?? event.event_type}</Text><br /><Text type="secondary">{formatTime(event.occurred_at)}{event.station_id ? ' · ' : ''}</Text>{event.station_id && <StationName id={event.station_id} />}</> }))} /> : <Empty description="创建发货单后，物流轨迹会显示在这里" />}
      <Text type="secondary">建运单前可编辑订单；揽收前可修改运单地址；区域 Z 固定。</Text>
    </>}
    <ModalForm<{ destination_station_id: string }> title="创建发货单" open={shipmentOpen} onOpenChange={setShipmentOpen} modalProps={{ destroyOnClose: true }} onFinish={async values => {
      if (!detail) return false
      const destination = Number(values.destination_station_id)
      return Boolean(await mutate(`create-shipment:${detail.id}:${destination}`, key => api.createShipment(detail.id, destination, key), '运单已创建'))
    }}>
      {network.error && <Alert type="error" message={network.error} />}
      <ProFormSelect name="destination_station_id" label="目的站" options={network.stations.filter(s => s.enabled && s.allows_delivery).map(s => ({ value: s.id, label: `${s.code} · ${s.name}` }))} rules={[{ required: true }]} fieldProps={{ placeholder: '选择负责最终派送的站点', notFoundContent: '暂无可派送站点，请先在网络配置中创建' }} />
      <Text type="secondary">目的站创建后不可变更；运单到达该站后才能开始派送。</Text>
    </ModalForm>
    <ModalForm<OrderInput> title={`编辑订单 ${detail?.order_no ?? ''}`} open={editOrderOpen} onOpenChange={setEditOrderOpen} initialValues={detail ? { product_name: detail.product_name, quantity: detail.quantity, sender_name: detail.sender_name, sender_address: detail.sender_address, recipient_name: detail.recipient_name, recipient_address: detail.recipient_address } : blankOrder} modalProps={{ destroyOnClose: true }} submitter={{ searchConfig: { submitText: '保存修改' } }} onFinish={async values => {
      if (!detail) return false
      const result = await mutate(`edit-order:${detail.id}:${JSON.stringify(values)}`, key => api.updateOrder(detail.id, values, key), '订单已更新')
      if (result) setEditOrderOpen(false)
      return Boolean(result)
    }}>
      <ProFormText name="product_name" label="商品名称" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} /><ProFormDigit name="quantity" label="商品数量" min={1} precision={0} rules={[{ required: true }]} /><ProFormText name="sender_name" label="卖家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} /><ProFormText name="sender_address" label="卖家详细地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /><ProFormText name="recipient_name" label="买家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} /><ProFormText name="recipient_address" label="买家详细地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /><Text type="secondary">配送区域固定为 Z，订单号由后端生成。</Text>
    </ModalForm>
    <ModalForm<{ sender_address: string; recipient_address: string }> title="编辑运单地址" open={addressOpen} onOpenChange={setAddressOpen} initialValues={{ sender_address: shipment?.sender_address, recipient_address: shipment?.recipient_address }} modalProps={{ destroyOnClose: true }} onFinish={async values => {
      if (!shipment) return false
      const result = await mutate(`address:${shipment.id}:${JSON.stringify(values)}`, key => api.updateAddress(shipment.id, values, key), '运单地址已更新')
      if (result) { setShipment(result); setAddressOpen(false) }
      return Boolean(result)
    }}><ProFormText name="sender_address" label="发件地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /><ProFormText name="recipient_address" label="收件地址" rules={[{ required: true }]} fieldProps={{ maxLength: 500 }} /></ModalForm>
  </PageContainer>
}

function OrderShipment({ orderId, orderStatus, revision }: { orderId: string; orderStatus: string; revision: number }) {
  const [shipment, setShipment] = useState<OrderDetail['shipment']>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setLoading(true)
    setFailed(false)
    api.order(orderId).then(result => { if (active) setShipment(result.shipment) }).catch(() => { if (active) setFailed(true) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [orderId, revision, retry])
  if (loading) return <Text type="secondary">加载运单信息…</Text>
  if (failed) return <Space size={4}><Text type="secondary">运单信息暂不可用</Text><Button type="link" size="small" onClick={() => setRetry(value => value + 1)}>重试</Button></Space>
  if (!shipment) return orderStatus === 'PENDING_SHIPMENT' ? <Tag>未创建运单</Tag> : orderStatus === 'COMPLETED' ? <StatusTag tone="success">已签收</StatusTag> : <Text type="secondary">尚未关联运单</Text>
  return <Space><Text code>{shipment.shipment_no}</Text><StatusTag tone={shipment.stage === 'SIGNED' ? 'success' : 'info'}>{stageLabel(shipment.stage)}</StatusTag></Space>
}

export { OrderDetailPage }
export default OrdersPage
