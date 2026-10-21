# 文档导航

本地统一检查和 GitHub CI 见 [检查说明](checks.md)。

Session 交接模板与生成命令见 [handoffs](handoffs/README.md)。

重要架构取舍见 [ADR 决策记录](adr/README.md)。

按任务选择下方入口；具体启动、开发和测试优先读取各子项目 README。

| 入口 | 内容 |
| --- | --- |
| [FE README](../fe/README.md)、[BE README](../be/README.md)、[Agent README](../agent/README.md) | 子项目开发入口 |
| [架构说明](architecture.md) | 系统关系与主要业务数据流 |
| [文档维护规则](contributing.md) | 文档归属、表达与更新要求 |
| [历史实施快照](history/implementation-snapshots.md) | 从旧 AGENTS.md 移出的实施记录，不能视为当前状态 |
| [FE 部署](../fe/deploy-prod.md)、[BE 部署](../be/deploy-prod.md)、[Agent 部署](../agent/deploy-prod.md) | 生产操作 |
| [本地容器](../DOCKER.md) | 本地编排说明 |

以下版本索引从原根目录 AGENTS.md 迁入；条目内完成状态为原记录，实际进展需核对对应 plan 和代码。

## 文档去哪找

| 要了解什么 | 文档 |
| --- | --- |
| V10 Agent 查询质量优化（当前规格范围已实现并验收；生产数据表现待实际运营观察） | [准确性、完整性与回答依据规格](技术方案/v10/agent/spec.md)、[实施与验证计划](技术方案/v10/agent/plan.md) |
| V9 Agent Render 部署、Pages SSE 代理和本地兼容 | [部署规格与操作步骤](技术方案/v9/agent/spec.md) |
| 运输线路统一契约及旧字段弃用策略 | [统一运输线路](技术方案/v7/be/unified-transport-lines.md) |
| 线路班次、车次生成和运单候选 | [班次与计划车次](技术方案/v7/be/line-services.md) |
| 订单时间窗与兼容任务共享规则 | [字段、匹配条件与迁移](技术方案/v7/be/order-delivery-windows.md) |
| 服务器时间与移除模拟时钟 | [时间规则、接口与迁移](技术方案/server-time.md) |
| 寄件首站与创建后预览 | [字段、接口和交付状态](技术方案/v7/be/planned-origin-and-preview.md) |
| 统一运输线路与全国城市演示初始化 | [管理接口、候选、迁移与交付](技术方案/v7/be/unified-transport-lines.md) |
| 站点服务范围与自动匹配目的站 | [配置接口、自动创建与发布状态](技术方案/v7/be/station-service-areas.md) |
| 订单与运单省市区＋详细地址 | [接口、字段与迁移](技术方案/v7/be/structured-addresses.md) |
| 国内省市区地址库（三表与接口） | [地址库设计与交付状态](技术方案/v7/be/address-library.md) |
| 订单、运单、揽收与入站的含义 | [领域术语](../be/CONTEXT.md) |
| V1 产品范围与界面示例 | [V1 PRD](prd/v1/JINGPO-logistics-system-v1.md) |
| V1 后端总体设计 | [技术方案](技术方案/v1/be/be-v1技术方案.md)、[规格](技术方案/v1/be/spec.md)、[实施与验证记录](技术方案/v1/be/plan.md) |
| V1 查询 Agent 设计 | [技术方案](技术方案/v1/agent/agent-v1技术方案.md)、[规格](技术方案/v1/agent/spec.md)、[实施与验证记录](技术方案/v1/agent/plan.md) |
| V2 通用物流阶段与轨迹事件 | [V2 PRD](prd/v2/JINGPO-logistics-system-v2.md)、[后端规格](技术方案/v2/be/spec.md)、[实施计划](技术方案/v2/be/plan.md) |
| V3 站点与线路配置（BE 已实现） | [V3 PRD](prd/v3/JINGPO-logistics-system-v3.md)、[规格](技术方案/v3/be/spec.md)、[实施计划](技术方案/v3/be/plan.md) |
| V4 发车前取消与业务操作归属（BE 已实现） | [V4 PRD](prd/v4/JINGPO-logistics-system-v4.md)、[BE 规格](技术方案/v4/be/spec.md)、[实施计划](技术方案/v4/be/plan.md) |
| V5 运单目的站更正（BE 已实现，FE 已适配） | [V5 PRD](prd/v5/JINGPO-logistics-system-v5.md)、[BE 规格](技术方案/v5/be/spec.md)、[BE 验收记录](技术方案/v5/be/plan.md)、[FE 适配记录](技术方案/v5/fe/plan.md) |
| V6 完整运输路径与未来段调整（BE 已实现，FE 已接入） | [V6 PRD](prd/v6/JINGPO-logistics-system-v6.md)、[BE 规格](技术方案/v6/be/spec.md)、[BE 验收记录](技术方案/v6/be/plan.md)、[FE 适配记录](技术方案/v6/fe/plan.md) |
| 前端物流查询助手与独立 Agent API | [接入契约、启动与验证](技术方案/v7/agent/web-assistant.md) |
| V7 查询 Agent 适配（代码已接入，验收待完成） | [Agent 规格](技术方案/v7/agent/spec.md)、[实施与验证计划](技术方案/v7/agent/plan.md) |
| V7 运输计划审核与全段任务生成（BE 已实现，FE 已接入待页面验收） | [V7 PRD](prd/v7/JINGPO-logistics-system-v7.md)、[BE 规格](技术方案/v7/be/spec.md)、[BE 实施与验证](技术方案/v7/be/plan.md)、[FE 适配与验证](技术方案/v7/fe/plan.md) |
| V8 待办 | [待补齐功能与验收目标](prd/v8/TODO.md) |
| V8 FE 登录与权限（已接 BE，浏览器与三端验收待完成） | `/login` 正式入口，`/v8-preview/login` 独立预览；[FE spec](技术方案/v8/fe/spec.md)、[交付状态](技术方案/v8/fe/plan.md)；Cookie/CSRF、固定角色与业务权限、用户管理及订单取消；Agent 已接 V8 身份，本地开关与 Compose 构建已启用，真实浏览器联调待验收 |
| V8 Agent 身份与权限（代码、受控测试及隔离 BE 联调已完成） | `identity.py` 校验个人身份；CLI 统一走 API，系统凭据库保存登录；普通用户安全运单查询；[规格](技术方案/v8/agent/spec.md)、[交付记录](技术方案/v8/agent/plan.md)；真实浏览器/模型与完整容器验收待完成 |
| V8 登录与角色权限（BE 已实现，三端联调待完成） | `be/auth/` 负责身份、会话与固定权限，订单记录创建者；[PRD](prd/v8/JINGPO-logistics-system-v8.md)、[BE spec](技术方案/v8/be/spec.md)、[BE 验证记录](技术方案/v8/be/plan.md)、[FE plan](技术方案/v8/fe/plan.md)、[Agent spec](技术方案/v8/agent/spec.md)、[Agent plan](技术方案/v8/agent/plan.md) |
