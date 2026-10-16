# JINGPO 前端

## V8 登录与权限

运行 `npm run dev` 后打开 `/v8-preview/login`，可模拟四角色、注册/邮箱验证、找回密码、个人账号、用户管理及 Google 回调状态。预览不请求 BE/Agent、不保存密码、不发送邮件；数据只在当前工作区内存保留，刷新或离开后重置。顶部可直接切换角色。

正式入口 `/login` 已接 BE V8：Cookie 登录、CSRF、邮箱验证/找回、Google 登录/补用户名、用户管理分页和 CLI 授权。业务页面要求登录，导航和动作按账号权限展示。HTTP 接入集中在 `src/auth/httpAdapter.ts`。详见 [FE spec](../docs/技术方案/v8/fe/spec.md) 与 [FE plan](../docs/技术方案/v8/fe/plan.md)。

使用前由 BE 负责方备份、迁移到 V8 并初始化唯一管理员，再重启服务。开发默认前端为 `http://localhost:5173`，BE 的 `AUTH_FRONTEND_URL` 必须与浏览器访问地址一致，开发 HTTP 设置 `AUTH_COOKIE_SECURE=false`。通过同源 `/api` 代理访问，写请求自动带 CSRF；不要将密码或网页登录凭据放进 localStorage。

Agent 服务已接入 V8 个人 Bearer 和会话隔离。助手开关仍需显式启用：在 `fe/.env.local` 设置 `VITE_AGENT_V8_ENABLED=true` 并重启开发服务；生产构建可传同名 Docker build arg，并配置 Agent 服务及同源 `/agent` 代理。启用后 FE 向 BE 领取只读个人 token，仅在内存保存并通过 Bearer 调用 Agent，不回退匿名接口。Google 需要后端真实配置；回调失败可能由 BE 直接返回 JSON 错误，不能将预览里的冲突状态切换视为真实 Google 验收。

技术栈：React + TypeScript + Vite + Ant Design + ProComponents。订单、运单和运输任务各有独立详情页，业务操作回到所属详情页。V5 目的站更正、V6 完整路径和 V7 全程运输计划审核已接入后端接口。

## 启动

先按 [BE 说明](../be/README.md) 启动 PostgreSQL、执行迁移并在 `127.0.0.1:8000` 启动 FastAPI。然后：

```bash
cd fe
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`。开发服务器把 `/api` 请求代理到 `http://127.0.0.1:8000`，将 `/agent` 请求代理到独立 Agent 服务 `http://127.0.0.1:8001`。如需直连其他后端地址，在 `fe/.env.local` 设置 `VITE_API_BASE_URL`，并把前端地址加入后端 `CORS_ORIGINS`。

## 页面地址

订单/运单与运输任务详情使用独立页面，地址可直接打开或分享：

| 页面 | 地址 |
| --- | --- |
| 订单列表 | `/orders` |
| 订单详情 | `/orders/:orderId` |
| 运单列表 | `/shipments` |
| 运单详情 | `/shipments/:shipmentId` |
| 运输任务列表 | `/tasks` |
| 运输任务详情 | `/tasks/:taskId` |
| 站点管理 | `/network/stations` |
| 运输线路 | `/network/transport-lines` |
| 线路每日班次 | `/network/transport-lines/:lineId/services` |

生产环境的静态站点服务器需要把这些前端地址回退到 `index.html`，以支持刷新和直接打开详情链接。

```bash
npm run build
```

## 代码风格检查

项目使用 ESLint 固定 TypeScript、React Hooks 与基础格式规则。提交前可运行：

```bash
npm run lint
```

纯格式问题可用 `npm run lint:fix` 自动修复；规则配置见 `eslint.config.js`。

## 页面职责

| 页面 | 操作 |
| --- | --- |
| 订单 | 创建、编辑订单；创建运单并跳转运单详情 |
| 网络配置 | 站点管理 `/network/stations`、运输线路 `/network/transport-lines`；线路维护每日重复班次，自动生成近期车次 |
| 运单 | 查看当前位置、全程计划、逐站计划/预测/实际时间和任务历史；新计划选择具体日期与班次后审核，旧运单保留 V6 单段安排；揽收、入站、派送、签收 |
| 运输任务 | 旧运单按下一段归集并创建单段任务；查看 V7 预生成任务、等待依赖、预测和实际状态；计划任务取消前先审核影响 |

运单详情的目的站更正记录单独展示，不混入物流轨迹；更正操作以 BE 返回的 `UPDATE_DESTINATION` 资格为准。V5 接口契约与前端验证记录见 [V5 FE 适配记录](../docs/技术方案/v5/fe/plan.md)。

运单完整路径与版本历史、按下一段批量创建任务见 [V6 FE 适配记录](../docs/技术方案/v6/fe/plan.md)。新路径规划从统一运输线路中选择；路径版本、接续站和线路版本冲突由后端校验。

线路每日班次、时刻模板、日期车次及运单班次选择见 [V7 FE 适配记录](../docs/技术方案/v7/fe/plan.md)。预览不会生成任务；确认会一次生成全程任务，后续任务需等待前段实际到达。

写请求带 `Idempotency-Key`；同一操作失败后，保留原 key 供重试。时间均按 Asia/Shanghai 展示，延误状态从后端读取。

## 分页约定与排查

订单、运单、运输任务列表默认每页 20 条，可切换为 10、20、50、100 条。ProTable 使用 `defaultPageSize` 设置初始条数；不要把 `pageSize` 写成固定值，否则请求条数变化后，分页控件仍按旧条数计算页数。运单详情的历史记录由后端每页返回 20 条，分页控件固定 20 条并关闭条数切换。

2026-10-01 排查：本地后端三个列表接口以 `page_size=2` 查询第 1、2 页，均返回不同记录及一致总数（订单 14、运单 14、任务 20）。前端修正上述配置后，lint 和构建通过；当前没有可连接的浏览器，页面点击翻页仍待验证。

## 物流查询助手

先按 [Agent README](../agent/README.md) 启动 8001 端口的 Agent API，再点击页面右下角「查询助手」。可查询订单、运单、运输任务、计划和更正记录，也可连续追问。窗口标题可拖动，右下角可调整大小；标题栏支持放大、还原、收起和新对话。手机上使用适应屏幕的窗口。

收起或在系统内跳转会保留当前对话。回答里的对象链接显示类型、业务编号和 ID，点击打开对应详情。查询中可以停止，网络失败可以重试；同一请求重试会复用已完成结果，避免重复模型调用。刷新页面会开始新的浏览器会话。

生产站点需将 `/agent/` 转发到 Agent API，关闭 SSE 缓冲并设置读取超时 150 秒；模型密钥不放到前端配置里。API 契约、代理配置与验证见 [助手接入文档](../docs/技术方案/v7/agent/web-assistant.md)。
