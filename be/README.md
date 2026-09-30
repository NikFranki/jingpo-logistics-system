# BE

## V5 运单目的站更正（后端已实现）

目的站更正只修改运输安排，订单、运单地址、当前位置与物流轨迹保持原样。允许待揽收、已揽收、无任务占用的在站运单更正；运输中、派送中、已签收拒绝。待发车任务需先取消。新站必须启用且允许派送。

- `PATCH /api/v1/shipments/{id}/destination`，要求 `Idempotency-Key`，返回最新运单详情。请求示例：

```json
{"expected_destination_station_id": 6, "destination_station_id": 5, "reason": "原目的站选择错误"}
```

两个 ID 必须为正整数，原因去首尾空白后为 1～500 字符。原目的站与当前不一致，或新站未变化，返回 409。相同 key 同内容重试返回首次缓存；不同内容返回 409。更正为当前所在站后允许开始派送，不自动派送。

- 运单详情 `allowed_actions` 新增 `UPDATE_DESTINATION`，不可用时区分阶段限制与任务占用。
- `GET /api/v1/shipments/{id}/destination-changes?page=1&page_size=20` 返回原站、新站、原因、演示时间；不是物流轨迹。

**本版没有数据库迁移**，结构版本仍为 `d92f4b76e301`。历史幂等缓存不回写，客户端操作成功后重新 GET 详情取得当前资格。现有本地后端使用 reload，可自动加载代码；其他环境需重启进程。没有自动更正开发库中的运单。

35 项联合后端测试通过，迁移结构无差异；验证命令沿用下方测试命令，将库名换为 `jingpo_logistics_v5_test`。客户端页面与 Agent 独立交接；详见 [V5 PRD](../docs/prd/v5/JINGPO-logistics-system-v5.md)、[spec](../docs/技术方案/v5/be/spec.md)、[验收记录](../docs/技术方案/v5/be/plan.md)。完整运输路径安排另见 [V6 规划草案](../docs/prd/v6/JINGPO-logistics-system-v6.md)。

## V4 待发车任务取消（后端已实现）

后端迁移版本为 `d92f4b76e301`。待发车任务可取消并解除全部运单占用；任务及关联历史保留，运单位置、地址、订单和物流轨迹保持原样。取消原因不能被后续重试覆盖。

### 新增契约

- `POST /api/v1/transport-tasks/{id}/cancel`，请求 `{"reason":"线路选择错误"}`，要求 `Idempotency-Key`，返回任务详情。原因去首尾空白后为 1～500 字符，仅 `PENDING_DEPARTURE` 可首次取消。
- 任务详情/列表/写响应增加 `cancelled_at`、`cancel_reason`；状态筛选支持 `CANCELLED`。取消任务延误为 `NOT_APPLICABLE`。
- 任务详情 `allowed_actions` 增加 `CANCEL`；运单详情增加 `CREATE_TRANSPORT_TASK`。资格用于页面提示，提交仍由服务端重新校验。
- `GET /api/v1/shipments/{id}/transport-tasks?page=1&page_size=20` 查询原关联历史，包含未发车的取消任务及各次关联的 `released_at`。

同 key 重试返回首次快照；成功后客户端重新 GET 详情。新 key 同原因重复取消成功且不再次释放，原因不同返回 409。业务入口迁入页面由 FE 负责，本次后端交付不代表页面验收。详见 [V4 spec](../docs/技术方案/v4/be/spec.md) 和 [验收记录](../docs/技术方案/v4/be/plan.md)。

### 从 V3 升级

开发库已于 2026-09-30 备份并升级到 V4，后端已重启且只读检查通过，记录见 V4 plan。以下步骤用于其他 V3 环境：停止后端后，在 be 目录备份，再执行：

```bash
mkdir -p backups
pg_dump -Fc jingpo_logistics -f "backups/jingpo_logistics_before_v4_$(date +%Y%m%d_%H%M%S).dump"
export DATABASE_URL="postgresql+psycopg:///jingpo_logistics"
./.venv/bin/python -m alembic upgrade head
./.venv/bin/python -m alembic current
./.venv/bin/python -m uvicorn main:app --reload
```

迁移保留历史数据、幂等 key/hash，并补齐旧缓存响应字段。历史运单缓存新增创建资格保守禁用并提示刷新；不会根据当前网络猜测历史资格。V4 拒绝 downgrade，回退使用升级前备份。

### 后端验证

```bash
DATABASE_URL="postgresql+psycopg:///jingpo_logistics_v4_test" RUN_V2_MIGRATION_TESTS=1 \
  ./.venv/bin/python -m unittest discover -s tests -v
DATABASE_URL="postgresql+psycopg:///jingpo_logistics_v4_test" \
  ./.venv/bin/python -m alembic check
```

测试库需升级到 head 并初始化演示网络与时钟（见后文）。迁移演练自动创建和清理临时库，需要 CREATE DATABASE 权限；HTTP 测试自动管理测试服务。未启用迁移演练时 6 项迁移测试跳过。禁止用开发库执行测试。

## V3 可配置网络

V3 后端支持站点和单向线路创建、编辑、启停。站点配置首次入站/派送能力；线路配置延误监测。运单创建必须人工指定目的站，到达自身目的站后才能派送。旧订单地址保持不变。

V3 对应版本为 `a83c9e14d602`，详细接口与规则见 [V3 spec](../docs/技术方案/v3/be/spec.md)，测试和发布记录见 [V3 plan](../docs/技术方案/v3/be/plan.md)。前端与 Agent 是独立交付，本轮后续只负责 BE。

### 从 V2 升级

在 be 目录执行；先停止旧后端并备份，数据库地址按实际环境调整：

```bash
mkdir -p backups
pg_dump -Fc jingpo_logistics -f "backups/jingpo_logistics_before_v3_$(date +%Y%m%d_%H%M%S).dump"
export DATABASE_URL="postgresql+psycopg:///jingpo_logistics"
./.venv/bin/python -m alembic upgrade head
./.venv/bin/python -m uvicorn main:app --reload
```

已有网络保留，A 允许首站入站、C 允许派送、旧运单目的站=C。既有 AB 任务保持监测延误、BC 保持不适用。V3 不支持无损 downgrade，回退需恢复升级前备份。开发库的迁移由用户执行；测试使用独立库。

### 主要接口

- `GET /api/v1/stations`、`GET /api/v1/routes` 默认包括停用配置，`?enabled=true` 只查询启用记录。
- `POST /api/v1/stations`、`PATCH /api/v1/stations/{id}` 维护站点。
- `POST /api/v1/routes`、`PATCH /api/v1/routes/{id}` 维护线路。
- `POST /api/v1/orders/{id}/shipment` 请求体为 `{"destination_station_id": 3}`，站点 ID 必须是实际启用的派送站。
- 首次入站沿用 `POST /api/v1/shipments/{id}/events`，请求体 `{"event_type":"ARRIVE","station_id":"1"}`，由站点能力校验，不再固定 A。
- 任务候选、创建和查询支持任意符合编码规则的线路；延误结果由任务监测快照决定。

写操作要求 `Idempotency-Key`。编码与线路起终点不可修改，不提供删除；停用线路不阻断已有任务，停用站点会校验在站货物、未完成任务、目的运单和启用线路。

### 验证

隔离的 `*_test` PostgreSQL 库须先升级到 head 并初始化演示时钟。原有 V2 回归测试还需要 A/B/C、AB/BC（初始化 SQL 见后文）：

```bash
DATABASE_URL="postgresql+psycopg:///jingpo_logistics_v3_test" RUN_V2_MIGRATION_TESTS=1 \
  ./.venv/bin/python -m unittest discover -s tests -v
DATABASE_URL="postgresql+psycopg:///jingpo_logistics_v3_test" \
  ./.venv/bin/python -m alembic check
```

16 项测试包含业务回归、V3 配置与并发、真实 HTTP 和迁移演练。迁移演练需要 CREATE DATABASE 权限，并自动清理自己创建的临时库；HTTP 测试自动启动并停止只连测试库的后端，无需手工启动服务。没有 opt-in 时 4 项迁移演练会跳过。业务测试保留样本，禁止使用开发库。

## V2 状态与事件迁移

V2 使用通用运单阶段 `AT_STATION`、`IN_TRANSIT` 和通用轨迹事件 `ARRIVE`、`DEPART`。A/B/C 仍是固定演示网络；揽收与首次入站分开。运单地址修改只影响运单，订单保留下单地址。

阶段表示货物所处环节，具体站点和线路从关联数据取得；运输中的最后扫描站点不是实时位置。任务发车、到达时，任务与全部关联运单的状态、轨迹、占用和操作日志必须在同一事务中更新，并保留幂等与业务防重规则。

业务含义见 [领域术语](CONTEXT.md)，完整规则与验收见 [V2 后端规格](../docs/技术方案/v2/be/spec.md)；未被 V2 改变的规则沿用 [V1 后端规格](../docs/技术方案/v1/be/spec.md)。

在升级已有 V1 数据库前先备份，并在副本上演练迁移。迁移会核对旧阶段、站点、任务关联，冲突数据会使升级失败且事务回滚。V2 阶段无法单凭字符串无损还原为 V1 的 `AT_A`、`AT_B` 等值；回退请恢复升级前备份。

```bash
export DATABASE_URL="postgresql+psycopg:///jingpo_logistics"
./.venv/bin/python -m alembic upgrade head
```

V2 当时的验收结果见其 plan；当前代码测试与数据库初始化请使用本文 V3 节。

V2 需求、验收和实施记录分别见 [PRD](../docs/prd/v2/JINGPO-logistics-system-v2.md)、[spec](../docs/技术方案/v2/be/spec.md)、[plan](../docs/技术方案/v2/be/plan.md)。

How to start?

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

# 迁移文件步骤
export DATABASE_URL="postgresql+psycopg:///jingpo_logistics"
python -m alembic revision \
  --autogenerate \
  -m "create task shipments"

# main 当前目录的 main.py 
# :app 文件里的 app = FastAPI() 对象
#  --reload 修改代码后自动重启
python -m uvicorn main:app --reload

# 加上 DATABASE_URL 环境变量
export DATABASE_URL="postgresql+psycopg://franki@localhost:5432/jingpo_logistics"
export DATABASE_URL="postgresql+psycopg:///jingpo_logistics"
python -m uvicorn main:app --reload

# 迁移目录
python -m alembic init migrations

# 初始化 Alembic
python -m alembic init migrations

# 查看当前数据库版本
python -m alembic current

# 根据模型（models.py）生成迁移文件
python -m alembic revision --autogenerate -m "create simulation settings"

# 预览迁移 SQL
python -m alembic upgrade head --sql

# 执行迁移
python -m alembic upgrade head

# 进入 PostgreSQL
psql -d jingpo_logistics

# 查看表结构
psql -d jingpo_logistics -c '\d simulation_settings'

# 初始化时钟
psql -d jingpo_logistics -c \
"INSERT INTO simulation_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;"

# 设置 cors origin
export CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"

# 迁移顺序：（修改模型 → 生成迁移 → review 迁移 → 预览 SQL → 执行迁移 → 检查数据库）
```

## 1. 创建环境

```bash
cd be
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 2. 启动 PostgreSQL

```bash
brew services start postgresql@17
brew services list
```

首次运行时创建数据库：

```bash
createdb jingpo_logistics
```

确认数据库可连接：

```bash
psql -d jingpo_logistics -c "SELECT 1;"
```

## 3. 配置环境变量

```bash
export DATABASE_URL="postgresql+psycopg:///jingpo_logistics"
export CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
export LOG_LEVEL="INFO"
```

## 4. 执行数据库迁移

```bash
python -m alembic upgrade head
python -m alembic current
```

### 初始化固定演示网络与时钟

新库迁移只创建结构。首次运行时在当前 `DATABASE_URL` 所指数据库执行以下 SQL；已有网络不用重复初始化：

```sql
INSERT INTO stations (code, name, allows_first_arrival, allows_delivery) VALUES
  ('A', 'A 分拣站', true, false), ('B', 'B 中转站', false, false), ('C', 'C 配送站', false, true)
ON CONFLICT (code) DO NOTHING;
INSERT INTO transport_routes (code, origin_station_id, destination_station_id, delay_monitoring_enabled)
SELECT route.code, origin.id, destination.id, route.code = 'AB'
FROM (VALUES ('AB', 'A', 'B'), ('BC', 'B', 'C')) AS route(code, source, target)
JOIN stations origin ON origin.code = route.source
JOIN stations destination ON destination.code = route.target
ON CONFLICT (code) DO NOTHING;
INSERT INTO simulation_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
```

测试库也需要此初始化；迁移测试自身会准备独立样本。

## 5. 启动服务

```bash
python -m uvicorn main:app --reload
```

访问：

- API 文档：http://127.0.0.1:8000/docs
- 存活检查：http://127.0.0.1:8000/health
- 数据库检查：http://127.0.0.1:8000/health/ready
