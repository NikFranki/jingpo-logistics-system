# JINGPO 前端 V1

技术栈：React + TypeScript + Vite + Ant Design + ProComponents。使用 ProLayout、PageContainer、ProTable 和 ModalForm 构建页面，对应 PRD V1 的订单与运单、运输任务、演示控制。所有业务状态与操作条件来自现有 FastAPI 接口。

## 启动

先按 [BE 说明](../be/README.md) 启动 PostgreSQL、执行迁移并在 `127.0.0.1:8000` 启动 FastAPI。然后：

```bash
cd fe
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`。开发服务器把 `/api` 请求代理到 `http://127.0.0.1:8000`。如需直连其他后端地址，在 `fe/.env.local` 设置 `VITE_API_BASE_URL`，并把前端地址加入后端 `CORS_ORIGINS`。

```bash
npm run build
```

## 使用顺序

1. 在「订单 / 运单」创建订单、创建发货单。
2. 在「演示控制」揽收、A 入站。
3. 在「运输任务」创建 A → B 任务；回「演示控制」发车、推进时钟、到达并入站。
4. 按同样方式完成 B → C；然后开始派送、签收。

写请求带 `Idempotency-Key`；同一操作失败后，保留原 key 供重试。时间均按 Asia/Shanghai 展示，延误状态从后端读取。
