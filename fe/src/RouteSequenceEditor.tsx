import { Button, Form, Select, Space, Typography } from 'antd'
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import type { TransportRoute } from './api'

const { Text } = Typography

type Props = {
  name: string
  routes: TransportRoute[]
  title: string
  emptyText?: string
  max?: number
  anchorStationId?: string
  destinationStationId?: string
}

export function RouteSequenceEditor({ name, routes, title, emptyText = '添加第一段线路，逐段组成完整路径。', max = 100, anchorStationId, destinationStationId }: Props) {
  const form = Form.useFormInstance()
  const values: string[] = Form.useWatch(name, form) ?? []
  const routeById = new Map(routes.map(route => [String(route.id), route]))
  const stationById = new Map(routes.flatMap(route => [[String(route.origin.id), route.origin], [String(route.destination.id), route.destination]]))
  const selected = values.map(id => id ? routeById.get(String(id)) : undefined)
  const complete = selected.length > 0 && selected.every(Boolean)
  const continuityValid = complete && selected.every((route, index) => index === 0 || selected[index - 1]?.destination.id === route?.origin.id)
  const anchorValid = !anchorStationId || selected[0]?.origin.id === anchorStationId
  const destinationValid = !destinationStationId || selected[selected.length - 1]?.destination.id === destinationStationId
  const previewValid = Boolean(continuityValid && anchorValid && destinationValid)
  const stationIds = complete && selected.length ? [String(selected[0]!.origin.id), ...selected.map(route => String(route!.destination.id))] : []

  return <Form.List name={name} rules={[{
    validator: async (_, values: string[] | undefined) => {
      if (!values?.length || values.some(value => !value)) throw new Error('请为每一段选择线路')
      if (values.length > max) throw new Error(`最多添加 ${max} 段线路`)
      if (complete && !previewValid) throw new Error('线路必须按站点顺序连续连接')
    },
  }]}>
    {(fields, { add, remove, move }, { errors }) => {
      return <>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <Text strong>{title}</Text>
          <Button icon={<PlusOutlined />} onClick={() => add('')} disabled={fields.length >= max}>添加一段</Button>
        </div>
        {fields.length === 0 && <Text type="secondary" style={{ display: 'block', padding: '10px 0' }}>{emptyText}</Text>}
        <Space direction="vertical" size={8} style={{ display: 'flex' }}>
          {fields.map((field, index) => {
            const previousField = fields[index - 1]
            const nextField = fields[index + 1]
            return <div key={field.key} style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 8, alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
                <Text type="secondary" style={{ width: 48, flexShrink: 0 }}>第 {index + 1} 段</Text>
                <Form.Item name={field.name} rules={[{ required: true, message: '请选择这段线路' }]} style={{ margin: 0, flex: 1, minWidth: 0 }}>
                  <Select showSearch optionFilterProp="label" placeholder="选择线路" options={routes.map(route => ({ value: String(route.id), label: `${route.code} · ${route.origin.code} → ${route.destination.code}${route.enabled ? '' : '（停用）'}` }))} />
                </Form.Item>
              </div>
              <Space size={0}>
                <Button type="text" aria-label="上移线路" icon={<ArrowUpOutlined />} disabled={!previousField} onClick={() => previousField && move(field.name, previousField.name)} />
                <Button type="text" aria-label="下移线路" icon={<ArrowDownOutlined />} disabled={!nextField} onClick={() => nextField && move(field.name, nextField.name)} />
                <Button type="text" danger aria-label="删除线路" icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
              </Space>
            </div>
          })}
        </Space>
        <Form.ErrorList errors={errors} />
        {fields.length > 0 && <div style={{ marginTop: 14, padding: '12px 16px', background: '#f5f8fc', borderRadius: 8 }}>
          <Text strong>站点路径</Text>
          <div style={{ marginTop: 6, lineHeight: 1.8 }}>
            {stationIds.length > 0
              ? <Text type={previewValid ? undefined : 'danger'}>{stationIds.map((id, index) => <span key={`${id}-${index}`}>{index > 0 && ' → '}{stationById.get(id)?.code ?? id}</span>)}</Text>
              : <Text type="secondary">选择每段线路后显示路径预览。</Text>}
            {complete && <Text type={previewValid ? 'secondary' : 'danger'} style={{ display: 'block' }}>{previewValid ? '路径连续，起点和终点符合要求。' : '路径不连续，或与指定接续站、目的站不一致。请调整线路顺序。'}</Text>}
          </div>
        </div>}
      </>
    }}
  </Form.List>
}
