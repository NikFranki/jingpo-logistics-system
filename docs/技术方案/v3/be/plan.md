# JINGPO V3 · 后端实施与验收记录

> 2026-09-30 · BE 实现与验收完成；开发库已备份、迁移并恢复服务。

依据：[PRD](../../../prd/v3/JINGPO-logistics-system-v3.md)、[spec](spec.md)。用户在本轮明确“只负责 be，前端的问题无需修改”，后续工作限定 BE。前端与 Agent 的独立交付不包含在本次完成声明中。

## 1. 后端交付顺序

```mermaid
flowchart LR
    M[数据模型与迁移] --> N[网络配置接口]
    N --> B[目的站与通用物流规则]
    B --> H[真实 HTTP 验证]
    H --> V[迁移与回归对账]
    V --> D[后端文档与交付]
```

| 步骤 | 后端交付 | 状态与证据 |
| --- | --- | --- |
| V3-S01 | PRD、spec、plan，明确首站能力、目的站、停用规则与延误快照 | 完成；用户 go ahead 后实施，后续限定 BE |
| V3-S02 | 通用编码及字段、V2→V3 迁移和幂等缓存兼容 | 完成；[迁移](../../../../be/migrations/versions/a83c9e14d602_configurable_network.py)、[V3 迁移演练](../../../../be/tests/test_v3_migration.py) |
| V3-S03 | 网络配置接口、日志、防重与停用保护 | 完成；[网络服务](../../../../be/network/service.py)、[配置测试](../../../../be/tests/test_v3_network.py) |
| V3-S04 | 目的站、动态首站/线路、候选、派送、延误快照 | 完成；新网络全链路、首站即目的站、错误派送与并发测试 |
| V3-S05～S06 | 前端与 Agent 适配 | 由其负责方推进；不纳入本次 BE 交付 |
| V3-S07 | BE 联合验收与文档 | 完成；16 项测试通过、alembic check 无差异、开发库版本核查 |

## 2. 可重复验证

测试库先升级到 `a83c9e14d602` 并初始化 A/B/C、AB/BC 和演示时钟；初始化 SQL 见 [BE README](../../../../be/README.md)。业务测试自动创建额外网络，不清理开发库。

```bash
cd be
DATABASE_URL='postgresql+psycopg:///jingpo_logistics_v3_test' RUN_V2_MIGRATION_TESTS=1 ./.venv/bin/python -m unittest discover -s tests -v
DATABASE_URL='postgresql+psycopg:///jingpo_logistics_v3_test' ./.venv/bin/python -m alembic check
```

结果：16/16 通过；模型检查返回 `No new upgrade operations detected`。测试分布：

| 文件 | 项数 | 验证内容 |
| --- | --- | --- |
| [V2 业务回归](../../../../be/tests/test_v2_flow.py) | 4 | A→B→C、地址隔离、重复发到、并发占用、AB 延误、批量故障回滚 |
| [历史链路迁移](../../../../be/tests/test_v2_migration.py) | 2 | V1→当前 head，全阶段和事件映射、旧首次入站 key 重放、空库升级 |
| [V3 HTTP](../../../../be/tests/test_v3_api.py) | 2 | 真实进程与 HTTP，配置接口/错误/筛选、非 ABC 线路至签收、延误与旧任务完成 |
| [V3 迁移](../../../../be/tests/test_v3_migration.py) | 2 | V2 副本全阶段对账、目的站/监测回填、创建运单与发车旧 key 重放、坏线路和缺 C 回滚 |
| [V3 网络规则](../../../../be/tests/test_v3_network.py) | 6 | 新网络、目的站、首站能力、首站即目的站、日志/幂等、停用各分支及并发 |

迁移测试要求 PostgreSQL 角色具有 CREATE DATABASE 权限，自动创建并删除自己的临时库。未设置 opt-in 时四项迁移测试会明确跳过。HTTP 测试启动自己的本地后端子进程，继承测试数据库配置，结束后停止；不依赖开发服务器，也不需要额外 HTTP 客户端依赖。

## 3. 逐项 BE 验收

| PRD 验收 | 后端实际证据 | 结果 |
| --- | --- | --- |
| V3-01 新网络 | 新编码首站/中转/目的站及两段线路，全流程 9 条事件；真实 HTTP 单段新线路至签收 | BE 通过；页面由 FE 验收 |
| V3-02 配置维护 | 创建、改名、能力、启停；同 key 重放与不同内容拒绝；重复编码/起终点、自环、未知字段与空 PATCH 拒绝；日志前后值 | 通过 |
| V3-03 目的站 | 经过可派送中转站仍拒绝派送；到自身目的站成功；首站即目的站可直接派送；已达目的站不能再加入任务 | 通过 |
| V3-04 首站/候选 | 缺首站能力拒绝首次入站，关闭能力后拒绝；错误起点、被占用及未入站运单不能加入任务 | 通过 |
| V3-05 停用保护 | 有启用线路、在站货物、未完成任务、未签收目的运单分别拒绝站点停用；停用与新目的运单并发仅一个成功；停用线路不阻断既有发到 | 通过 |
| V3-06 延误快照 | 新线路超时 60 分钟、到达后晚到 60 分钟；任务创建后关闭线路监测仍监测旧任务，新任务不适用 | 通过 |
| V3-07 迁移 | 9 个历史阶段、45 条轨迹，事件和占用完整比较，旧任务及运单原字段逐字段保留；能力/目的站/监测回填；旧 key 无新增写入重放 | 通过 |
| V3-08 回归与契约 | V2 业务、并发与回滚回归；HTTP 新线路筛选及停用记录查询、必填目的站校验、统一错误结构和 request_id | BE 通过；客户端由其负责方验收 |

## 4. 接口交接

详见 [spec 第 3 节](spec.md)。需要客户端适配的关键变化：创建运单增加必填正整数 `destination_station_id`；运单详情/列表/候选增加目的站；站点增加启用与能力字段；线路增加启用/监测字段；任务详情/列表/写响应增加 `delay_monitoring_enabled`。线路编码不再只有 AB/BC。

GET 网络列表默认含停用数据供历史名称解析；创建业务可传 enabled=true 并按能力筛选。目的站、任务线路终点、最后扫描站点是三个不同含义。

## 5. 发布与回退

开发库 `jingpo_logistics` 经查询仍为 `f714e269c2db`（V2），本轮没有执行 V3 开发库迁移。验证库为独立 `jingpo_logistics_v3_test`，由 V2 测试库副本升级；临时迁移演练库由测试自动清理。

用户切换开发环境时：停止旧服务 → 在 be/backups 备份 V2 库 → Alembic upgrade head → 启动 V3 后端 → 客户端同步适配。命令见 BE README。新网络无法无损还原为 V2 固定约束，downgrade 明确拒绝，回退使用升级前备份。

## 6. 开发库实际切换

2026-09-30，用户授权备份和迁移后：暂时停止本地后端，将 V2 数据库备份到 `be/backups/jingpo_logistics_before_v3_20260930_114214.dump`（43,185 字节），pg_restore --list 成功且 Git 忽略生效。升级 `jingpo_logistics` 至 `a83c9e14d602`，alembic check 无差异；历史运单目的站 C、历史任务监测快照的异常回填数量均为 0。后端 8000 已以 --reload 恢复，健康、站点、线路、运单及任务五项只读 HTTP 检查均通过。第 5 节记录的是此前交付时状态；本节为实际切换记录。
