# BE

## V3 可配置网络

V3 后端支持站点和单向线路创建、编辑、启停。站点配置首次入站/派送能力；线路配置延误监测。运单创建必须人工指定目的站，到达自身目的站后才能派送。旧订单地址保持不变。

当前版本为 `a83c9e14d602`，详细接口与规则见 [V3 spec](../docs/技术方案/v3/be/spec.md)，测试和发布记录见 [V3 plan](../docs/技术方案/v3/be/plan.md)。前端与 Agent 是独立交付，本轮后续只负责 BE。

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
