# JINGPO V4 · BE 实施与验证记录

> 2026-09-30 · BE 已实现并在隔离库验证。开发库已备份升级并重启；FE 页面与 Agent 适配由其负责方独立验收。

依据：[PRD](../../../prd/v4/JINGPO-logistics-system-v4.md)、[spec](spec.md)。本轮只修改 BE 与相关文档。

## 1. 分步路线

```mermaid
flowchart LR
    R[PRD/spec/plan] --> M[任务状态与迁移]
    M --> C[取消与占用释放]
    C --> Q[操作资格与任务历史]
    Q --> V[HTTP、并发和迁移验收]
    V --> H[客户端交接]
```

| 步骤 | 交付 | 完成证据 | 当前状态 |
| --- | --- | --- | --- |
| V4-S01 | 产品规则、BE 契约与客户端交接 | PRD/spec/plan、状态图与验收表 | 完成 |
| V4-S02 | TaskStatus、取消字段与状态约束、历史缓存迁移 | d92f4b76e301；V3 副本升级、空库演练、旧 key 重放 | 完成 |
| V4-S03 | 取消接口、原因、批量释放、日志与幂等 | test_v4_cancellation.py、test_v4_api.py | 完成 |
| V4-S04 | 发到/延误/停用规则、操作资格、运单任务历史 | 生命周期资格、旧取消重试、竞争、历史分页测试 | 完成 |
| V4-S05 | BE HTTP 与迁移联合回归、README/项目索引 | 全量 27 项测试、alembic check 和 diff 检查 | 完成 |
| V4-S06 | FE 业务页面迁移及 Agent 只读适配 | 由其负责方记录构建、走查和查询验收 | 独立交接，本轮未验收 |

## 2. 验收清单

| 条目 | 必须证明 | 实际证据 |
| --- | --- | --- |
| V4-01 | 全批次释放、运单原地不变、重新建任务可履约 | 两张运单取消前后完整详情一致；重建并中转、派送、签收；订单地址不变 |
| V4-02 | 取消信息与原关联保留，无伪造轨迹，能查无事件任务 | 取消任务无 TrackingEvent；原关联 released_at=cancelled_at；查询含待发车/取消/到达记录 |
| V4-03 | 同/不同 key 及原因冲突；旧取消不影响新占用 | 空白规范化后同 key 返回首次快照；新 key 相同原因成功；不同原因拒绝；新任务运输中仍保持占用 |
| V4-04 | 状态拒绝、不可复活、竞争唯一成功、整批回滚 | 真实并发 depart/cancel 唯一成功；数据库触发器让第二张运单释放失败，状态/取消字段/两张占用/操作日志整体回滚 |
| V4-05 | BE 操作资格和业务接口交接 | 运单生命周期创建资格、任务 CANCEL 资格、HTTP 请求校验及成功后再查询通过；页面迁移未由本轮验收 |
| V4-06 | V3 历史迁移、缓存重放及正常业务回归 | 历史任务/运单/轨迹/关联对账，保留 key/hash；真实旧 create/depart/arrive 调用返回迁移后的原缓存；V2/V3 回归全通过 |

## 3. 执行记录

从 `jingpo_logistics_v3_test` 导出快照 `/tmp/jingpo_v4_test_source.dump`，恢复到新建的 `jingpo_logistics_v4_test`，升级到 `d92f4b76e301`。V4 新迁移只增加取消字段、替换任务状态/时间约束和补齐 JSON 缓存；不删除任务、关联、物流事件或日志。

```bash
DATABASE_URL='postgresql+psycopg:///jingpo_logistics_v4_test' RUN_V2_MIGRATION_TESTS=1 \
  ./.venv/bin/python -m unittest discover -s tests -v
DATABASE_URL='postgresql+psycopg:///jingpo_logistics_v4_test' \
  ./.venv/bin/python -m alembic check
git diff --check
```

- 27 项全通过：原 V2/V3 16 项 + V4 7 项业务事务、2 项真实 HTTP、2 项迁移。
- HTTP 验证取消及重试、状态冲突、非法原因/未知字段/无效 key、404/409/422/503、Request-ID、CANCELLED 筛选、任务历史分页。
- 6 项迁移测试在独立临时数据库执行并自行清理；V4 覆盖 V3→V4 数据对账、递归缓存新增字段/资格、空库升级和拒绝 downgrade。
- `alembic check`：No new upgrade operations detected。它验证迁移与 ORM 列/索引等结构；任务 CHECK 的实际约束另由非法 SQL 写入测试验证。
- 初次加入“时钟未初始化”测试时，恢复测试时钟的原始 SQL 未引用 `current_time` 导致测试清理失败；已改用 SQLAlchemy 表插入，恢复隔离库并重新执行全量回归通过。开发库未受影响。
- `git diff --check` 通过。未提交 Git 变更，未运行客户端测试。

## 4. 发布与客户端交接

开发库 `jingpo_logistics` 已按用户“备份、迁移、重启后端”的授权升级到 `d92f4b76e301`。执行步骤见 [BE README](../../../../be/README.md)。拒绝直接 downgrade；回退恢复升级前备份。

### 开发库发布记录（2026-09-30）

- 停止原后端，确认 8000 端口释放后备份。文件：`be/backups/jingpo_logistics_before_v4_20260930_134833.dump`，约 48 KB；`pg_dump -Fc` 成功，`pg_restore --list` 可正常读取。
- 升级前保存数据快照，执行 `alembic upgrade head`，从 `a83c9e14d602` 升至 `d92f4b76e301`；`alembic check` 无新增操作。
- 升级后逐行对账：9 个订单、9 张运单、12 个任务、14 条任务关联、65 条物流轨迹、6 个站点、5 条线路、演示时钟原字段保持一致；115 条操作日志的 ID、key/hash、动作、资源和时间保持一致。新增取消字段与缓存兼容按迁移处理。
- 在 `be` 目录启动 `./.venv/bin/python -m uvicorn main:app --reload`，连接开发库，地址 `http://127.0.0.1:8000`，允许 localhost/127.0.0.1:5173。
- 真实只读 HTTP 检查通过：`/health/ready`、任务列表/详情取消字段与 CANCEL 资格、运单 CREATE_TRANSPORT_TASK 资格和关联历史、OpenAPI 取消路径及 localhost:5173 CORS。未向开发库写入测试业务数据。

FE 按 PRD 将业务操作放入订单、运单及任务页面，演示工具只保留时钟。请求成功后重新 GET 最新详情；幂等重放是历史快照，不能作为当前占用判断。历史任务 shipments 为原关联运单，但 stage 为其当前阶段。

Agent 继续只读，适配 CANCELLED、取消字段与任务历史接口。V4 整体页面全流程和真实 Agent 查询由客户端负责方另行验收；BE 通过不代表这些已通过。
