export type Stage = 'PENDING_PICKUP' | 'PICKED_UP' | 'AT_STATION' | 'IN_TRANSIT' | 'OUT_FOR_DELIVERY' | 'SIGNED'
export type TaskStatus = 'WAITING_CARGO' | 'WAITING_PREDECESSOR' | 'PENDING_DEPARTURE' | 'IN_TRANSIT' | 'ARRIVED' | 'CANCELLED'
export type RouteCode = string
export type Page<T> = { items: T[]; total: number; page: number; page_size: number }
export type Action = { action: string; enabled: boolean; reason_code: string | null; reason: string | null }
export type AddressSnapshot = { sender_province_id?: string | null; sender_province_name?: string | null; sender_city_id?: string | null; sender_city_name?: string | null; sender_district_id?: string | null; sender_district_name?: string | null; recipient_province_id?: string | null; recipient_province_name?: string | null; recipient_city_id?: string | null; recipient_city_name?: string | null; recipient_district_id?: string | null; recipient_district_name?: string | null }
export type Order = AddressSnapshot & { id: string; order_no: string; product_name: string; quantity: number; sender_name: string; sender_address: string; recipient_name: string; recipient_address: string; earliest_handover_at: string | null; latest_delivery_at: string | null; region_code: string; status: string; created_at: string; updated_at: string }
export type OrderDetail = Order & { shipment: { id: string; shipment_no: string; stage: Stage } | null }
export type DestinationMatch = { status: 'MATCHED' | 'NOT_FOUND' | 'CONFLICT' | 'ADDRESS_REQUIRED' | 'INVALID_ADDRESS' | 'EXISTING_SHIPMENT'; reason: string | null; destination_station: Station | null; matched_level: 'PROVINCE' | 'CITY' | 'DISTRICT' | null; service_area_id: string | null }
export type AdministrativeRegion = { id: string; code: string; name: string; level: 'PROVINCE' | 'CITY' | 'DISTRICT'; province_id: string | null; city_id: string | null; kind: string | null; has_cities?: boolean; has_districts?: boolean }
export type Shipment = AddressSnapshot & { id: string; shipment_no: string; order_id: string; sender_address: string; recipient_address: string; earliest_handover_at?: string | null; latest_delivery_at?: string | null; region_code: string; stage: Stage; destination_station_id: string; last_scanned_station_id: string | null; created_at: string; updated_at: string; scheduling_mode?: 'LEGACY' | 'REVIEWED'; schedule_status?: ScheduleStatus | null; schedule_version?: number }
export type ScheduleStatus = 'NOT_CONFIRMED' | 'CONFIRMED' | 'NEEDS_RECONFIRMATION' | 'BLOCKED' | 'COMPLETED'
export type AssociationState = 'PLANNED' | 'ACTIVE' | 'RELEASED'
export type PathLegState = 'PENDING' | 'RESERVED' | 'IN_TRANSIT' | 'ARRIVED'
export type TransportPathStatus = 'WAITING_FIRST_ARRIVAL' | 'NEEDS_PLANNING' | 'READY' | 'RESERVED' | 'IN_TRANSIT' | 'COMPLETED' | 'BLOCKED'
export type PathLeg = { id: string; position: number; route_id: string; route_code: string; origin_station_id: string; destination_station_id: string; state: PathLegState; task_id: string | null }
export type ShipmentTransportPath = { version: number; status: TransportPathStatus; anchor_station_id: string | null; destination_station_id: string; next_route_id: string | null; next_route_code: string | null; reason_code: string | null; reason: string | null; legs: PathLeg[] }
export type PathPlan = { id: string; code: string; name: string; enabled: boolean; version: number; origin_station_id: string; destination_station_id: string; usable: boolean; reason: string | null; route_ids: string[]; transfer_overrides?: TransferOverride[] }
export type PathOptions = { path: ShipmentTransportPath; plans: PathPlan[] }
export type ShipmentLineOptions = { recommended_line_id: string | null; lines: { id: string; total_reference_minutes: number | null }[] }
export type PathVersionLeg = Pick<PathLeg, 'id' | 'position' | 'route_id' | 'route_code' | 'origin_station_id' | 'destination_station_id'>
export type PathVersion = { version: number; destination_station_id: string; source_plan_id: string | null; source_plan_version: number | null; reason: string; occurred_at: string; legs: PathVersionLeg[] }
export type PathHistoryPage = Page<PathVersion>
export type TrackingEvent = { id: string; event_type: string; occurred_at: string; station_id: string | null; task_id: string | null }
export type ActiveTransportTask = { id: string; route_code: RouteCode; origin_station_id: string; destination_station_id: string; status: TaskStatus }
export type ScheduleLeg = { planned_travel_minutes: number | null; approved_transfer_minutes: number | null; planned_origin_arrival_at: string | null; path_leg_id: string; position: number; route_id: string; route_code: string; origin_station_id: string; destination_station_id: string; task_id: string | null; association_id: string | null; planned_departure_at: string | null; planned_arrival_at: string | null; travel_reference_minutes: number | null; transfer_reference_minutes: number | null; scheduling_source: string | null; schedule_revision: number | null; forecast_departure_at: string | null; forecast_arrival_at: string | null; forecast_stale: boolean; waiting_members: { shipment_id: string; shipment_no?: string; reason?: string; ready_at?: string | null }[]; task_no: string | null; task_status: string | null; actual_departure_at: string | null; actual_arrival_at: string | null; association_state: AssociationState | null; ready_at: string | null }
export type ScheduleResponse = { configuration_risks: { code?: string; message?: string; [key: string]: unknown }[]; shipment_id: string; scheduling_mode: string; status: ScheduleStatus; reason: string | null; path_version: number; version: number; origin_station_id: string | null; destination_station_id: string; legs: ScheduleLeg[] }
export type SchedulePreviewLeg = { position: number; route_id: string; route_code: string; origin_station_id: string; destination_station_id: string; planned_departure_at: string | null; planned_arrival_at: string | null; travel_reference_minutes: number | null; transfer_reference_minutes: number | null; approved_transfer_minutes: number; planned_travel_minutes: number | null; shared_task_id: string | null; shared_task_revision: number | null; shared_members: string[] }
export type SchedulePreview = { shipment_id: string; path_version: number; schedule_version: number; destination_station_id: string; anchor_station_id: string; stage: string; frozen_task_id: string | null; frozen_task_revision: number | null; anchor_arrival_at: string | null; source_plan_id: string | null; source_plan_version: number | null; legs: SchedulePreviewLeg[]; warnings: { code: string; message: string }[]; missing: { code?: string; message?: string; [key: string]: unknown }[]; replacements: { id: string; task_id: string; task_no?: string; [key: string]: unknown }[]; planned_origin_arrival_at: string | null; can_confirm: boolean; preview_token: string }
export type ScheduleHistoryItem = { version: number; path_version: number; reason: string; occurred_at: string; legs: ScheduleLeg[]; source_plan_id: string | null; source_plan_version: number | null }
export type ScheduleHistoryPage = Page<ScheduleHistoryItem>
export type ShipmentDetail = Shipment & { scheduling_mode?: 'LEGACY' | 'REVIEWED'; schedule?: ScheduleResponse | null; active_transport_task: ActiveTransportTask | null; tracking_events: TrackingEvent[]; allowed_actions: Action[]; path_version?: number; transport_path?: ShipmentTransportPath | null }
export type TaskItem = { scheduling_source?: 'LEGACY' | 'PLAN'; planned_departure_at: string | null; forecast_departure_at?: string | null; forecast_arrival_at?: string | null; forecast_stale?: boolean; schedule_revision?: number; waiting_members?: { shipment_id: string; shipment_no?: string; reason?: string; reason_code?: string; ready_at?: string | null }[]; delay_monitoring_enabled: boolean; id: string; task_no: string; route_code: RouteCode; status: TaskStatus; expected_arrival_at: string; departed_at: string | null; arrived_at: string | null; cancelled_at: string | null; cancel_reason: string | null; created_at: string; delay_status: string; delay_minutes: number | null }
export type TaskDetail = Omit<TaskItem, 'delay_status' | 'delay_minutes'> & { origin_station_id: string; destination_station_id: string; shipments: { id: string; shipment_no: string; stage: Stage }[]; delay_status: string; delay_minutes: number | null; allowed_actions: Action[]; cancel_impact?: { association_id: string; shipment_id: string; task_id: string; task_revision: number; task_status: string; association_state: AssociationState }[]; cancelled_at: string | null; cancel_reason: string | null }
export type TaskPage = Page<TaskItem>
export type ShipmentTaskHistory = { association_state?: AssociationState; schedule_version?: number | null; release_reason?: string | null; id: string; task_no: string; route_code: string; status: TaskStatus; origin_station_id: string; destination_station_id: string; cancelled_at: string | null; cancel_reason: string | null; released_at: string | null }
export type ShipmentTaskHistoryPage = Page<ShipmentTaskHistory>
export type DestinationChange = { id: string; previous_destination_station_id: string; destination_station_id: string; reason: string; occurred_at: string }
export type DestinationChangePage = Page<DestinationChange>
export type Station = { id: string; code: string; name: string; enabled: boolean; allows_first_arrival: boolean; allows_delivery: boolean; transfer_minutes?: number | null }
export type TransportRoute = { id: string; code: string; origin: Station; destination: Station; enabled: boolean; delay_monitoring_enabled: boolean; travel_minutes?: number | null }
export type TransferOverride = { station_id: number; minutes: number }
export type PathPlanInput = { code: string; name: string; route_ids: number[]; enabled: boolean; transfer_overrides?: TransferOverride[] }
export type PathPlanUpdate = { expected_version: number; name?: string; route_ids?: number[]; enabled?: boolean; transfer_overrides?: TransferOverride[] }
export type ShipmentPathUpdate = { expected_version: number; expected_anchor_station_id: number; reason: string } & ({ plan_id: number; expected_plan_version: number } | { route_ids: number[] })
export type StationInput = Omit<Station, 'id'>
export type RouteInput = { code: string; origin_station_id: number; destination_station_id: number; enabled: boolean; delay_monitoring_enabled: boolean; travel_minutes?: number }
export type CancelPreview = { task_id: string; schedule_revision: number; reason: string; impact: { association_id: string; shipment_id: string; task_id: string; task_revision: number; task_status: string; association_state: AssociationState }[]; cancel_token: string }
export type SchedulePreviewInput = { expected_path_version?: number; expected_schedule_version?: number; expected_destination_station_id?: number; origin_station_id?: number; plan_id?: number; expected_plan_version?: number; route_ids?: number[]; first_departure_at?: string; planned_origin_arrival_at?: string; legs?: { route_id: number; planned_departure_at?: string | null; planned_arrival_at?: string | null }[] }
export type Candidate = Shipment & { last_scanned_station_id: string }
export type OrderInput = Pick<Order, 'product_name' | 'quantity' | 'sender_name' | 'sender_address' | 'recipient_name' | 'recipient_address'> & Partial<{ earliest_handover_at: string | null; latest_delivery_at: string | null; sender_province_id: number | null; sender_city_id: number | null; sender_district_id: number | null; recipient_province_id: number | null; recipient_city_id: number | null; recipient_district_id: number | null }>

const base = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  constructor(message: string, public status: number, public requestId?: string, public details?: { field: string; message: string }[]) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  const timeoutController = new AbortController()
  const timeout = setTimeout(() => timeoutController.abort(), 15_000)
  try {
    response = await fetch(`${base}${path}`, { ...init, signal: init?.signal ?? timeoutController.signal })
  } catch (error) {
    if (timeoutController.signal.aborted) throw new ApiError('请求超时，后端暂时没有响应，请重试。', 0)
    if (error instanceof Error && error.name === 'AbortError') throw new ApiError('请求已取消。', 0)
    throw new ApiError('无法连接后端服务，请确认 BE 已启动后重试。', 0)
  } finally {
    clearTimeout(timeout)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const fallback: Record<number, string> = {
      400: '提交内容有误，请检查表单后重试。',
      401: '请求未通过身份验证，请检查访问凭证。',
      403: '当前账号没有执行此操作的权限。',
      404: '目标记录不存在或已被删除，请刷新列表。',
      409: '数据状态已变化，请刷新后重试。',
      429: '操作过于频繁，请稍后重试。',
    }
    const message = body?.error?.message ?? fallback[response.status] ?? (response.status >= 500 ? '后端暂时无法处理请求，请稍后重试。' : `请求失败 (${response.status})`)
    throw new ApiError(message, response.status, body?.request_id, body?.error?.details)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function query(values: Record<string, string | number | boolean | undefined>) {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)) })
  return params.size ? `?${params}` : ''
}

function write<T>(path: string, method: 'POST' | 'PATCH' | 'PUT', body?: unknown, key: string = crypto.randomUUID()) {
  return request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

let stationsRequest: Promise<Station[]> | undefined
let routesRequest: Promise<TransportRoute[]> | undefined
let stationsCachedAt = 0
let routesCachedAt = 0
const networkCacheTtl = 30_000

function stations() {
  if (!stationsRequest || Date.now() - stationsCachedAt > networkCacheTtl) {
    stationsCachedAt = Date.now()
    stationsRequest = request<Station[] | Page<Station>>('/api/v1/stations').then(async result => {
      if (Array.isArray(result)) return result
      const rows = [...result.items]
      for (let page = 2; rows.length < result.total; page += 1) {
        const next = (await api.stationPage({ page, page_size: result.page_size })).items
        if (!next.length) break
        rows.push(...next)
      }
      return rows
    }).catch(error => {
      stationsRequest = undefined
      stationsCachedAt = 0
      throw error
    })
  }
  return stationsRequest
}

function routes() {
  if (!routesRequest || Date.now() - routesCachedAt > networkCacheTtl) {
    routesCachedAt = Date.now()
    routesRequest = request<TransportRoute[] | Page<TransportRoute>>('/api/v1/routes').then(async result => {
      if (Array.isArray(result)) return result
      const rows = [...result.items]
      for (let page = 2; rows.length < result.total; page += 1) {
        const next = (await api.routePage({ page, page_size: result.page_size })).items
        if (!next.length) break
        rows.push(...next)
      }
      return rows
    }).catch(error => {
      routesRequest = undefined
      routesCachedAt = 0
      throw error
    })
  }
  return routesRequest
}

function writeNetwork<T>(path: string, method: 'POST' | 'PATCH', body: unknown, key?: string) {
  return write<T>(path, method, body, key).then(result => {
    stationsRequest = undefined
    routesRequest = undefined
    stationsCachedAt = 0
    routesCachedAt = 0
    return result
  })
}

export const api = {
  provinces: () => request<AdministrativeRegion[]>('/api/v1/provinces'),
  cities: (province_id: string) => request<AdministrativeRegion[]>(`/api/v1/cities${query({ province_id })}`),
  districts: (province_id: string, city_id?: string) => request<AdministrativeRegion[]>(`/api/v1/districts${query({ province_id, city_id })}`),
  stations,
  routes,
  stationPage: (params: { page?: number; page_size?: number } = {}) => request<Page<Station> | Station[]>(`/api/v1/stations${query(params)}`).then(result => Array.isArray(result) ? { items: result, total: result.length, page: params.page ?? 1, page_size: params.page_size ?? result.length } : result),
  routePage: (params: { page?: number; page_size?: number } = {}) => request<Page<TransportRoute> | TransportRoute[]>(`/api/v1/routes${query(params)}`).then(result => Array.isArray(result) ? { items: result, total: result.length, page: params.page ?? 1, page_size: params.page_size ?? result.length } : result),
  createStation: (body: StationInput, key?: string) => writeNetwork<Station>('/api/v1/stations', 'POST', body, key),
  updateStation: (id: string, body: Partial<Omit<StationInput, 'code'>>, key?: string) => writeNetwork<Station>(`/api/v1/stations/${id}`, 'PATCH', body, key),
  createRoute: (body: RouteInput, key?: string) => writeNetwork<TransportRoute>('/api/v1/routes', 'POST', body, key),
  updateRoute: (id: string, body: { enabled?: boolean; delay_monitoring_enabled?: boolean }, key?: string) => writeNetwork<TransportRoute>(`/api/v1/routes/${id}`, 'PATCH', body, key),
  pathPlans: (params: { enabled?: boolean; origin_station_id?: number; destination_station_id?: number } = {}) => request<PathPlan[]>(`/api/v1/path-plans${query(params)}`),
  createPathPlan: (body: PathPlanInput, key?: string) => write<PathPlan>('/api/v1/path-plans', 'POST', body, key),
  updatePathPlan: (id: string, body: PathPlanUpdate, key?: string) => write<PathPlan>(`/api/v1/path-plans/${id}`, 'PATCH', body, key),
  orders: (params: { page?: number; page_size?: number; order_no?: string; shipment_no?: string; stage?: string } = {}) => request<Page<Order>>(`/api/v1/orders${query(params)}`),
  order: (id: string) => request<OrderDetail>(`/api/v1/orders/${id}`),
  createOrder: (body: OrderInput, key?: string) => write<Order>('/api/v1/orders/create', 'POST', body, key),
  updateOrder: (id: string, body: OrderInput, key?: string) => write<Order>(`/api/v1/orders/${id}`, 'PATCH', body, key),
  destinationMatch: (orderId: string) => request<DestinationMatch>(`/api/v1/orders/${orderId}/destination-match`),
  createShipment: (orderId: string, destination_station_id?: number, key?: string) => write<ShipmentDetail>(`/api/v1/orders/${orderId}/shipment`, 'POST', { ...(destination_station_id ? { destination_station_id } : {}), scheduling_mode: 'REVIEWED' }, key),
  shipments: (params: { page?: number; page_size?: number; shipment_no?: string; stage?: string } = {}) => request<Page<Shipment>>(`/api/v1/shipments${query(params)}`),
  shipment: (id: string) => request<ShipmentDetail>(`/api/v1/shipments/${id}`),
  shipmentPath: (id: string) => request<ShipmentTransportPath>(`/api/v1/shipments/${id}/path`),
  shipmentPathOptions: (id: string) => request<PathOptions>(`/api/v1/shipments/${id}/path-options`),
  shipmentLineOptions: (id: string) => request<ShipmentLineOptions>(`/api/v1/shipments/${id}/line-options`),
  updateShipmentPath: (id: string, body: ShipmentPathUpdate, key?: string) => write<ShipmentTransportPath>(`/api/v1/shipments/${id}/path`, 'PUT', body, key),
  shipmentPathHistory: (id: string, page = 1, page_size = 20) => request<PathHistoryPage>(`/api/v1/shipments/${id}/path-history${query({ page, page_size })}`),
  updateShipmentDestination: (id: string, body: { expected_destination_station_id: number; destination_station_id: number; reason: string }, key?: string) => write<ShipmentDetail>(`/api/v1/shipments/${id}/destination`, 'PATCH', body, key),
  shipmentDestinationChanges: (id: string, page = 1, page_size = 20) => request<DestinationChangePage>(`/api/v1/shipments/${id}/destination-changes${query({ page, page_size })}`),
  shipmentTasks: (id: string, page = 1, page_size = 20) => request<ShipmentTaskHistoryPage>(`/api/v1/shipments/${id}/transport-tasks${query({ page, page_size })}`),
  shipmentSchedule: (id: string) => request<ScheduleResponse>(`/api/v1/shipments/${id}/schedule`),
  shipmentScheduleHistory: (id: string, page = 1, page_size = 20) => request<ScheduleHistoryPage>(`/api/v1/shipments/${id}/schedule-history${query({ page, page_size })}`),
  previewShipmentSchedule: (id: string, body: SchedulePreviewInput) => write<SchedulePreview>(`/api/v1/shipments/${id}/schedule/preview`, 'POST', body),
  confirmShipmentSchedule: (id: string, body: { preview_token: string; reason: string; acknowledged_warning_codes: string[] }, key?: string) => write<ScheduleResponse>(`/api/v1/shipments/${id}/schedule/confirm`, 'POST', body, key),
  updateAddress: (id: string, body: { sender_address: string; recipient_address: string }, key?: string) => write<ShipmentDetail>(`/api/v1/shipments/${id}/address`, 'PATCH', body, key),
  shipmentEvent: (id: string, event_type: string, station_id?: string, key?: string) => write<ShipmentDetail>(`/api/v1/shipments/${id}/events`, 'POST', { event_type, ...(station_id ? { station_id } : {}) }, key),
  tasks: (params: { page?: number; page_size?: number; task_no?: string; route_code?: string; status?: string } = {}) => request<TaskPage>(`/api/v1/transport-tasks${query(params)}`),
  task: (id: string) => request<TaskDetail>(`/api/v1/transport-tasks/${id}`),
  candidates: (route_code: RouteCode, page = 1, page_size = 100) => request<Page<Candidate>>(`/api/v1/transport-tasks/candidates${query({ route_code, page, page_size })}`),
  createTask: (body: { route_code?: RouteCode; expected_path_versions?: Record<string, number>; expected_arrival_at: string; shipment_ids: number[] }, key?: string) => write<TaskDetail>('/api/v1/transport-tasks/create', 'POST', body, key),
  taskAction: (id: string, action: 'depart' | 'arrive', key?: string, expected_schedule_revision?: number) => write<TaskDetail>(`/api/v1/transport-tasks/${id}/${action}`, 'POST', action === 'depart' && expected_schedule_revision ? { expected_schedule_revision } : undefined, key),
  cancelTask: (id: string, reason: string, key?: string) => write<TaskDetail>(`/api/v1/transport-tasks/${id}/cancel`, 'POST', { reason }, key),
  previewTaskCancel: (id: string, reason: string) => write<CancelPreview>(`/api/v1/transport-tasks/${id}/cancel-preview`, 'POST', { reason }),
  confirmTaskCancel: (id: string, body: { reason: string; expected_schedule_revision: number; cancel_token: string }, key?: string) => write<TaskDetail>(`/api/v1/transport-tasks/${id}/cancel`, 'POST', body, key),
}
