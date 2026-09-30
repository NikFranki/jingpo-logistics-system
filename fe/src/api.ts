export type Stage = 'PENDING_PICKUP' | 'PICKED_UP' | 'AT_STATION' | 'IN_TRANSIT' | 'OUT_FOR_DELIVERY' | 'SIGNED'
export type TaskStatus = 'PENDING_DEPARTURE' | 'IN_TRANSIT' | 'ARRIVED' | 'CANCELLED'
export type RouteCode = string
export type Page<T> = { items: T[]; total: number; page: number; page_size: number }
export type Action = { action: string; enabled: boolean; reason_code: string | null; reason: string | null }
export type Order = { id: string; order_no: string; product_name: string; quantity: number; sender_name: string; sender_address: string; recipient_name: string; recipient_address: string; region_code: string; status: string; created_at: string; updated_at: string }
export type OrderDetail = Order & { shipment: { id: string; shipment_no: string; stage: Stage } | null }
export type Shipment = { id: string; shipment_no: string; order_id: string; sender_address: string; recipient_address: string; region_code: string; stage: Stage; destination_station_id: string; last_scanned_station_id: string | null; created_at: string; updated_at: string }
export type TrackingEvent = { id: string; event_type: string; occurred_at: string; station_id: string | null; task_id: string | null }
export type ActiveTransportTask = { id: string; route_code: RouteCode; origin_station_id: string; destination_station_id: string; status: TaskStatus }
export type ShipmentDetail = Shipment & { active_transport_task: ActiveTransportTask | null; tracking_events: TrackingEvent[]; allowed_actions: Action[] }
export type TaskItem = { delay_monitoring_enabled: boolean; id: string; task_no: string; route_code: RouteCode; status: TaskStatus; expected_arrival_at: string; departed_at: string | null; arrived_at: string | null; cancelled_at: string | null; cancel_reason: string | null; created_at: string; delay_status: string; delay_minutes: number | null }
export type TaskDetail = Omit<TaskItem, 'delay_status' | 'delay_minutes'> & { origin_station_id: string; destination_station_id: string; shipments: { id: string; shipment_no: string; stage: Stage }[]; simulation_time: string; delay_status: string; delay_minutes: number | null; allowed_actions: Action[]; cancelled_at: string | null; cancel_reason: string | null }
export type TaskPage = Page<TaskItem> & { simulation_time: string }
export type ShipmentTaskHistory = { id: string; task_no: string; route_code: string; status: TaskStatus; origin_station_id: string; destination_station_id: string; cancelled_at: string | null; cancel_reason: string | null; released_at: string | null }
export type ShipmentTaskHistoryPage = Page<ShipmentTaskHistory>
export type Station = { id: string; code: string; name: string; enabled: boolean; allows_first_arrival: boolean; allows_delivery: boolean }
export type TransportRoute = { id: string; code: string; origin: Station; destination: Station; enabled: boolean; delay_monitoring_enabled: boolean }
export type StationInput = Omit<Station, "id">
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

function write<T>(path: string, method: 'POST' | 'PATCH', body?: unknown, key: string = crypto.randomUUID()) {
  return request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export const api = {
  clock: () => request<{ current_time: string }>('/api/v1/simulation/clock'),
  advance: (minutes: 30 | 120, key?: string) => write<{ current_time: string }>('/api/v1/simulation/clock/advance', 'POST', { minutes }, key),
  stations: () => request<Station[]>("/api/v1/stations"),
  routes: () => request<TransportRoute[]>("/api/v1/routes"),
  createStation: (body: StationInput, key?: string) => write<Station>("/api/v1/stations", "POST", body, key),
  updateStation: (id: string, body: Partial<Omit<StationInput, "code">>, key?: string) => write<Station>(`/api/v1/stations/${id}`, "PATCH", body, key),
  createRoute: (body: RouteInput, key?: string) => write<TransportRoute>("/api/v1/routes", "POST", body, key),
  updateRoute: (id: string, body: { enabled?: boolean; delay_monitoring_enabled?: boolean }, key?: string) => write<TransportRoute>(`/api/v1/routes/${id}`, "PATCH", body, key),
  orders: (params: { page?: number; page_size?: number; order_no?: string; shipment_no?: string; stage?: string } = {}) => request<Page<Order>>(`/api/v1/orders${query(params)}`),
  order: (id: string) => request<OrderDetail>(`/api/v1/orders/${id}`),
  createOrder: (body: OrderInput, key?: string) => write<Order>('/api/v1/orders/create', 'POST', body, key),
  updateOrder: (id: string, body: OrderInput, key?: string) => write<Order>(`/api/v1/orders/${id}`, 'PATCH', body, key),
  createShipment: (orderId: string, destination_station_id: number, key?: string) => write<ShipmentDetail>(`/api/v1/orders/${orderId}/shipment`, 'POST', { destination_station_id }, key),
  shipments: (params: { page?: number; page_size?: number; shipment_no?: string; stage?: string } = {}) => request<Page<Shipment>>(`/api/v1/shipments${query(params)}`),
  shipment: (id: string) => request<ShipmentDetail>(`/api/v1/shipments/${id}`),
  shipmentTasks: (id: string, page = 1, page_size = 20) => request<ShipmentTaskHistoryPage>(`/api/v1/shipments/${id}/transport-tasks${query({ page, page_size })}`),
  updateAddress: (id: string, body: { sender_address: string; recipient_address: string }, key?: string) => write<ShipmentDetail>(`/api/v1/shipments/${id}/address`, 'PATCH', body, key),
  shipmentEvent: (id: string, event_type: string, station_id?: string, key?: string) => write<ShipmentDetail>(`/api/v1/shipments/${id}/events`, 'POST', { event_type, ...(station_id ? { station_id } : {}) }, key),
  tasks: (params: { page?: number; page_size?: number; task_no?: string; route_code?: string; status?: string } = {}) => request<TaskPage>(`/api/v1/transport-tasks${query(params)}`),
  task: (id: string) => request<TaskDetail>(`/api/v1/transport-tasks/${id}`),
  candidates: (route_code: RouteCode, page = 1) => request<Page<Candidate>>(`/api/v1/transport-tasks/candidates${query({ route_code, page, page_size: 100 })}`),
  createTask: (body: { route_code: RouteCode; expected_arrival_at: string; shipment_ids: number[] }, key?: string) => write<TaskDetail>('/api/v1/transport-tasks/create', 'POST', body, key),
  taskAction: (id: string, action: 'depart' | 'arrive', key?: string) => write<TaskDetail>(`/api/v1/transport-tasks/${id}/${action}`, 'POST', undefined, key),
  cancelTask: (id: string, reason: string, key?: string) => write<TaskDetail>(`/api/v1/transport-tasks/${id}/cancel`, 'POST', { reason }, key),
}
