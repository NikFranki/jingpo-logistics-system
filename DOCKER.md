# 本机 Docker 演示

需要 macOS 上的 Docker Desktop。项目由 PostgreSQL、BE、Agent API、Nginx 前端和 Mailpit 组成；数据存放在 Docker 命名卷 `postgres_data`。

先按 [Agent README](agent/README.md) 准备 `agent/.env` 中的模型供应商/型号/API Key，Compose 在运行时注入这些配置，镜像不复制密钥文件。随后在项目根目录运行：

```bash
docker compose up --build
```

首次启动会创建一个新的本地数据库并运行 Alembic 迁移。打开 <http://localhost:8080>；后端就绪检查是 <http://localhost:8080/health/ready>，API 文档是 <http://localhost:8080/docs>。停止服务按 `Ctrl+C`，之后可用 `docker compose up -d` 在后台启动、`docker compose logs -f` 查看日志、`docker compose down` 停止并移除容器。

数据库数据默认保存在命名卷中；`docker compose down -v` 会连同数据库一起删除。它只连接 compose 内新建的数据库，不会使用或修改 Mac 上已有的 PostgreSQL 开发库。

当前 Compose 配置用于本机演示，不包含公网 TLS；V8 BE 业务接口已要求登录。首次升级并启动 BE 后，用 `docker compose exec be python -m auth.admin init --username <用户名> --email <邮箱>` 交互设置唯一管理员。FE/Agent 的真实登录接入状态见各端 V8 plan，离线账号预览不代表三端已经联调。

Mailpit 本地邮件测试服务可在 <http://localhost:8025> 查看验证和找回邮件，仅绑定本机访问。BE 使用容器内部 SMTP 1025；正式环境需替换为启用 TLS 的 SMTP，并配置正式 Google 客户端与固定回调地址。详见 [V8 BE 契约](docs/技术方案/v8/be/spec.md)。

只启动测试邮箱可运行 `docker compose up -d mailpit`。本机直接运行的 BE 使用 `SMTP_HOST=127.0.0.1 SMTP_PORT=1025 SMTP_STARTTLS=false SMTP_FROM=noreply@jingpo.local`，并将 `AUTH_FRONTEND_URL` 设为实际前端地址后重启。邮件仅保存在 Mailpit，不会发送到真实收件箱；不要用真实邮箱密码测试。

数据库首次启动后是空的，需要按项目的地址库、线路和站点初始化说明准备演示数据。若要重置这个 Docker 演示库，先确认无需保留其中的数据，再执行 `docker compose down -v`。

Agent V8 使用当前网页派生的个人只读凭据；Compose 已启用 FE 助手并将 `/agent/` 转发到独立 Agent 容器，保留 SSE。CLI 可将 `BE_BASE_URL` 和 `AGENT_BASE_URL` 同时配置为 `http://localhost:8080`，然后执行 `/login`；凭据保存到系统凭据库，不填写到模型配置中。当前只验证 Compose 配置，完整镜像构建和浏览器联调见 [Agent V8 记录](docs/技术方案/v8/agent/plan.md)。
