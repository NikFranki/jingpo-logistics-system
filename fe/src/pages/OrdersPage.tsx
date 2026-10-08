import { fuzzySelectFilter } from '../fuzzySearch'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Space, Spin, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, PlusOutlined } from '@ant-design/icons'
import { ModalForm, PageContainer, ProFormDigit, ProFormSelect, ProFormText, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type DestinationMatch, type Order, type OrderDetail, type OrderInput } from '../api'
import { apiError, stageLabel, stageText, stages, StatusTag, type Shared, useNetwork } from '../shared'
import { AddressRegionFields } from '../AddressRegionFields'

const { Text } = Typography
type OrderFormValues = Omit<OrderInput, 'sender_address' | 'recipient_address' | 'earliest_handover_at' | 'latest_delivery_at' | 'sender_province_id' | 'sender_city_id' | 'sender_district_id' | 'recipient_province_id' | 'recipient_city_id' | 'recipient_district_id'> & {
  sender_region_ids?: string[]
  sender_province_id?: string
  sender_city_id?: string
  sender_district_id?: string
  sender_detail_address: string
  recipient_region_ids?: string[]
  recipient_province_id?: string
  recipient_city_id?: string
  recipient_district_id?: string
  recipient_detail_address: string
}

function regionPath(provinceId?: string | null, cityId?: string | null, districtId?: string | null) {
  return [
    provinceId ? `PROVINCE:${provinceId}` : undefined,
    cityId ? `CITY:${cityId}` : undefined,
    districtId ? `DISTRICT:${districtId}` : undefined,
  ].filter((id): id is string => Boolean(id))
}

function fullAddress(address: Order, side: 'sender' | 'recipient') {
  const region = [address[`${side}_province_name`], address[`${side}_city_name`], address[`${side}_district_name`]].filter(Boolean).join('')
  const detail = address[`${side}_address`]
  return `${region}${region && detail ? ' ' : ''}${detail}`
}

function orderInput(values: OrderFormValues): OrderInput {
  const input: OrderInput = {
    product_name: values.product_name,
    quantity: values.quantity,
    sender_name: values.sender_name,
    sender_address: values.sender_detail_address,
    recipient_name: values.recipient_name,
    recipient_address: values.recipient_detail_address,
  }
  if (values.sender_region_ids?.length) Object.assign(input, {
    sender_province_id: Number(values.sender_province_id),
    sender_city_id: values.sender_city_id ? Number(values.sender_city_id) : null,
    sender_district_id: values.sender_district_id ? Number(values.sender_district_id) : null,
  })
  if (values.recipient_region_ids?.length) Object.assign(input, {
    recipient_province_id: Number(values.recipient_province_id),
    recipient_city_id: values.recipient_city_id ? Number(values.recipient_city_id) : null,
    recipient_district_id: values.recipient_district_id ? Number(values.recipient_district_id) : null,
  })
  return input
}

const blankOrderForm: OrderFormValues = {
  product_name: '',
  quantity: 1,
  sender_name: '',
  recipient_name: '',
  sender_detail_address: '',
  recipient_detail_address: '',
}

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
    { title: '运单 / 运输阶段', dataIndex: 'stage', valueType: 'select', valueEnum: stages, fieldProps: { showSearch: true, filterOption: fuzzySelectFilter, placeholder: '全部运输阶段' }, render: (_, row) => <OrderShipment orderId={row.id} orderStatus={row.status} revision={revision} /> },
    { title: '创建时间', dataIndex: 'created_at', valueType: 'dateTime', search: false },
    { title: '操作', valueType: 'option', render: (_, row) => <Link to={`/orders/${row.id}`}>查看详情</Link> },
  ]
  return <PageContainer title="订单" subTitle="创建订单并管理对应的运单。" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setFormOpen(true)}>创建模拟订单</Button>}>
    {loadError && <Alert type="error" showIcon message="订单列表加载失败" description={loadError} action={<Button size="small" onClick={() => actionRef.current?.reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
    <ProTable<Order> actionRef={actionRef} rowKey="id" columns={columns} scroll={{ x: 980 }} search={{ labelWidth: 96 }} options={{ reload: true, density: true, setting: true }} pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }} request={async params => {
      try {
        const result = await api.orders({ page: params.current ?? 1, page_size: params.pageSize ?? 20, order_no: params.order_no as string, shipment_no: params.shipment_no as string, stage: params.stage as string })
        setLoadError(undefined)
        return { data: result.items, success: true, total: result.total }
      } catch (error) { const reason = apiError(error); setLoadError(reason); return { data: [], success: false, total: 0 } }
    }} />
    <ModalForm<OrderFormValues> title="创建模拟订单" open={formOpen} onOpenChange={setFormOpen} initialValues={blankOrderForm} modalProps={{ destroyOnHidden: true }} submitter={{ searchConfig: { submitText: '创建订单' } }} onFinish={async values => {
      const body = orderInput(values)
      const saved = await mutate(`create-order:${JSON.stringify(body)}`, key => api.createOrder(body, key), '订单已创建')
      if (saved) { actionRef.current?.reload(); navigate(`/orders/${saved.id}`) }
      return Boolean(saved)
    }}>
      <ProFormText name="product_name" label="商品名称" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} />
      <ProFormDigit name="quantity" label="商品数量" min={1} precision={0} rules={[{ required: true }]} />
      <ProFormText name="sender_name" label="卖家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} />
      <AddressRegionFields prefix="sender" label="卖家" />
      <ProFormText name="recipient_name" label="买家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} />
      <AddressRegionFields prefix="recipient" label="买家" />
    </ModalForm>
  </PageContainer>
}

function OrderDetailPage({ revision, busy, mutate }: Shared) {
  const { orderId = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<OrderDetail>()
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string>()
  const [retry, setRetry] = useState(0)
  const [editOrderOpen, setEditOrderOpen] = useState(false)
  const [shipmentOpen, setShipmentOpen] = useState(false)
  const [destinationMatch, setDestinationMatch] = useState<DestinationMatch>()
  const [destinationMatchLoading, setDestinationMatchLoading] = useState(false)
  const [destinationMatchError, setDestinationMatchError] = useState<string>()
  const network = useNetwork(revision)
  const matchOrderId = detail?.id

  useEffect(() => {
    if (!shipmentOpen || !matchOrderId) return
    let active = true
    setDestinationMatch(undefined)
    setDestinationMatchError(undefined)
    setDestinationMatchLoading(true)
    api.destinationMatch(matchOrderId)
      .then(result => { if (active) setDestinationMatch(result) })
      .catch(error => { if (active) setDestinationMatchError(apiError(error)) })
      .finally(() => { if (active) setDestinationMatchLoading(false) })
    return () => { active = false }
  }, [shipmentOpen, matchOrderId, revision])

  useEffect(() => {
    let active = true
    setLoading(true)
    setLoadError(undefined)
    Promise.resolve().then(async () => {
      const order = await api.order(orderId)
      if (active) setDetail(order)
    }).catch(error => { if (active) setLoadError(apiError(error)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [orderId, revision, retry])

  const title = detail?.order_no ?? '订单详情'
  return <PageContainer title={title} subTitle="查看订单信息，创建或打开关联运单。" extra={<Space wrap><Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/orders')}>返回订单列表</Button>{detail && !detail.shipment && <Button onClick={() => setEditOrderOpen(true)}>编辑订单</Button>}{detail && !detail.shipment && <Button type="primary" loading={busy} onClick={() => setShipmentOpen(true)}>创建运单</Button>}{detail?.shipment && <Button type="primary" onClick={() => navigate(`/shipments/${detail.shipment!.id}`)}>查看运单</Button>}</Space>}>
    {loadError && <Alert type="error" showIcon message="订单详情加载失败" description={loadError} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} style={{ marginBottom: 16 }} />}
    {loading && !detail ? <Spin /> : detail && <>
      <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
        <Descriptions.Item label="订单状态">{detail.status === 'COMPLETED' ? '已完成' : detail.shipment ? '运单已创建' : '待创建运单'}</Descriptions.Item>
        <Descriptions.Item label="商品">{detail.product_name} × {detail.quantity}</Descriptions.Item>
        <Descriptions.Item label="下单发件地址"><span className="jp-wrap-anywhere">{detail.sender_name} · {fullAddress(detail, 'sender')}</span></Descriptions.Item>
        <Descriptions.Item label="下单收件地址"><span className="jp-wrap-anywhere">{detail.recipient_name} · {fullAddress(detail, 'recipient')}</span></Descriptions.Item>
        <Descriptions.Item label="关联运单">{detail.shipment ? <><span>{detail.shipment.shipment_no} · {stageText[detail.shipment.stage]}</span> <Button type="link" onClick={() => navigate(`/shipments/${detail.shipment!.id}`)}>打开运单详情</Button></> : '未创建'}</Descriptions.Item>
      </Descriptions>
      <Text type="secondary" style={{ display: 'block', marginTop: 16 }}>订单配送区域固定为 Z；运单创建后，履约操作和物流轨迹在运单详情中查看。</Text>
    </>}
    <ModalForm<{ destination_station_id?: string }> title="创建运单" open={shipmentOpen} onOpenChange={setShipmentOpen} modalProps={{ destroyOnHidden: true }} submitter={{ searchConfig: { submitText: '确认创建' }, submitButtonProps: { disabled: destinationMatchLoading || Boolean(destinationMatchError) || (destinationMatch?.status !== 'MATCHED' && destinationMatch?.status !== 'ADDRESS_REQUIRED') } }} onFinish={async values => {
      if (!detail) return false
      const destination = values.destination_station_id ? Number(values.destination_station_id) : undefined
      const shipment = await mutate(`create-shipment:${detail.id}:${destination ?? 'AUTO'}`, key => api.createShipment(detail.id, destination, key), '运单已创建')
      if (shipment) navigate(`/shipments/${shipment.id}?schedule=1`)
      return Boolean(shipment)
    }}>
      {detail && <Alert type="info" showIcon message="收件地址会自动从订单同步到运单" description={<span className="jp-wrap-anywhere">{detail.recipient_name} · {fullAddress(detail, 'recipient')}</span>} style={{ marginBottom: 20 }} />}
      {destinationMatchLoading && <Spin tip="正在按收件区域匹配目的站…" />}
      {destinationMatchError && <Alert type="error" showIcon message="目的站匹配失败" description={destinationMatchError} action={<Button size="small" onClick={() => setShipmentOpen(false)}>关闭后重试</Button>} />}
      {destinationMatch?.status === 'MATCHED' && destinationMatch.destination_station && <Alert type="success" showIcon message={`目的站：${destinationMatch.destination_station.name}（${destinationMatch.destination_station.code}）`} description={`按收件地址的${destinationMatch.matched_level === 'DISTRICT' ? '区县' : destinationMatch.matched_level === 'CITY' ? '城市' : '省级'}服务范围自动匹配。创建时后端会再次核对。`} />}
      {destinationMatch && destinationMatch.status !== 'MATCHED' && destinationMatch.status !== 'ADDRESS_REQUIRED' && <Alert type="error" showIcon message={destinationMatch.status === 'NOT_FOUND' ? '收件区域暂未配置派送站' : destinationMatch.status === 'CONFLICT' ? '收件区域匹配到多个派送站' : destinationMatch.status === 'INVALID_ADDRESS' ? '订单收件区域无效或已停用' : '订单已有运单'} description={destinationMatch.reason ?? '请先在网络配置中修正站点服务范围或订单地址，再重试。'} />}
      {destinationMatch?.status === 'ADDRESS_REQUIRED' && <>
        <Alert type="warning" showIcon message="该历史订单没有结构化收件区域" description={destinationMatch.reason ?? '请选择负责派送的站点；新订单可按省、市、区自动匹配目的站。'} style={{ marginBottom: 16 }} />
        {network.error && <Alert type="error" message={network.error} />}
        <ProFormSelect name="destination_station_id" label="负责末端派送的站点" options={network.stations.filter(s => s.enabled && s.allows_delivery).map(s => ({ value: s.id, label: `${s.code} · ${s.name}` }))} rules={[{ required: true }]} fieldProps={{ showSearch: true, filterOption: fuzzySelectFilter, placeholder: '选择负责派送的站点', notFoundContent: '暂无可派送站点，请先在网络配置中创建' }} />
      </>}
      <Text type="secondary">收件地址会同步到运单。目的站在创建时由后端按服务范围确认，创建后不可变更；运单到达该站后才能开始派送。</Text>
    </ModalForm>
    <ModalForm<OrderFormValues> title={`编辑订单 ${detail?.order_no ?? ''}`} open={editOrderOpen} onOpenChange={setEditOrderOpen} initialValues={detail ? { product_name: detail.product_name, quantity: detail.quantity, sender_name: detail.sender_name, sender_region_ids: regionPath(detail.sender_province_id, detail.sender_city_id, detail.sender_district_id), sender_province_id: detail.sender_province_id ?? undefined, sender_city_id: detail.sender_city_id ?? undefined, sender_district_id: detail.sender_district_id ?? undefined, sender_detail_address: detail.sender_address, recipient_name: detail.recipient_name, recipient_region_ids: regionPath(detail.recipient_province_id, detail.recipient_city_id, detail.recipient_district_id), recipient_province_id: detail.recipient_province_id ?? undefined, recipient_city_id: detail.recipient_city_id ?? undefined, recipient_district_id: detail.recipient_district_id ?? undefined, recipient_detail_address: detail.recipient_address } : blankOrderForm} modalProps={{ destroyOnHidden: true }} submitter={{ searchConfig: { submitText: '保存修改' } }} onFinish={async values => {
      if (!detail) return false
      const body = orderInput(values)
      const result = await mutate(`edit-order:${detail.id}:${JSON.stringify(body)}`, key => api.updateOrder(detail.id, body, key), '订单已更新')
      if (result) setEditOrderOpen(false)
      return Boolean(result)
    }}>
      <ProFormText name="product_name" label="商品名称" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} /><ProFormDigit name="quantity" label="商品数量" min={1} precision={0} rules={[{ required: true }]} /><ProFormText name="sender_name" label="卖家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} /><AddressRegionFields prefix="sender" label="卖家" regionRequired={false} /><ProFormText name="recipient_name" label="买家姓名" rules={[{ required: true }]} fieldProps={{ maxLength: 100 }} /><AddressRegionFields prefix="recipient" label="买家" regionRequired={false} /><Text type="secondary">历史地址按详细地址载入；选择行政区后保存时会一并写入显示地址。配送区域固定为 Z。</Text>
    </ModalForm>
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
