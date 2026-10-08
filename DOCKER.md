# 本机 Docker 演示

需要 macOS 上的 Docker Desktop。项目由 PostgreSQL、FastAPI 后端和 Nginx 前端组成；数据存放在 Docker 命名卷 `postgres_data`。

在项目根目录运行：

```bash
docker compose up --build
```

首次启动会创建一个新的本地数据库并运行 Alembic 迁移。打开 <http://localhost:8080>；后端就绪检查是 <http://localhost:8080/health/ready>，API 文档是 <http://localhost:8080/docs>。停止服务按 `Ctrl+C`，之后可用 `docker compose up -d` 在后台启动、`docker compose logs -f` 查看日志、`docker compose down` 停止并移除容器。

数据库数据默认保存在命名卷中；`docker compose down -v` 会连同数据库一起删除。它只连接 compose 内新建的数据库，不会使用或修改 Mac 上已有的 PostgreSQL 开发库。

当前 Compose 配置用于本机演示，不包含 TLS、登录鉴权或公网安全配置。数据库首次启动后是空的，需要按项目的地址库、线路和站点初始化说明准备演示数据。若要重置这个 Docker 演示库，先确认无需保留其中的数据，再执行 `docker compose down -v`。
