# JINGPO V5 · BE 实施与验证记录

> 2026-09-30 · BE 已实现并通过隔离库验收；客户端独立交接。

依据：[PRD](../../../prd/v5/JINGPO-logistics-system-v5.md)、[spec](spec.md)。本轮仅修改 BE 和相关文档。

```mermaid
flowchart LR
    D[规则与契约] --> W[更正事务与操作资格]
    W --> H[更正历史接口]
    H --> T[并发、回滚、HTTP与历史回归]
    T --> C[文档与客户端交接]
```

## 1. 交付

| 步骤 | 当前状态与证据 |
| --- | --- |
| V5-S01 PRD/spec/plan、术语与 V6 后续规划 | 完成；V6 仅规划草案 |
| V5-S02 更正请求、事务、幂等、原站前提、资格 | 完成；shipments schemas/service/router |
| V5-S03 历史查询、分页与错误契约 | 完成；读取成功更正操作日志，不读取轨迹推断 |
| V5-S04 独立库业务/HTTP/历史迁移回归 | 完成；35 项联合测试通过，alembic check 无差异 |
| V5-S05 README、索引及交接 | 完成；本地服务真实只读检查通过 |
| V5-S06 FE 页面与 Agent 适配 | 由其负责方实施验收，本轮未修改/测试客户端 |

## 2. 验收证据

| PRD 项 | 验证内容 | 证据 |
| --- | --- | --- |
| V5-01 | 待揽收/已揽收/在站更正，到当前站后可派送、签收 | test_allowed_stages_history_and_local_delivery_without_address_changes；HTTP 全流程 |
| V5-02 | 任务占用与运输/派送/签收阶段拒绝，取消后可更正 | test_occupancy_and_stage_guards_cancel_then_correct；HTTP 阶段拒绝 |
| V5-03 | 校验原因/站点/原站，规范化重试与 key 冲突；竞争无覆盖 | test_normalized_idempotency_stale_precondition_and_target_guards；并发更正、任务创建、停用/取消派送能力竞争；HTTP 严格参数边界 |
| V5-04 | 日志失败回滚，事实及地址隔离 | 触发器注入日志 INSERT 失败，完整运单详情保持原值、无更正记录；更正前后比较阶段/地址/扫描站/轨迹/占用，订单地址与签收结果核验 |
| V5-05 | 原/新目的站、原因、演示时间，分页/空/404 | 业务与真实 HTTP 历史分页、时间有时区和字符串 ID 验证；只返回业务记录，不返回 key/hash |
| V5-06 | 历史兼容和 V2～V4 回归，无结构迁移 | 旧创建缓存缺少 UPDATE_DESTINATION 仍通过响应校验和真实服务重放；6 项历史迁移测试通过；alembic check 无新操作 |

## 3. 执行记录

从 V4 测试库导出 `/tmp/jingpo_v5_test_source.dump`，恢复到新建隔离库 `jingpo_logistics_v5_test`。数据库已有 V4 结构；V5 无新迁移文件，版本仍为 `d92f4b76e301`。

```bash
DATABASE_URL='postgresql+psycopg:///jingpo_logistics_v5_test' RUN_V2_MIGRATION_TESTS=1 \
  ./.venv/bin/python -m unittest discover -s tests -v
DATABASE_URL='postgresql+psycopg:///jingpo_logistics_v5_test' \
  ./.venv/bin/python -m alembic check
git diff --check
```

- 全量结果：35 项通过，约 10 秒。原 V2～V4 27 项 + V5 6 项业务/事务测试、2 项真实 HTTP 测试。
- HTTP 覆盖 404/409/422/503、Request-ID、严格整数（拒绝字符串/布尔/浮点）、原因空白/长度/类型/额外字段、幂等规范化、过期前提、无变化拒绝、派送与签收、历史分页及不泄露 key/hash。
- 真实竞争验证：同一原站前提的两次更正只有一个成功；更正为当前站与创建出站任务只有一个成功；新目的站更正与停用/移除派送能力只有一个成功。旧站保护随目的站变化释放，新站未签收运单保护生效。
- 注入更正日志写入故障后，目的站/更新时间回滚、轨迹和关联不变；相同 key 在故障清理后可成功重试。
- `alembic check`：No new upgrade operations detected。未修改已发布迁移、旧 key/hash 或历史缓存，不执行开发库测试写操作。
- 文档链接和 `git diff --check` 通过；未提交 Git 变更。

## 4. 运行与客户端交接

本地后端继续在 `be` 目录以 reload 运行，`http://127.0.0.1:8000`。真实只读核验 `/health/ready`、OpenAPI 两个 V5 路径、运单 UPDATE_DESTINATION 资格和 destination-changes 查询通过；未调用开发库更正接口，截图中的运单没有被自动改动。

V5 无需备份或迁移数据库；其他不使用 reload 的环境更新代码后需重启后端。客户端写操作后刷新详情和更正历史，以当前资格为准，不能把缓存重放当作当前目的站。客户端显示原站前提并让用户确认，遇到 409 先刷新再确认，不自动重试覆盖。

FE 提供更正弹窗与独立历史展示，Agent 增加只读历史查询并正确区分运输事实和运输安排；本轮没有修改或验收这些客户端。完整运输路径、下一段任务推荐和未来路径变更见 [V6 规划](../../../prd/v6/JINGPO-logistics-system-v6.md)，尚未实现。
