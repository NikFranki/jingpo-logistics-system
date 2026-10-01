# JINGPO 前端

技术栈：React + TypeScript + Vite + Ant Design + ProComponents。订单、运单和运输任务各有独立详情页，业务操作回到所属详情页，演示控制仅保留模拟时钟。V5 目的站更正与 V6 完整路径功能已接入后端接口。

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
| 演示控制 | `/simulation` |

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
| 网络配置 | 维护站点、运输线路和可复用的完整路径方案 |
| 运单 | 查看当前位置、完整路径和任务历史；揽收、入站、规划/调整未来路径、创建下一段运输任务、编辑履约地址、派送、签收 |
| 运输任务 | 按路径下一段归集运单并批量创建；确认发车、到达；发车前填写原因取消任务 |
| 演示时钟 | 查看并推进模拟时间，不执行物流业务操作 |

运单详情的目的站更正记录单独展示，不混入物流轨迹；更正操作以 BE 返回的 `UPDATE_DESTINATION` 资格为准。V5 接口契约与前端验证记录见 [V5 FE 适配记录](../docs/技术方案/v5/fe/plan.md)。

路径方案、运单完整路径与版本历史、按下一段批量创建任务见 [V6 FE 适配记录](../docs/技术方案/v6/fe/plan.md)。路径版本、接续站和方案版本冲突由后端校验；页面刷新后需重新确认，不能绕过路径直接选择其他线路。

写请求带 `Idempotency-Key`；同一操作失败后，保留原 key 供重试。时间均按 Asia/Shanghai 展示，延误状态从后端读取。
