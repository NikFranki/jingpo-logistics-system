# JINGPO Agent V7 · 实施与验证计划

> 2026-10-09 · 实施记录。V7 查询工具和响应适配已写入工作区；受控测试与真实 BE 契约验收尚未完成。

## 1. 目标与边界

按 [V7 Agent 规格](spec.md) 补齐只读查询，使 Agent 能读取完整运输计划、计划历史、目的站更正记录和 V7 任务字段，并正确区分未来计划、当前执行、历史事实、预测与实际到达。

不改变 BE/FE，不调用任何写接口。保留现有 LangGraph、CLI、预算、会话及脱敏机制。遇到规格与运行中 BE/OpenAPI 不一致时，先记录差异并更新契约，不猜字段。

## 2. 工作顺序

| 步骤 | 工作内容 | 主要文件 | 验收证据 | 状态 |
| --- | --- | --- | --- | --- |
| 0 | 核对当前 BE router/schema、V7 契约和 Agent 基线；整理字段与分页上限 | `docs/技术方案/v7/be/client-contract.md`、`be/*/schemas.py`、`agent/responses.py` | 已核对本地 BE schemas/router；相关查询均为 GET。真实运行契约待后端启动后复核 | 已完成（代码核对） |
| 1 | 扩展订单、运单、任务响应模型及摘要；过滤姓名、详细地址 | `agent/responses.py`、`agent/tools.py` | 已实现模型和摘要字段；模块导入通过。受控字段断言待测 | 已实现 |
| 2 | 增加 schedule、schedule history、目的站更正历史客户端方法及响应模型 | `agent/be_client.py`、`agent/responses.py` | 已实现固定 GET 路径和分页校验；受控 HTTP/响应场景待测 | 已实现 |
| 3 | 增加只读计划与更正历史工具，并支持按需读取计划历史 | `agent/tools.py`、`agent/cli.py`、`agent/prompts.py` | 已注册两个新工具；只调用固定 GET；部分失败返回 partial | 已实现 |
| 4 | 延误查询合并当前 schedule、当前任务和轨迹历史任务并去重 | `agent/tools.py`、`agent/prompts.py` | 已实现来源标记、18 个任务上限和不完整提示；行为用例待测 | 已实现 |
| 5 | 更新回答规则及 README：状态、时间类型、过期预测、共享等待、时间窗、买家 ETA | `agent/prompts.py`、`agent/README.md` | 已更新；真实模型回答和受控场景待验收 | 已实现 |
| 6 | 覆盖新增工具、组合预算、截断、错误和隐私过滤的受控测试 | `agent/tests/` | 本轮未新增或运行测试；待后续测试验收 | 未验证 |
| 7 | 执行真实 BE 只读契约核验；按可用数据验证计划、历史和更正记录 | `agent/verify_backend.py`、V7 Agent plan | 现有 127.0.0.1:8000 进程未加载新路由；临时启动当前 BE 后通过 Agent 客户端验收全局更正接口，HTTP 200、响应校验通过 | 更正接口已验收；其他 V7 接口待查 |
| 8 | 记录验证状态并更新 README 与索引 | 本文、`agent/README.md`、根 `AGENTS.md` | 已标明实现、验证和待验收边界；真实模型评测未执行 | 已完成（文档状态） |
| 9 | 运单更正查询改为全局审计日志分页 | `be/shipments/*`、`agent/tools.py`、`agent/be_client.py`、`agent/responses.py` | 新增 GET 全局更正日志接口；每条直接关联运单 ID/编号；Agent 单页只发 1 次请求，不查运输任务 | 已实现，待 BE 场景验收 |

## 3. 工具与预算设计要点

- 一个运单查询最多读取详情、schedule、站点映射和按需历史页；每个实际 HTTP 调用都使用既有 `TurnContext` 预算和超时。
- 延误关联任务采用任务 ID 去重，来源注明 `schedule`、`active_transport_task`、`tracking_event`。如果同一任务出现在多个来源，只读取一次并保留全部来源标签。
- 计划历史、目的站更正历史按用户意图读取，不能为每个普通轨迹查询无条件扫描所有页。
- 设定单次返回项目数和总体工具消息上限。达到上限要返回 `truncated`、分页信息或明确未完成状态，不静默丢弃未来分段。
- schedule 与任务详情可能来自不同时间点；状态矛盾时可在预算内重读一次相关 GET，仍不一致则说明无法确认，不拼成确定事实。
- 结果摘要不包含姓名、详细地址、原始 HTTP body、token 或 preview/cancel 凭据。

## 4. 验证记录

2026-10-13 方案修正：目的站更正本身已写入 `operation_logs`，因此新增全局 GET `/api/v1/shipments/destination-changes`，按日志分页并关联运单编号。Agent 工具现在每页只发 1 次请求；确认更正事实不再遍历运单或查询运输任务。普通运单列表分页上限为 100。代码和导入检查已完成。2026-10-13 对现有 `127.0.0.1:8000` 只读请求收到 422，确认该运行进程未加载新增路由（已运行超过 3 天）。随后在临时端口启动当前 BE 代码，复用现有 BE 进程的数据库配置，通过 Agent `be_client` 请求 page=1/page_size=1：HTTP 200，响应模型校验通过，返回 total=1、1 条记录、request_id，Agent HTTP 计数为 1；临时进程已关闭，原 BE 未重启。现有 8000 服务需重启/部署后才能通过原地址使用新接口。

2026-10-14 现场修复：用户查询全局目的站更正记录时再次遇到 HTTP 422。错误详情是旧路由将 `destination-changes` 解析为整数 `shipment_id`；检查确认 8000 端口运行的是未启用 reload 的旧 BE 进程。已重启本地 BE，保留 `jingpo_logistics` 数据库连接配置，无需迁移。就绪检查 HTTP 200；全局接口及 Agent `be_client` 分页查询均 HTTP 200，响应校验通过，total=1。当前 8000 服务已加载新路由。

当前状态：全局目的站更正接口已通过 8000 本地 BE 和 Agent `be_client` 的只读验证；受控测试未运行，其他 V7 接口及真实模型自然语言表现待验收。

| 验证项 | 结果 | 证据 |
| --- | --- | --- |
| 受控测试 | 未运行 | 本轮未运行测试，待后续验证 |
| 语法/导入检查 | 通过 | Python AST 解析通过；Agent 虚拟环境可导入 `responses`、`tools`、`cli` |
| 依赖检查 | 未执行 | 待补命令和结果 |
| 真实 BE 全局更正接口 | 通过 | 2026-10-14 重启旧本地 BE 后，Agent `be_client` 请求 page=1/page_size=1 返回 HTTP 200、request_id、total=1，响应模型校验通过；1 次 HTTP 请求 |
| 真实模型自然语言评测 | 未执行 | 待后续补充 |
| 隐私/只读检查 | 代码检查完成，场景未测 | 响应模型忽略姓名/详细地址；工具固定 GET；受控断言待补 |

## 5. 完成定义

- [spec.md](spec.md) 中 AG7-01～AG7-13 均有验证记录或明确说明限制。
- 新增 BE 响应模型与当前 OpenAPI 契约一致；真实核验只读，不修改业务数据。
- Agent 能覆盖无轨迹未来段、已过期预测、共享任务等待及 schedule 历史查询。
- README 与索引准确写出适配范围和验证状态；受控测试不能标成真实模型验收。
