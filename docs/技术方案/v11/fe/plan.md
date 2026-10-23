# V11 · FE Agent 写入交互实施与验证计划

> 草案，待 FE/BE/Agent 联合 review。实现尚未开始。交互契约见 [FE spec](spec.md)。

## 1. 实施顺序

```mermaid
flowchart LR
    A[冻结 SSE 和 BE schemas] --> B[assistantApi 类型与 parser]
    B --> C[BE policy/preview/execute adapter]
    C --> D[动作卡片与状态恢复]
    D --> E[自动执行逐 action 设置]
    E --> F[组件测试和浏览器走查]
    F --> G[隔离端到端验收]
```

| 步骤 | 工作 | 完成条件 | 状态 |
| --- | --- | --- | --- |
| V11-FE-01 | 对齐 Agent SSE proposal 与 BE preview/action policy/execute/status schema | 三端字段、错误码和过期行为冻结 | 待开始 |
| V11-FE-02 | 扩展 `assistantApi.ts` 解析 `action_proposed`，使用严格的 action union | 未知 action、额外字段和错误 ID 不产生按钮；只读问答未回归 | 待开始 |
| V11-FE-03 | 在 `api.ts`/auth adapter 添加 policy、preview、execute、reject 请求 | 使用 Cookie+CSRF；不把网页凭据传 Agent；BE 错误可分类 | 待开始 |
| V11-FE-04 | 实现动作卡片及状态 | 预览前不可确认；确认/取消后正确禁用按钮；超时/刷新查询 BE proposal 状态 | 待开始 |
| V11-FE-05 | 实现逐 action 自动执行设置 | 默认关闭；仅展示 BE eligible 列表；preview 返回 AUTO_ALLOWED 才执行；服务端失败不显示开启 | 待开始 |
| V11-FE-06 | 调用 Agent action sync 更新对话时间线 | 仅发送 proposal correlation；Agent/BE 核验结果后才显示成功摘要 | 待开始 |
| V11-FE-07 | 组件测试、typecheck/lint/build 与页面走查 | 用户确认前 mutation 调用数为 0；键盘/窄屏/读屏和账号切换通过 | 待开始 |
| V11-FE-08 | 隔离 BE/Agent 联调 | 订单修改、目的站更正、拒绝、过期、stale、权限和未知执行状态覆盖完整 | 待开始 |

## 2. 验收矩阵

| 场景 | 通过条件 |
| --- | --- |
| 只读问答 | 沿用现有消息、引用、警告和对象导航，无动作卡片 |
| 人工确认 | BE preview 后展示准确前后值/影响；用户确认前 execute 请求为 0 |
| 取消/过期 | 仅取消/过期状态；无 mutation；旧按钮不可复用 |
| 自动执行 | 默认 off；只有 BE `AUTO_ALLOWED` 才调用 automatic execute；任一错误均停止 |
| 并发修改 | BE 409 后清除旧 preview 并刷新；不能用旧内容继续确认 |
| 网络超时 | 显示核对中，GET 同一 proposal；不换幂等键重试 |
| 登录变化 | 撤销待确认 UI、会话和凭据缓存；不能在新账号下继续旧 proposal |
| 无障碍与布局 | 键盘、读屏、手机窄屏下可以查看完整变更并执行/取消 |

## 3. 依赖及范围

- BE 提供 policy/preview/execute/reject/status API、严格 schema、CSRF 和 proposal 生命周期。
- Agent 输出 candidate event 并提供受 session owner 保护的 action sync 接口。
- 初始自动白名单为空；自动开关可在 eligible actions 为空时不显示。UI 不自行批准高风险操作。
- 首期按网页 Agent 助手实施；CLI 保持只读。
- 不新增 FE 直接绕过 assistant 的自然语言写接口；现有订单/运单业务表单行为保持既有规则。

## 4. 交付记录

| 日期 | 内容 | 验证 | 状态 |
| --- | --- | --- | --- |
| 2026-10-21 | 编写 FE spec/plan 草案 | 文档链接与 `git diff --check`；未改 FE 代码、未运行测试 | 待 review |
