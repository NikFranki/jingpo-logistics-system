# JINGPO 前端

技术栈：React + TypeScript + Vite + Ant Design + ProComponents。前端正在按 V4 交接方案迁移：订单、运单和运输任务各有独立详情页，业务操作回到所属详情页，演示控制仅保留模拟时钟。V4 API 需要配套 BE 实现后才能完成联调。

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

## V4 页面职责

| 页面 | 操作 |
| --- | --- |
| 订单 | 创建、编辑订单；创建运单并跳转运单详情 |
| 运单 | 查看当前位置、轨迹和任务历史；揽收、入站、创建运输任务、编辑履约地址、派送、签收 |
| 运输任务 | 批量创建任务；确认发车、到达；发车前填写原因取消任务 |
| 演示时钟 | 查看并推进模拟时间，不执行物流业务操作 |

BE V4 接口尚未联调前，运单任务历史和任务取消入口会依赖相应接口实现。

写请求带 `Idempotency-Key`；同一操作失败后，保留原 key 供重试。时间均按 Asia/Shanghai 展示，延误状态从后端读取。
