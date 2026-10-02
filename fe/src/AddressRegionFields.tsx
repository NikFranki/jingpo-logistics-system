import { useEffect, useState } from 'react'
import { Alert, Cascader, Form, Input, Spin, type CascaderProps } from 'antd'
import type { DefaultOptionType } from 'antd/es/cascader'
import { fuzzyMatch } from './fuzzySearch'
import { api, type AdministrativeRegion } from './api'
import { apiError } from './shared'

type RegionOption = DefaultOptionType & {
  value: string
  label: string
  id: string
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
  value: `${region.level}:${region.id}`,
  label: region.name,
  id: region.id,
  level: region.level,
  province_id: region.province_id,
  city_id: region.city_id,
  has_cities: region.has_cities,
  has_districts: region.has_districts,
  isLeaf: region.level === 'DISTRICT' || (region.level === 'CITY' && !region.has_districts)
    || (region.level === 'PROVINCE' && !region.has_cities && !region.has_districts),
}))

function attachChildrenAtPath(tree: RegionOption[], path: string[], children: RegionOption[], depth = 0): RegionOption[] {
  return tree.map(option => {
    if (option.value !== path[depth]) return option
    if (depth === path.length - 1) return { ...option, children, isLeaf: children.length === 0 }
    return { ...option, children: attachChildrenAtPath(option.children ?? [], path, children, depth + 1) }
  })
}

async function provinceChildren(province: RegionOption) {
  const [cities, directDistricts] = await Promise.all([
    province.has_cities ? api.cities(province.id) : Promise.resolve([]),
    province.has_districts ? api.districts(province.id) : Promise.resolve([]),
  ])
  return optionsFromRegions([...cities, ...directDistricts])
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
          const children = await provinceChildren(province)
          const selectedChild = children.find(option => option.value === selectedIds[1])
          tree = attachChildrenAtPath(tree, selectedIds.slice(0, 1), children)
          if (selectedChild?.level === 'CITY' && selectedIds.length > 2) {
            const districts = optionsFromRegions(await api.districts(province.id, selectedChild.id))
            tree = attachChildrenAtPath(tree, selectedIds.slice(0, 2), districts)
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
      const children = current.level === 'PROVINCE'
        ? await provinceChildren(current)
        : optionsFromRegions(await api.districts(current.province_id!, current.id))
      setOptions(previous => attachChildrenAtPath(previous, selectedOptions.map(option => String(option.value)), children))
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
        showSearch={{ filter: (input, path) => fuzzyMatch(input, path.map(option => option.label).join(' ')) }}
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
            form.setFieldValue(`${prefix}_${level.toLowerCase()}_id`, selected?.id)
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
