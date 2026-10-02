import { useEffect, useState } from 'react'
import { Alert, Cascader, Form, Input, Spin, type CascaderProps } from 'antd'
import type { DefaultOptionType } from 'antd/es/cascader'
import { api, type AdministrativeRegion } from './api'
import { apiError } from './shared'

type RegionOption = DefaultOptionType & {
  value: string
  label: string
  level: AdministrativeRegion['level']
  province_id: string | null
  city_id: string | null
  has_cities?: boolean
  has_districts?: boolean
  isLeaf?: boolean
  children?: RegionOption[]
}

type Props = { prefix: 'sender' | 'recipient'; label: string; regionRequired?: boolean }

const optionsFromRegions = (regions: AdministrativeRegion[]): RegionOption[] => regions.map(region => ({
  value: region.id,
  label: region.name,
  level: region.level,
  province_id: region.province_id,
  city_id: region.city_id,
  has_cities: region.has_cities,
  has_districts: region.has_districts,
  isLeaf: region.level === 'DISTRICT' || (region.level === 'CITY' && !region.has_districts)
    || (region.level === 'PROVINCE' && !region.has_cities && !region.has_districts),
}))

function attachChildren(tree: RegionOption[], value: string, children: RegionOption[]): RegionOption[] {
  return tree.map(option => option.value === value
    ? { ...option, children, isLeaf: children.length === 0 }
    : option.children ? { ...option, children: attachChildren(option.children, value, children) } : option)
}

export function AddressRegionFields({ prefix, label, regionRequired = true }: Props) {
  const form = Form.useFormInstance()
  const regionField = `${prefix}_region_ids`
  const [options, setOptions] = useState<RegionOption[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingChildren, setLoadingChildren] = useState(false)
  const [error, setError] = useState<string>()

  useEffect(() => {
    let active = true
    Promise.resolve().then(async () => {
      let tree = optionsFromRegions(await api.provinces())
      const selectedIds = (form.getFieldValue(regionField) as string[] | undefined) ?? []
      if (selectedIds.length > 1) {
        const province = tree.find(option => option.value === selectedIds[0])
        if (province) {
          let children = province.has_cities ? optionsFromRegions(await api.cities(province.value)) : []
          let city = children.find(option => option.value === selectedIds[1])
          if (!city) {
            children = optionsFromRegions(await api.districts(province.value))
            city = children.find(option => option.value === selectedIds[1])
          }
          tree = attachChildren(tree, province.value, children)
          if (city && selectedIds.length > 2) {
            const districts = optionsFromRegions(await api.districts(province.value, city.value))
            tree = attachChildren(tree, city.value, districts)
          }
        }
      }
      if (active) setOptions(tree)
    }).catch(reason => {
      if (active) setError(apiError(reason))
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [form, regionField])

  const loadData: NonNullable<CascaderProps<RegionOption>['loadData']> = async selectedOptions => {
    const current = selectedOptions[selectedOptions.length - 1] as RegionOption
    setLoadingChildren(true)
    setError(undefined)
    try {
      let regions: AdministrativeRegion[]
      if (current.level === 'PROVINCE') {
        regions = current.has_cities
          ? await api.cities(current.value)
          : await api.districts(current.value)
      } else {
        regions = await api.districts(current.province_id!, current.value)
      }
      const children = optionsFromRegions(regions)
      current.children = children
      current.isLeaf = children.length === 0
      setOptions(previous => attachChildren(previous, current.value, children))
    } catch (reason) {
      setError(apiError(reason))
    } finally {
      setLoadingChildren(false)
    }
  }

  const detailField = `${prefix}_detail_address`
  return <>
    <Form.Item name={regionField} label={`${label}所在地区`} rules={regionRequired ? [{ required: true, type: 'array', min: 1, message: `请选择${label}所在地区` }] : undefined}>
      <Cascader<RegionOption>
        options={options}
        loadData={loadData}
        changeOnSelect={false}
        allowClear
        disabled={loading || options.length === 0}
        placeholder={loading ? '正在加载行政区' : '请选择省 / 市 / 区'}
        displayRender={labels => labels.join(' / ')}
        onChange={(_, selectedOptions) => {
          const levels = ['PROVINCE', 'CITY', 'DISTRICT']
          levels.forEach(level => {
            const selected = selectedOptions.find(option => (option as RegionOption).level === level) as RegionOption | undefined
            form.setFieldValue(`${prefix}_${level.toLowerCase()}_id`, selected?.value)
          })
        }}
        suffixIcon={loading || loadingChildren ? <Spin size="small" /> : undefined}
      />
    </Form.Item>
    <Form.Item name={`${prefix}_province_id`} hidden><Input /></Form.Item>
    <Form.Item name={`${prefix}_city_id`} hidden><Input /></Form.Item>
    <Form.Item name={`${prefix}_district_id`} hidden><Input /></Form.Item>
    <Form.Item name={detailField} label={`${label}详细地址`} rules={[{ required: true, whitespace: true, message: `请输入${label}详细地址` }, { max: 500, message: '详细地址最多 500 个字符' }]}>
      <Input maxLength={500} placeholder="输入街道、门牌号、楼栋和房间号" />
    </Form.Item>
    {error && <Alert type="error" showIcon message="行政区加载失败" description={error} style={{ marginBottom: 16 }} />}
    {!loading && !error && options.length === 0 && <Alert type="info" showIcon message="地址库尚未配置" description="请先导入行政区数据，再创建带地址的订单。" style={{ marginBottom: 16 }} />}
  </>
}
