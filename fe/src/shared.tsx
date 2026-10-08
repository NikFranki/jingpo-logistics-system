import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { api, ApiError, type Action, type OrderInput, type ShipmentDetail, type Stage, type TaskDetail, type Station, type TransportRoute } from './api'
import { statusTagStyles, type StatusTone } from './theme'

const { Text } = Typography
const legacyStageText: Record<string, string> = { AT_A: 'A 站内', IN_TRANSIT_AB: 'A → B 运输中', AT_B: 'B 站内', IN_TRANSIT_BC: 'B → C 运输中', AT_C: 'C 站内' }
export const stageText: Record<Stage, string> = new Proxy({ PENDING_PICKUP: '待揽收', PICKED_UP: '已揽收', AT_STATION: '在站', IN_TRANSIT: '运输中', OUT_FOR_DELIVERY: '派送中', SIGNED: '已签收' }, { get: (labels, property) => typeof property === 'string' ? labels[property as Stage] ?? legacyStageText[property] ?? property : Reflect.get(labels, property) })
export const taskStatusText: Record<string, string> = new Proxy({ WAITING_CARGO: '待首站入站', WAITING_PREDECESSOR: '待前段到达', PENDING_DEPARTURE: '待发车', IN_TRANSIT: '运输中', ARRIVED: '已到达', CANCELLED: '已取消' }, { get: (labels, property) => typeof property === 'string' ? labels[property as keyof typeof labels] ?? property : Reflect.get(labels, property) })
export const eventText: Record<string, string> = { SHIPMENT_CREATED: '发货单已创建', PICKUP: '包裹已揽收', ARRIVE: '到达并入站', DEPART: '运输任务已发车', START_DELIVERY: '开始派送', SIGN: '买家已签收' }
export const actionText: Record<string, string> = { PICKUP: '揽收', ARRIVE: '首次入站', START_DELIVERY: '开始派送', SIGN: '签收', DEPART: '任务发车' }
export const stages = Object.fromEntries(Object.entries(stageText).map(([value, text]) => [value, { text }]))
export const blankOrder: OrderInput = { product_name: '', quantity: 1, sender_name: '', sender_address: '', recipient_name: '', recipient_address: '' }

const chinaTimeZone = 'Asia/Shanghai'
const chinaDateTimeFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: chinaTimeZone,
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})

function chinaDateTimeParts(value: Date) {
  return Object.fromEntries(chinaDateTimeFormatter.formatToParts(value).map(part => [part.type, part.value]))
}

export function formatTime(value?: string | null) {
  if (!value) return '—'
  const parts = chinaDateTimeParts(new Date(value))
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
}

export function formatTimeWithSeconds(value: string) {
  const parts = chinaDateTimeParts(new Date(value))
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}

export function chinaDatePickerValue(value?: string | null) {
  if (!value) return undefined
  const parts = chinaDateTimeParts(new Date(value))
  return dayjs(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`)
}

export function chinaTimeStamp(value?: Dayjs | null) {
  return value ? `${value.second(0).millisecond(0).format('YYYY-MM-DDTHH:mm:ss')}+08:00` : undefined
}
export function apiError(error: unknown) {
  if (error instanceof ApiError) {
    const fields = error.details?.map(item => `${item.field.replace(/^body\./, '')}: ${item.message}`).join('；')
    return `${fields || error.message}${error.requestId ? `（请求 ID ${error.requestId}）` : ''}`
  }
  return error instanceof Error ? error.message : '请求失败，请重试。'
}
export function delayText(item: { delay_status: string; delay_minutes: number | null; status?: string }) {
  if (item.delay_status === 'OVERDUE') return `超时中 · ${item.delay_minutes ?? 0} 分钟`
  if (item.delay_status === 'LATE_ARRIVAL') return `已到达 · 晚到 ${item.delay_minutes ?? 0} 分钟`
  return item.delay_status === 'NOT_APPLICABLE' ? item.status === 'CANCELLED' ? '不适用（任务已取消）' : '未启用延误监测' : '未延误'
}
export function stageLabel(value: string) { return legacyStageText[value] ?? stageText[value as Stage] ?? value }
export function allowed(actions: Action[], action: string) { return actions.find(item => item.action === action) }
export function StatusTag({ tone, children, icon }: { tone: StatusTone; children: ReactNode; icon?: ReactNode }) {
  return <Tag icon={icon} style={statusTagStyles[tone]}>{children}</Tag>
}

export type Mutate = <T>(identity: string, action: (key: string) => Promise<T>, success: string) => Promise<T | undefined>
export type Shared = { revision: number; busy: boolean; mutate: Mutate }
export function useNetwork(revision: number) {
  const [stations, setStations] = useState<Station[]>([])
  const [routes, setRoutes] = useState<TransportRoute[]>([])
  const [error, setError] = useState<string>()
  useEffect(() => {
    let active = true
    Promise.all([api.stations(), api.routes()]).then(([stationRows, routeRows]) => {
      if (active) { setStations(stationRows); setRoutes(routeRows); setError(undefined) }
    }).catch(reason => { if (active) setError(apiError(reason)) })
    return () => { active = false }
  }, [revision])
  return { stations, routes, error }
}

export function StationName({ id }: { id: string }) {
  const [station, setStation] = useState<{ code: string; name: string }>()
  useEffect(() => { let active = true; api.stations().then(rows => { if (active) setStation(rows.find(row => row.id === id)) }).catch(() => { if (active) setStation(undefined) }); return () => { active = false } }, [id])
  return <>{station ? `${station.code} 站（${station.name}）` : id}</>
}

export function ShipmentLocation({ shipment }: { shipment: ShipmentDetail }) {
  if (shipment.stage === 'AT_STATION' && shipment.last_scanned_station_id) {
    return <Alert style={{ marginBottom: 16 }} type="info" showIcon message={<>当前所在站：<Text strong><StationName id={shipment.last_scanned_station_id} /></Text></>} />
  }
  if (shipment.stage === 'IN_TRANSIT' && shipment.active_transport_task) {
    return <Alert style={{ marginBottom: 16 }} type="info" showIcon message={<>当前运输区间：<Text strong><StationName id={shipment.active_transport_task.origin_station_id} /> → <StationName id={shipment.active_transport_task.destination_station_id} /></Text></>} description={<>最近扫描在 <StationName id={shipment.last_scanned_station_id ?? shipment.active_transport_task.origin_station_id} />；到达目的站并入站后才会显示为“当前所在站”。</>} />
  }
  if (shipment.stage === 'OUT_FOR_DELIVERY') return <Alert style={{ marginBottom: 16 }} type="info" showIcon message="包裹正在派送，等待签收。" />
  if (shipment.stage === 'SIGNED') return <Alert style={{ marginBottom: 16 }} type="success" showIcon message="包裹已签收。" />
  return null
}

export function TaskShipmentTag({ item }: { item: TaskDetail['shipments'][number] }) {
  const [shipment, setShipment] = useState<ShipmentDetail>()
  useEffect(() => { let active = true; api.shipment(item.id).then(result => { if (active) setShipment(result) }).catch(() => {}); return () => { active = false } }, [item.id])
  return <Tag>{item.shipment_no} · {stageText[item.stage]}{shipment?.stage === 'AT_STATION' && shipment.last_scanned_station_id ? <> · 当前在 <StationName id={shipment.last_scanned_station_id} /></> : shipment?.stage === 'IN_TRANSIT' && shipment.active_transport_task ? <> · <StationName id={shipment.active_transport_task.origin_station_id} /> → <StationName id={shipment.active_transport_task.destination_station_id} /> 运输中</> : null}</Tag>
}
