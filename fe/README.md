# JINGPO 前端

技术栈：React + TypeScript + Vite + Ant Design + ProComponents。订单、运单和运输任务各有独立详情页，业务操作回到所属详情页。V5 目的站更正、V6 完整路径和 V7 全程运输计划审核已接入后端接口。

## 启动

先按 [BE 说明](../be/README.md) 启动 PostgreSQL、执行迁移并在 `127.0.0.1:8000` 启动 FastAPI。然后：

```bash
cd fe
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`。开发服务器把 `/api` 请求代理到 `http://127.0.0.1:8000`。如需直连其他后端地址，在 `fe/.env.local` 设置 `VITE_API_BASE_URL`，并把前端地址加入后端 `CORS_ORIGINS`。

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
