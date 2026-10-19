# FE 生产部署

## 快速入口

| 用途 | 控制台 |
| --- | --- |
| 生产 Pages 项目与部署 | [Cloudflare Pages · jingpo-fe](https://dash.cloudflare.com/d318e6f2f4a817b7ca27249ebfee4afd/pages/view/jingpo-fe/4626deac-99f9-4e9b-b4c9-8b433ffcc476) |

## 平台与构建

前端部署在 Cloudflare Pages，后端 API 仍由 Render 提供。Cloudflare 连接 GitHub 仓库 `jingpo-logistics-system` 的 `main` 分支：

| 设置项 | 值 |
| --- | --- |
| Root directory | `fe` |
| Build command | `npm ci && npm run build` |
| Build output directory | `dist` |
| `VITE_AGENT_V8_ENABLED` | Agent 部署且验收后设 `true`；部署前保持 `false` |
| `VITE_API_BASE_URL` | 不设置，保持空白 |
| Pages Functions 生产变量 `BE_API_ORIGIN` | `https://jingpo-be.onrender.com` |
| Pages Functions 生产变量 `AGENT_API_ORIGIN` | Render `jingpo-agent` 服务的 HTTPS origin，例如 `https://jingpo-agent.onrender.com` |

### 自动部署的文件过滤

仓库同时包含 FE、BE 和 Agent。为了避免只改后端或文档也触发前端构建，在 Cloudflare Pages 的 **Settings → Build → Build watch paths** 设置：

| 项 | 值 |
| --- | --- |
| Include paths | `fe/*` |
| Exclude paths | 留空 |

路径相对于 Git 仓库根目录；`fe/*` 会匹配 `fe/` 下的文件和子目录。推送中只要有 FE 文件变化就会构建；仅改 `be/`、`agent/` 或仓库级文档则跳过前端构建。Cloudflare 对大型推送会绕过此过滤并执行构建，具体规则见 [Build watch paths 文档](https://developers.cloudflare.com/pages/configuration/build-watch-paths/)。单次临时跳过可在提交信息前加 `[CI Skip]`。

`BE_API_ORIGIN`、`AGENT_API_ORIGIN` 是 Pages Function 生产运行时变量，不是浏览器端的 `VITE_` 变量。Agent 的 Render 设置见 [Agent 生产部署](../agent/deploy-prod.md)，技术细节见 [V9 Agent 部署规格](../docs/技术方案/v9/agent/spec.md)。`fe/functions/api/[[path]].js` 把同源 `/api/*` 转发到 BE；`fe/functions/agent/[[path]].js` 把 `/agent/*` 转发到 Agent 并直通 SSE 响应流；`fe/public/_routes.json` 限定两条代理路径。不要让浏览器跨站直连 Render，否则登录 Cookie 可能被浏览器拦截。

## 登录与 Google OAuth

浏览器在 Pages 域名打开登录页，前端请求同域 `/api/v1/auth/google/start`；Pages Function 转发给 BE。BE 生成授权请求并重定向到 Google。Google 将授权结果回调到 Pages 域名下的 `/api/v1/auth/google/callback`，Pages 再代理给 BE；BE 校验身份、设置同站 Cookie，并重定向回前端 `/oauth/google/callback`。前端随后读取登录状态并继续注册补全或进入业务页面。

Google 登录只申请 `openid email`，不负责发邮件。Google 登录的 Client ID/Secret 只放在 Render BE，不放在 FE 或 Cloudflare 构建变量里。Gmail API 发验证/重置邮件是另一套授权，配置见 [BE 生产部署](../be/deploy-prod.md)。

生产 Pages 域名确定后（例如 `https://jingpo-fe.pages.dev`），将它用于 BE 的 `AUTH_FRONTEND_URL`、`CORS_ORIGINS`，并将 `/api/v1/auth/google/callback` 完整地址用于 BE 的 `GOOGLE_REDIRECT_URI` 和 Google Cloud OAuth Web client 的 **Authorized redirect URIs**。两处回调地址必须完全一致。不要删除仍在使用的本地回调地址。

## 发布后检查

确认 Pages 部署成功后，打开站点验证：登录页加载、邮箱注册/验证、密码登录、Google 登录、刷新后会话仍有效。Agent 上线时另外验证 `/agent/health`、无身份创建会话返回 401，以及登录后助手 SSE 完成 progress/result。若 `/api/*` 报错，检查 `BE_API_ORIGIN`；若 `/agent/*` 报错，检查 `AGENT_API_ORIGIN`、`VITE_AGENT_V8_ENABLED` 与 Render Agent 日志。
