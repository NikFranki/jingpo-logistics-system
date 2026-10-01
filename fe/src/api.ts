export type Stage = 'PENDING_PICKUP' | 'PICKED_UP' | 'AT_STATION' | 'IN_TRANSIT' | 'OUT_FOR_DELIVERY' | 'SIGNED'
export type TaskStatus = 'PENDING_DEPARTURE' | 'IN_TRANSIT' | 'ARRIVED' | 'CANCELLED'
export type RouteCode = string
export type Page<T> = { items: T[]; total: number; page: number; page_size: number }
export type Action = { action: string; enabled: boolean; reason_code: string | null; reason: string | null }
export type Order = { id: string; order_no: string; product_name: string; quantity: number; sender_name: string; sender_address: string; recipient_name: string; recipient_address: string; region_code: string; status: string; created_at: string; updated_at: string }
export type OrderDetail = Order & { shipment: { id: string; shipment_no: string; stage: Stage } | null }
export type Shipment = { id: string; shipment_no: string; order_id: string; sender_address: string; recipient_address: string; region_code: string; stage: Stage; destination_station_id: string; last_scanned_station_id: string | null; created_at: string; updated_at: string }
export type PathLegState = 'PENDING' | 'RESERVED' | 'IN_TRANSIT' | 'ARRIVED'
export type TransportPathStatus = 'WAITING_FIRST_ARRIVAL' | 'NEEDS_PLANNING' | 'READY' | 'RESERVED' | 'IN_TRANSIT' | 'COMPLETED' | 'BLOCKED'
export type PathLeg = { id: string; position: number; route_id: string; route_code: string; origin_station_id: string; destination_station_id: string; state: PathLegState; task_id: string | null }
export type ShipmentTransportPath = { version: number; status: TransportPathStatus; anchor_station_id: string | null; destination_station_id: string; next_route_id: string | null; next_route_code: string | null; reason_code: string | null; reason: string | null; legs: PathLeg[] }
export type PathPlan = { id: string; code: string; name: string; enabled: boolean; version: number; origin_station_id: string; destination_station_id: string; usable: boolean; reason: string | null; route_ids: string[] }
export type PathOptions = { path: ShipmentTransportPath; plans: PathPlan[] }
export type PathVersionLeg = Pick<PathLeg, 'id' | 'position' | 'route_id' | 'route_code' | 'origin_station_id' | 'destination_station_id'>
export type PathVersion = { version: number; destination_station_id: string; source_plan_id: string | null; source_plan_version: number | null; reason: string; occurred_at: string; legs: PathVersionLeg[] }
export type PathHistoryPage = Page<PathVersion>
export type TrackingEvent = { id: string; event_type: string; occurred_at: string; station_id: string | null; task_id: string | null }
export type ActiveTransportTask = { id: string; route_code: RouteCode; origin_station_id: string; destination_station_id: string; status: TaskStatus }
export type ShipmentDetail = Shipment & { active_transport_task: ActiveTransportTask | null; tracking_events: TrackingEvent[]; allowed_actions: Action[]; path_version?: number; transport_path?: ShipmentTransportPath | null }
export type TaskItem = { delay_monitoring_enabled: boolean; id: string; task_no: string; route_code: RouteCode; status: TaskStatus; expected_arrival_at: string; departed_at: string | null; arrived_at: string | null; cancelled_at: string | null; cancel_reason: string | null; created_at: string; delay_status: string; delay_minutes: number | null }
export type TaskDetail = Omit<TaskItem, 'delay_status' | 'delay_minutes'> & { origin_station_id: string; destination_station_id: string; shipments: { id: string; shipment_no: string; stage: Stage }[]; simulation_time: string; delay_status: string; delay_minutes: number | null; allowed_actions: Action[]; cancelled_at: string | null; cancel_reason: string | null }
export type TaskPage = Page<TaskItem> & { simulation_time: string }
export type ShipmentTaskHistory = { id: string; task_no: string; route_code: string; status: TaskStatus; origin_station_id: string; destination_station_id: string; cancelled_at: string | null; cancel_reason: string | null; released_at: string | null }
export type ShipmentTaskHistoryPage = Page<ShipmentTaskHistory>
export type DestinationChange = { id: string; previous_destination_station_id: string; destination_station_id: string; reason: string; occurred_at: string }
export type DestinationChangePage = Page<DestinationChange>
export type Station = { id: string; code: string; name: string; enabled: boolean; allows_first_arrival: boolean; allows_delivery: boolean }
export type TransportRoute = { id: string; code: string; origin: Station; destination: Station; enabled: boolean; delay_monitoring_enabled: boolean }
export type PathPlanInput = { code: string; name: string; route_ids: number[]; enabled: boolean }
export type PathPlanUpdate = { expected_version: number; name?: string; route_ids?: number[]; enabled?: boolean }
export type ShipmentPathUpdate = { expected_version: number; expected_anchor_station_id: number; reason: string } & ({ plan_id: number; expected_plan_version: number } | { route_ids: number[] })
export type StationInput = Omit<Station, 'id'>
export type RouteInput = { code: string; origin_station_id: number; destination_station_id: number; enabled: boolean; delay_monitoring_enabled: boolean }
export type Candidate = Shipment & { last_scanned_station_id: string }
export type OrderInput = Pick<Order, 'product_name' | 'quantity' | 'sender_name' | 'sender_address' | 'recipient_name' | 'recipient_address'>

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

function query(values: Record<string, string | number | undefined>) {
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
    stationsRequest = request<Station[]>('/api/v1/stations').catch(error => {
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
    routesRequest = request<TransportRoute[]>('/api/v1/routes').catch(error => {
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
  clock: () => request<{ current_time: string }>('/api/v1/simulation/clock'),
  advance: (minutes: 30 | 120, key?: string) => write<{ current_time: string }>('/api/v1/simulation/clock/advance', 'POST', { minutes }, key),
  stations,
  routes,
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
  createShipment: (orderId: string, destination_station_id: number, key?: string) => write<ShipmentDetail>(`/api/v1/orders/${orderId}/shipment`, 'POST', { destination_station_id }, key),
  shipments: (params: { page?: number; page_size?: number; shipment_no?: string; stage?: string } = {}) => request<Page<Shipment>>(`/api/v1/shipments${query(params)}`),
  shipment: (id: string) => request<ShipmentDetail>(`/api/v1/shipments/${id}`),
  shipmentPath: (id: string) => request<ShipmentTransportPath>(`/api/v1/shipments/${id}/path`),
  shipmentPathOptions: (id: string) => request<PathOptions>(`/api/v1/shipments/${id}/path-options`),
  updateShipmentPath: (id: string, body: ShipmentPathUpdate, key?: string) => write<ShipmentTransportPath>(`/api/v1/shipments/${id}/path`, 'PUT', body, key),
  shipmentPathHistory: (id: string, page = 1, page_size = 20) => request<PathHistoryPage>(`/api/v1/shipments/${id}/path-history${query({ page, page_size })}`),
  updateShipmentDestination: (id: string, body: { expected_destination_station_id: number; destination_station_id: number; reason: string }, key?: string) => write<ShipmentDetail>(`/api/v1/shipments/${id}/destination`, 'PATCH', body, key),
  shipmentDestinationChanges: (id: string, page = 1, page_size = 20) => request<DestinationChangePage>(`/api/v1/shipments/${id}/destination-changes${query({ page, page_size })}`),
  shipmentTasks: (id: string, page = 1, page_size = 20) => request<ShipmentTaskHistoryPage>(`/api/v1/shipments/${id}/transport-tasks${query({ page, page_size })}`),
  updateAddress: (id: string, body: { sender_address: string; recipient_address: string }, key?: string) => write<ShipmentDetail>(`/api/v1/shipments/${id}/address`, 'PATCH', body, key),
  shipmentEvent: (id: string, event_type: string, station_id?: string, key?: string) => write<ShipmentDetail>(`/api/v1/shipments/${id}/events`, 'POST', { event_type, ...(station_id ? { station_id } : {}) }, key),
  tasks: (params: { page?: number; page_size?: number; task_no?: string; route_code?: string; status?: string } = {}) => request<TaskPage>(`/api/v1/transport-tasks${query(params)}`),
  task: (id: string) => request<TaskDetail>(`/api/v1/transport-tasks/${id}`),
  candidates: (route_code: RouteCode, page = 1, page_size = 100) => request<Page<Candidate>>(`/api/v1/transport-tasks/candidates${query({ route_code, page, page_size })}`),
  createTask: (body: { route_code?: RouteCode; expected_path_versions?: Record<string, number>; expected_arrival_at: string; shipment_ids: number[] }, key?: string) => write<TaskDetail>('/api/v1/transport-tasks/create', 'POST', body, key),
  taskAction: (id: string, action: 'depart' | 'arrive', key?: string) => write<TaskDetail>(`/api/v1/transport-tasks/${id}/${action}`, 'POST', undefined, key),
  cancelTask: (id: string, reason: string, key?: string) => write<TaskDetail>(`/api/v1/transport-tasks/${id}/cancel`, 'POST', { reason }, key),
}
