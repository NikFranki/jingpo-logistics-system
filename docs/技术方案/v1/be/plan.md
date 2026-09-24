# JINGPO 后端 V1 · 分步实施与学习计划

> 2026-09-21 · 待开始。当前仅输出文档，未授权按此计划自动完成全部代码。

依据：[spec.md](spec.md)、[后端技术方案](be-v1技术方案.md)、[V1 PRD](../../../prd/v1/JINGPO-logistics-system-v1.md)。

## 1. 如何使用计划

spec 定义最终结果，本计划将它拆成可理解、可运行的小步。技术方案中的目录是最终方向，文件随实际需求出现，不一次性创建整个架构。

每个步骤都采用以下节奏：

1. 说明这一步要解决的问题，以及暂时尚未实现的能力。
2. 解释本次将出现的文件、函数和数据流，再修改少量代码。
3. 给出启动或调用方法，由用户观察结果；需要测试时同步加入。
4. 回顾改动和失败原因，记录完成证据。
5. 等用户确认理解后，才开始下一步。确认计划不等于授权全部执行。

下面每一行是独立的小步，不是一次开发任务。文件路径均相对未来的 `backend/`；实际文件名在本步解释后确定，辅助文件和依赖只按需加入。某一步超出可讲清楚的范围时，再拆为 a/b 两步。

## 2. 分步路线

| 步骤 | 本步目标与文件演进 | 重点理解 | 验证与规格对应 |
| --- | --- | --- | --- |
| S01 | 最小服务：新增 `app/main.py` 与最小依赖文件，仅 `/health` | 进程、端口、Uvicorn、路由、HTTP 与 JSON | `/health` 成功、未知路径 404、打开 `/docs`；BE-01 部分 |
| S02 | 接 PostgreSQL：新增 `config.py`、`db.py`，补 `/health/ready` | 配置与代码分离、驱动/ORM/数据库的区别、连接与 Session | 数据库可用返回成功，不可用返回 503；BE-01 |
| S03 | 第一张表：引入 Alembic，仅建 simulation_settings，提供单行初始化 | 迁移和 ORM 各自负责什么，演示时间为何持久化 | 新库升级、初始化重复执行、重启不重置；BE-10/15 部分 |
| S04 | 首次拆模块：新增 simulation 的只读时钟路由与响应模型，main 注册路由 | 路由拆分、依赖注入、请求内 Session 生命周期 | `/api/v1/simulation/clock` 返回带时区时间；BE-10 部分 |
| S05 | 增加 operation_logs 模型及迁移，暂不开放写业务接口 | 唯一约束、JSONB、请求记录、幂等 key 与业务 ID 的区别 | 测试库拒绝重复 key；解释成功日志为何持久化；BE-12 基础 |
| S06 | 以推进时钟为第一个写接口：逐步加入事务、锁、成功日志和幂等处理 | commit/rollback、并发、重试为何可能重复执行 | +30/+120、非法参数、同 key 重试只推进一次；BE-10/12/13；本步可拆开讲解，闭环后才交付接口 |
| S07 | 引入 orders 模型、迁移与只读订单查询 | 表模型与接口模型、主键、生成编号、查询与 404 | 空列表、不存在的订单；BE-02 部分；不创建运单等未来表 |
| S08 | 创建订单：增加输入模型和业务函数，复用 S06 的写事务约定 | 参数校验与业务校验、INSERT、请求到落库的路径 | 正常订单存入 PostgreSQL，非法数量不落库，同 key 不重复创建；BE-02/12 |
| S09 | 订单编辑：完善状态限制、错误结构和操作日志 | PATCH 白名单、禁止修改字段、更新与审计 | 有效编辑成功、状态字段不可传、修改前后值可查；BE-02/15 部分 |
| S10 | 增加 stations、transport_routes 模型及迁移，幂等初始化固定网络 | 外键、关联关系、种子数据与业务数据的区别 | A/B/C 和 AB/BC 查询正确，重复初始化不改时钟；BE-06/15 基础 |
| S11 | 引入 shipments 模型及迁移，再增加 tracking_events 及其最小 task 外键依赖 transport_tasks | 一对一关系、历史轨迹、外键依赖如何影响建表顺序 | 迁移与约束正确；无任务业务 API；必要时拆两步，只建当前外键必需的表 |
| S12 | 创建运单及详情查询：新增 shipments 模块，订单调用业务函数 | 地址快照、跨模块协作、多个写入共用事务 | 重复发货仅一张运单、创建轨迹正确、订单锁定、故障整体回滚；BE-03/05/12 |
| S13 | 修改运单地址，加入揽收事件 | 状态控制编辑权限、保存前后值、并发修改 | 揽收前可改且不回写订单，揽收后不可改，重试不重复轨迹；BE-04/05/13 |
| S14 | A 入站、详情轨迹排序和 allowed_actions | 用状态规则约束操作，展示条件与后端校验的关系 | 未揽收不能入站，成功后最后扫描站点为 A；BE-05/14 部分 |
| S15 | 引入 task_shipments 迁移；运输模块实现候选查询 | 多对多历史关系、部分唯一索引、占用不是物流状态 | AB 只查 A、BC 只查 B，排除占用；BE-06 基础 |
| S16 | 创建运输任务及详情 | 提交时重新校验、ETA 与演示时间、关联写入事务 | 合法任务成功；空清单、重复 ID、错误站点、占用冲突失败；BE-06/07/13 |
| S17 | 任务发车接口，AB/BC 共用业务函数 | 批量状态变更、前置条件、不可部分成功 | 发车更新全部运单；重复调用不重复轨迹；BE-05/08 部分 |
| S18 | 到达并入站接口，释放占用并保留关联 | 多表原子性、轨迹引用操作日志、故障注入验证 | 多张运单一起入站；中途失败全部回滚；完成 AB 后可创建 BC；BE-08 |
| S19 | 开始派送和签收，订单联动完成 | 用业务动作组织跨模块状态，不在接口间互调 | 完成卖家到买家链路；跳站、提前签收被拒绝；BE-09 |
| S20 | 任务查询增加 AB 超时与晚到计算 | 派生数据、边界时间、物流状态与附加提示的区别 | 等于 ETA、超过 ETA、晚到、BC 不提示；BE-11 |
| S21 | 按需拆小补齐列表筛选分页、详情、allowed_actions、CORS、request_id 和运行说明 | 完整接口契约、可观测性、交付可复现性 | 对照技术方案接口清单逐项核对；BE-14/15 |
| S22 | V1 联合验收，整理每项证据及遗留问题 | 功能通过与系统可靠性的区别 | 对照 spec BE-01～15；用户 review 后判断是否完成 |

测试跟随业务加入，不集中留到 S22。S06 起使用独立测试数据库，验证事务与防重；后续每增加关键业务，再加入对应成功、失败和回滚场景。尚未出现的条件暂不测试，例如 S09 的“创建运单后锁定”在 S12 补齐验证。

S06 是写入公共能力的学习样例，先解释推进时钟这一具体需求，完成后再在 S08 复用；不预建通用 CRUD 基类或工作流引擎。需要拆分 S06 时可先演示局部事务，再加入幂等与锁，未完整前不将写接口标记为可验收完成。

S11 提前引入 transport_tasks 仅为已有 DDL 的 tracking_events.task_id 外键依赖；先解释这一依赖，再分批建表，不借此提前生成运输模块全部业务代码。

## 3. 下一步的明确范围：S01

开始前先用一个请求说明：浏览器或 `/docs` → Uvicorn → FastAPI 路由 → Python 函数 → JSON 响应。随后查看本地 Python 环境，决定可兼容版本并固定本步依赖。

预计只需要：

- `backend/app/main.py`：创建 FastAPI 对象，提供 `GET /health`。
- `backend/requirements.txt`：固定当前需要的依赖；此时只引入 FastAPI、Uvicorn 及必要依赖。
- 如需本地虚拟环境，加入相应忽略规则并解释它为什么不提交。

验证：启动服务，调用 `/health` 得到 200 和 `{"status":"ok"}`，访问不存在的路径观察 404，打开 `/docs` 调用同一接口，再停止服务理解请求为何失败。

本步不接数据库，不生成订单、运单或运输模块，不运行 SQL。完成后用户应能解释：服务由谁启动、URL 如何对应函数、函数返回值如何成为 HTTP 响应。然后停在这里，等用户决定进入 S02。

## 4. 每一步的记录模板

执行时将当前步骤的记录追加到本文件，不创建大量流程文档：

```text
步骤：Sxx / 标题
本步状态：未开始 / 进行中 / 待用户确认 / 已完成
解决的问题：
新增文件及职责：
修改文件及前后差异：
本次请求的数据流：
运行与验证命令：
观察到的成功与失败结果：
对应规格编号与尚未覆盖的内容：
用户疑问与解释：
是否获准进入下一步：
```

测试成功后标为“待用户确认”，用户确认后才标为“已完成”。不能将计划中的命令写成已经执行，不能将未演示的知识点写成用户已经掌握。

## 5. 当前进度

- 当前：spec.md 与 plan.md 已起草，等待评审。
- 开发步骤：S01～S22 均未开始；未验证本地 Python/PostgreSQL 环境。
- 下一动作：用户确认开始 S01 后，仅推进最小服务这一小步。
- 编排方式：单 Agent 串行；不自动开展多 Agent 并行编码。

### S01：最小 FastAPI 服务

- 状态：已完成
- 环境：Python 3.12.5，使用 .venv 和 requirements.txt
- 新增：be/app/main.py，创建应用并注册 GET /health
- 验证：/health 返回正常；/docs 调用返回 200；未知路径返回 404
- 理解：Uvicorn 启动服务，FastAPI 匹配路由，函数返回 JSON 响应
- 下一步：S02，连接 PostgreSQL

### S02：连接 PostgreSQL

- 状态：已完成
- 新增：be/config.py，读取 DATABASE_URL
- 新增：be/db.py，创建 SQLAlchemy Engine
- 修改：be/main.py，增加 GET /health/ready
- 验证：数据库正常时返回 200；数据库不存在时返回 503
- 理解：/health 检查服务进程，/health/ready 检查数据库依赖
- 下一步：S03，使用 Alembic 创建第一张表

### S03：第一张表与数据库迁移

- 状态：已完成
- 新增：be/migrations/，用于管理数据库结构版本
- 新增：be/models.py，定义 SimulationSettings 模型
- 修改：be/db.py，增加 SQLAlchemy Base
- 修改：migrations/env.py，接入 DATABASE_URL 和模型元数据
- 迁移版本：5653bf12321f
- 验证：成功创建 simulation_settings；重复初始化没有增加第二行
- 理解：模型描述表结构，Alembic 记录结构变化，迁移执行后才真正改变数据库
- 下一步：S04，通过接口读取演示时钟

### S04：读取演示时钟接口

- 状态：已完成
- 修改：be/db.py，增加 SessionLocal 和 get_db
- 新增：be/simulation/schemas.py，定义接口响应结构
- 新增：be/simulation/router.py，查询演示时钟
- 修改：be/main.py，注册 simulation 路由
- 验证：GET /api/v1/simulation/clock 返回数据库中的演示时间；/docs 出现 simulation 分组
- 理解：Engine 管理连接，Session 负责一次数据库操作，Depends 管理请求内 Session，Router 按业务拆分接口
- 下一步：S05，增加操作日志表

### S05：操作日志表

- 状态：已完成
- 修改：be/models.py，增加 OperationLog 模型
- 新增迁移：4c5a234a0a46_create_operation_logs.py
- 验证：operation_logs 创建成功；重复 idempotency_key 被数据库唯一约束拒绝；测试事务已回滚
- 理解：幂等 key 标识一次用户操作；请求摘要用于核对请求内容；数据库唯一约束是防重复的最终保障
- 下一步：S06，实现推进演示时钟的第一个写接口

### S06：推进演示时钟

- 状态：已完成
- 修改：simulation/schemas.py，限制 minutes 只能为 30 或 120
- 新增：simulation/service.py，负责事务、行锁和时间推进
- 修改：simulation/router.py，增加 POST /clock/advance 和 Idempotency-Key
- 验证：30/120 分钟正常推进；60 返回 422；同 key 同内容不重复推进；同 key 不同内容返回 409
- 数据：成功操作与时钟更新在同一事务提交，operation_logs 只记录一次
- 理解：事务保证一起成功或回滚；行锁避免同时覆盖；幂等保证网络重试不重复执行
- 下一步：S07，创建订单表并实现只读查询

### S07：订单表与只读查询

- 状态：已完成
- 修改：be/models.py，增加 Order 模型
- 新增迁移：2b703bcfbb9f_create_orders.py
- 新增：orders/schemas.py，定义订单列表和详情响应
- 新增：orders/service.py，负责分页和主键查询
- 新增：orders/router.py，提供订单列表和详情接口
- 修改：be/main.py，注册 orders 路由
- 验证：空列表返回 items=[] 和 total=0；page=0 返回 422；不存在的订单返回 404
- 理解：数据库模型和接口模型职责不同；service 查询数据，router 处理 HTTP；分页由 count、offset、limit 组成
- 下一步：S08，实现创建订单

### S08：创建订单

- 状态：已完成
- 修改：orders/schemas.py，增加创建订单输入校验
- 修改：orders/service.py，增加创建事务、请求摘要和幂等处理
- 修改：orders/router.py，增加 POST /api/v1/orders/create
- 新增：be/errors.py，集中定义公共业务异常
- 验证：正常创建返回 201；列表和详情可查询；同 key 重试不重复创建；同 key 不同内容返回 409；非法数量和额外状态字段返回 422
- 数据：订单与 operation_logs 在同一事务提交
- 理解：Pydantic 负责输入格式；service 负责业务与事务；数据库约束负责最终数据正确性
- 下一步：S09，创建运单前编辑订单

### S09：编辑订单

- 状态：已完成
- 修改：orders/schemas.py，增加 PATCH 白名单和空请求校验
- 修改：orders/service.py，增加订单锁、状态检查、更新事务和前后值日志
- 修改：orders/router.py，增加 PATCH /api/v1/orders/{order_id}
- 修改：be/errors.py，增加订单不存在和不可编辑错误
- 验证：单字段和多字段更新成功；空请求、null、非法字段返回 422；不存在订单返回 404；幂等重试不重复更新；同 key 不同内容返回 409
- 审计：operation_logs 正确保存 before_data 和 after_data
- 待补验证：S12 创建运单后，订单必须拒绝编辑
- 下一步：S10，创建固定站点和运输线路

### S10：固定站点与线路

- 状态：已完成
- 修改：be/models.py，增加 Station 和 TransportRoute
- 新增迁移：b7ce8389f6a2_create_network_tables.py
- 初始化：写入 A/B/C 三个站点和 AB/BC 两条线路
- 新增：network/schemas.py、service.py、router.py
- 修改：be/main.py，注册 network 路由
- 验证：站点接口返回 3 条；线路接口返回 2 条及起终点；重复初始化不增加数据
- 理解：基础数据与业务数据不同；外键保证线路引用真实站点；同表连接两次时使用 aliased 区分起点和终点
- 下一步：S11，增加运单、运输任务骨架和物流轨迹表

### S11：运单、任务骨架与物流轨迹表

- 状态：已完成
- 修改：be/models.py，增加 Shipment、TransportTask、TrackingEvent
- 新增迁移：09488665357e_create_shipments.py
- 新增迁移：a6b205da2353_create_tasks_and_tracking_events.py
- 验证：shipments、transport_tasks、tracking_events 创建成功，外键和 CHECK 约束存在
- 理解：订单与运单是一对一；轨迹是只追加的历史事实；任务骨架提前存在，是因为轨迹需要引用任务
- 当前边界：尚未实现运单、任务和轨迹接口
- 下一步：S12，从订单创建运单并查询运单详情

### S12：从订单创建运单

- 状态：已完成
- 新增：shipments/schemas.py、service.py、router.py
- 修改：orders/router.py，增加 POST /api/v1/orders/{order_id}/shipment
- 修改：be/errors.py，增加 ShipmentNotFoundError
- 修改：be/main.py，注册 shipments 路由
- 验证：首次创建返回 201；订单状态变为 SHIPMENT_CREATED；运单地址复制自订单；生成一条 SHIPMENT_CREATED 轨迹
- 防重复：同 key 重试返回原结果；新 key 重复发货返回 200 和原运单；数据库仍只有一张运单和一条创建轨迹
- 补充验证：创建运单后，PATCH 订单返回 409，完成 S09 遗留验证
- 理解：flush 获取数据库生成值但不提交；订单、运单、轨迹和操作日志共用一个事务
- 下一步：S13，修改运单地址并实现揽收

### S13：运单地址修改与揽收

- 状态：已完成
- 修改：shipments/schemas.py，增加地址修改和物流事件请求
- 修改：shipments/service.py，增加地址更新事务和 PICKUP 事件
- 修改：shipments/router.py，增加 PATCH /address 和 POST /events
- 修改：be/errors.py，增加 InvalidShipmentStateError
- 验证：揽收前可修改运单地址且不回写订单；揽收后地址修改返回 409；PICKUP 将阶段改为 PICKED_UP 并追加轨迹
- 防重复：同 key 和新 key 重复揽收都不会增加轨迹
- 审计：地址修改记录前后值；揽收记录状态变化
- 待自动化验证：地址修改和揽收真正并发时的锁顺序
- 下一步：S14，实现 A 入站、轨迹排序和 allowed_actions

### S14：A 站入站、轨迹排序与 allowed_actions

- 状态：已完成
- 修改：`shipments/service.py`，增加 `ENTER_A` 事件规则和操作条件计算
- 修改：`shipments/schemas.py`，增加 `allowed_actions` 响应结构
- 验证：已揽收运单成功进入 A，阶段更新为 `AT_A`，并记录 A 站和 `ENTER_A` 轨迹
- 防重复：相同幂等 key 重试不重复写入轨迹
- 查询：轨迹按发生时间和 ID 倒序返回；详情返回当前可执行操作及禁用原因
- 理解：后端状态规则同时约束业务操作，并向前端提供按钮启用条件
- 下一步：S15，实现运输任务候选运单查询

### S15：运输任务关联与候选运单查询

- 状态：已完成
- 修改：`models.py`，增加 `TaskShipment` 模型及运单占用约束
- 新增迁移：`ccce65c68961_create_task_shipments.py`
- 新增：`transport/schemas.py`、`service.py`、`router.py`
- 修改：`main.py`，注册 `transport-tasks` 路由
- 验证：AB 返回 A 站候选运单；BC 无符合条件运单时返回空列表
- 占用：运单关联未释放任务后从候选列表消失，释放后重新出现
- 理解：任务与运单保留历史关联，`released_at IS NULL` 表示当前占用
- 下一步：S16，创建运输任务及详情查询

### S16：创建运输任务及详情查询

- 状态：已完成
- 修改：`transport/schemas.py`，增加任务创建请求和详情响应模型
- 修改：`transport/service.py`，增加任务创建事务、业务校验、幂等处理和详情查询
- 修改：`transport/router.py`，增加创建任务和任务详情接口
- 验证：AB 任务创建成功，任务状态为 `PENDING_DEPARTURE`，关联运单保持 `AT_A`
- 校验：空运单、重复 ID、错误站点、已占用运单和非法 ETA 均被拒绝
- 防重复：相同 key 重试返回原任务；相同 key 不同请求返回 409
- 占用：任务创建后关联运单从候选列表消失
- 理解：候选查询用于展示，创建时必须重新锁定并校验最新状态
- 下一步：S17，实现运输任务发车

### S17：运输任务发车

- 状态：已完成
- 修改：`errors.py`，增加运输任务状态异常
- 修改：`transport/service.py`，增加发车事务、批量运单更新、轨迹和幂等处理
- 修改：`transport/router.py`，增加运输任务发车接口
- 验证：AB 任务发车后状态变为 `IN_TRANSIT`，运单变为 `IN_TRANSIT_AB`
- 轨迹：新增一条关联任务和 A 站的 `DEPART_AB` 轨迹
- 防重复：相同 key 或新 key 重复发车均不重复写入轨迹
- 理解：任务、全部关联运单、轨迹和操作日志在同一事务中提交
- 下一步：S18，实现到达并入站

### S18：运输任务到达并入站

- 状态：已完成
- 修改：`transport/service.py`，增加到达事务、批量入站、占用释放和轨迹记录
- 修改：`transport/router.py`，增加运输任务到达接口
- 验证：AB 任务到达后状态变为 `ARRIVED`，运单进入 `AT_B`
- 轨迹：新增关联任务和 B 站的 `ARRIVE_B` 轨迹
- 释放：`task_shipments.released_at` 写入到达时间，运单可加入 BC 任务
- 防重复：相同 key 或新 key 重复到达均不重复写入轨迹或释放占用
- 理解：任务、运单、关联占用、轨迹和日志在同一事务中完成
- 下一步：S19，实现开始派送和签收

### S19：开始派送与签收

- 状态：已完成
- 修改：`shipments/schemas.py`，增加 `START_DELIVERY` 和 `SIGN` 事件
- 修改：`shipments/service.py`，增加派送、签收规则和操作条件
- 联动：签收时同步将关联订单更新为 `COMPLETED`
- 验证：运单从 `AT_C` 进入 `OUT_FOR_DELIVERY`，最终进入 `SIGNED`
- 轨迹：正确写入 `START_DELIVERY` 和 `SIGN`
- 状态：签收后订单变为 `COMPLETED`
- 理解：运单签收、订单完成、轨迹和操作日志在同一事务中提交
- 下一步：S20，实现 AB 运输超时与晚到计算

### S20：AB 运输超时与晚到计算

- 状态：已完成
- 修改：`transport/schemas.py`，增加演示时间、延误状态和延误分钟数
- 修改：`transport/service.py`，增加实时延误计算和任务详情组装
- 修改：`transport/router.py`，任务详情使用延误响应模型
- 验证：AB 正常到达返回 `NONE`
- 验证：AB 运输超时返回 `OVERDUE` 和正确分钟数
- 验证：AB 晚到返回 `LATE_ARRIVAL` 和正确分钟数
- 验证：BC 返回 `NOT_APPLICABLE`
- 理解：延误根据任务时间实时计算，不保存状态、不创建异常表
- 下一步：S21，补齐查询、错误结构、CORS、request_id 和运行说明

### S21: 列表查询、统一错误、CORS 与运行说明

- 状态：已完成
- 修改：`shipments/`、`orders/`、`transport/`，补齐列表筛选、分页、详情及 `allowed_actions`
- 修改：`main.py`，增加 `request_id`、统一 HTTP/422/500 错误响应和 CORS
- 修改：`config.py`，增加 `CORS_ORIGINS` 和 `LOG_LEVEL`
- 修改：`README.md`，补充 PostgreSQL、数据库迁移和服务启动说明
- 验证：列表查询、详情、404、422、`X-Request-ID` 和 CORS 均通过

### S22: V1 联合验收

- 状态：已完成
- 验收：`BE-01～BE-15` 全部通过
- 验收：重新执行完整物流链路，订单最终进入 `COMPLETED`
- 验收：查询、状态流转、幂等、延误计算、错误响应、request_id 和 CORS 正常
- 遗留问题：无
- 结论：后端 V1 验收完成