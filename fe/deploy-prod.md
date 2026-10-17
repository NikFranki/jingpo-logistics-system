# FE 生产部署

## 平台与构建

前端部署在 Cloudflare Pages，后端 API 仍由 Render 提供。Cloudflare 连接 GitHub 仓库 `jingpo-logistics-system` 的 `main` 分支：

| 设置项 | 值 |
| --- | --- |
| Root directory | `fe` |
| Build command | `npm ci && npm run build` |
| Build output directory | `dist` |
| `VITE_AGENT_V8_ENABLED` | `false`（Agent 尚未部署） |
| `VITE_API_BASE_URL` | 不设置，保持空白 |
| Pages Functions 生产变量 `BE_API_ORIGIN` | `https://jingpo-be.onrender.com` |

`BE_API_ORIGIN` 是 Pages Function 运行时变量，不是浏览器端的 `VITE_` 变量。每个变量单独添加。`fe/functions/api/[[path]].js` 把同源 `/api/*` 转发给 Render；`fe/public/_routes.json` 限定代理范围。不要让浏览器跨站直连 Render，否则登录 Cookie 可能被浏览器拦截。

## 登录与 Google OAuth

浏览器在 Pages 域名打开登录页，前端请求同域 `/api/v1/auth/google/start`；Pages Function 转发给 BE。BE 生成授权请求并重定向到 Google。Google 将授权结果回调到 Pages 域名下的 `/api/v1/auth/google/callback`，Pages 再代理给 BE；BE 校验身份、设置同站 Cookie，并重定向回前端 `/oauth/google/callback`。前端随后读取登录状态并继续注册补全或进入业务页面。

Google 登录只申请 `openid email`，不负责发邮件。Google 登录的 Client ID/Secret 只放在 Render BE，不放在 FE 或 Cloudflare 构建变量里。Gmail API 发验证/重置邮件是另一套授权，配置见 [BE 生产部署](../be/deploy-prod.md)。

生产 Pages 域名确定后（例如 `https://jingpo-fe.pages.dev`），将它用于 BE 的 `AUTH_FRONTEND_URL`、`CORS_ORIGINS`，并将 `/api/v1/auth/google/callback` 完整地址用于 BE 的 `GOOGLE_REDIRECT_URI` 和 Google Cloud OAuth Web client 的 **Authorized redirect URIs**。两处回调地址必须完全一致。不要删除仍在使用的本地回调地址。

## 发布后检查

确认 Pages 部署成功后，打开站点验证：登录页加载、邮箱注册/验证、密码登录、Google 登录、刷新后会话仍有效。若 `/api/*` 报错，先确认 Pages 生产环境的 `BE_API_ORIGIN`；再检查 Render 变量和服务日志。
