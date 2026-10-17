# BE 生产部署

## 平台

| 部分 | 平台 | 用途 |
| --- | --- | --- |
| API 服务 | Render，服务名 `jingpo-be` | FastAPI 后端 |
| 查询 Agent | Render，服务名 `jingpo-agent` | 独立只读 FastAPI/SSE 服务，部署步骤见 [V9 Agent 部署规格](../docs/技术方案/v9/agent/spec.md) |
| PostgreSQL | Neon，生产分支 | 业务数据与账号会话 |
| 前端 | Cloudflare Pages，`https://jingpo-fe.pages.dev` | 浏览器访问；通过 Pages Function 同源代理 `/api/*` 到 BE |
| Google | Google Cloud OAuth Web client 与 Gmail API | 分别用于 Google 登录、验证/重置邮件 |

Render 从 GitHub `main` 部署 BE 和 Agent。数据库连接使用 Neon 提供的 PostgreSQL 连接串；不要把生产数据库连接串提交到仓库。Agent 不连接数据库，只通过个人 Bearer 调用 BE。建议 Agent 与 BE 放在同一 Render region，并在 Agent 环境变量中使用 Dashboard 的 BE 内部地址；若 BE 免费实例不能接收私有网络请求，则使用 BE HTTPS 公网地址。发布数据库变更前先备份，再由 BE 执行迁移。

## Render 环境变量

在 Render `jingpo-be` → **Environment** 配置；保存后等待服务重启。密钥只填入平台 Secret，不写入 Git。

| 变量 | 生产值/用途 |
| --- | --- |
| `DATABASE_URL` | Neon 的生产 PostgreSQL 连接串，使用 SQLAlchemy/psycopg 格式（项目示例前缀为 `postgresql+psycopg://`） |
| `SCHEDULE_SIGNING_KEY` | 随机且长期保密的签名密钥 |
| `AUTH_FRONTEND_URL` | `https://jingpo-fe.pages.dev`；BE 的 OAuth 完成跳转及邮件链接使用 |
| `CORS_ORIGINS` | 包含 `https://jingpo-fe.pages.dev`；多个来源用逗号分隔，保留确实需要的其他来源 |
| `AUTH_COOKIE_SECURE` | `true` |
| `GOOGLE_CLIENT_ID` | Google Cloud OAuth Web client 的 Client ID |
| `GOOGLE_CLIENT_SECRET` | 同一 OAuth Web client 的 Client Secret |
| `GOOGLE_REDIRECT_URI` | `https://jingpo-fe.pages.dev/api/v1/auth/google/callback` |
| `EMAIL_PROVIDER` | `gmail_api`（当前生产发信方式） |
| `GMAIL_API_CLIENT_ID` | Gmail 发信授权使用的 OAuth Client ID |
| `GMAIL_API_CLIENT_SECRET` | 对应 Client Secret |
| `GMAIL_API_REFRESH_TOKEN` | 已授权 `https://www.googleapis.com/auth/gmail.send` 的长期 refresh token |
| `GMAIL_API_SENDER` | 与该 Gmail 授权账号一致的发件地址 |

Google 登录凭据与 Gmail 发信凭据可以使用同一个 OAuth Client，但用途和授权不同：登录只请求 `openid email`；发信 refresh token 必须另外授权 `gmail.send`。SMTP 是可切换的备用发信实现，改用 SMTP 时设置 `EMAIL_PROVIDER=smtp` 和 `SMTP_HOST`、`SMTP_PORT`、TLS/认证及发件人变量；不要同时依赖两种 provider。

## Google Cloud 设置

在与 OAuth Client 对应的 Google Cloud 项目中启用 Gmail API。OAuth Web client 的 **Authorized redirect URIs** 添加与 `GOOGLE_REDIRECT_URI` 完全相同的 Pages callback 地址。Google OAuth 同意屏幕的应用名称/支持邮箱用于向用户说明授权方，不等于 Client ID，也不配置在前端代码中。若应用处于 Testing，只有列入 Test users 的 Google 账号可登录；上线给任意用户使用需按 Google 要求调整发布状态/验证。

## FE/BE 变量如何配合

| 配置 | 放在哪里 | 必须对应 |
| --- | --- | --- |
| `BE_API_ORIGIN=https://jingpo-be.onrender.com` | Cloudflare Pages Function 生产运行时变量 | Pages `/api/*` 的上游 BE 地址 |
| `AUTH_FRONTEND_URL=https://jingpo-fe.pages.dev` | Render BE | Pages 的实际公开站点域名 |
| `CORS_ORIGINS` 包含 Pages 域名 | Render BE | 浏览器前端来源；即使经同源代理，也保留正确来源配置 |
| `GOOGLE_REDIRECT_URI=.../api/v1/auth/google/callback` | Render BE | 同时逐字添加到 Google Cloud OAuth Client |
| `AUTH_COOKIE_SECURE=true` | Render BE | HTTPS 生产环境，保护登录与 CSRF Cookie |
| `VITE_API_BASE_URL` 不设置 | Cloudflare Pages 构建环境 | 前端请求相对路径 `/api/...`，由 Pages Function 代理 |
| `VITE_AGENT_V8_ENABLED=false` | Cloudflare Pages 构建环境 | Agent 服务尚未部署时关闭助手入口 |

改动 Pages 域名时，需同步修改 Render 的 `AUTH_FRONTEND_URL`、`CORS_ORIGINS`、`GOOGLE_REDIRECT_URI`，以及 Google Cloud 的 Authorized redirect URI，然后重新部署 BE。Pages Function 的 `BE_API_ORIGIN` 则只需指向 BE 服务地址。

## 部署验收

检查 Render `https://jingpo-be.onrender.com/health` 返回 `{"status":"ok"}`；再从 Pages 站点验证账号注册/邮件验证、登录、刷新会话及 Google 登录。免费 Render 服务休眠后首次请求可能较慢。更详细的身份接口及权限规则见 [BE README](README.md) 和 [V8 BE spec](../docs/技术方案/v8/be/spec.md)。
