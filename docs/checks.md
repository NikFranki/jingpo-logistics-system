# 本地检查与 GitHub CI

在仓库根目录执行，本地和 GitHub Actions 使用同一组入口：

| 命令 | 检查范围 |
| --- | --- |
| `make check` | 顺序执行全部检查，失败立即停止；也可分别运行各项 |
| `make check-be` | Python 依赖、临时数据库全量迁移、模型差异、后端回归测试 |
| `make check-fe` | TypeScript、ESLint、生产构建 |
| `make check-agent` | Python 依赖、模拟模型与 HTTP 的受控测试 |
| `make handoff SESSION_ID=<id> INPUT=<session-export.md>` | 通过只读 `codex exec` 生成独立 session 交接记录 |

## Codex 本地 Hooks

仓库信任后，`.codex/hooks.json` 会启用两类轻量检查：

- 写入前拦截通过补丁修改 `.env*`、密钥文件、备份/导出/日志目录，以及明显危险的数据库降级、删卷和清理命令。
- 补丁写入后，仅对改动的 FE 文件运行 ESLint；对改动的 BE/Agent Python 文件做语法检查。它们只提供快速反馈，不代替完整测试。

Hook 只覆盖 Codex 经过的工具调用，不能作为安全边界；shell 脚本、外部编辑器和其他进程仍需常规权限、备份和审查。完整验收继续使用 `make check-*` 与 GitHub CI。FE 依赖未安装时会提示并跳过定向 lint，不会自动安装依赖。

## 首次准备

BE、Agent 使用 Python 3.12，各自按 README 建立 `.venv`，分别安装 `be/requirements-dev.txt` 和 `agent/requirements.txt`。FE 使用 Node.js 24，在根目录执行 `npm ci --prefix fe`。

Make 默认使用各目录虚拟环境；可用 `BE_PYTHON=python`、`AGENT_PYTHON=python` 指定已安装依赖的解释器。检查不会自动安装或升级依赖。

后端需要 PostgreSQL 17，连接角色需有创建数据库权限。本机默认通过 Unix socket 连接 `postgres` 管理库。其他环境显式指定专用测试实例：

```bash
TEST_DATABASE_URL='postgresql+psycopg://tester:password@127.0.0.1:5432/postgres' make check-be
```

脚本只接受管理库 `postgres` 或名称以 `_test` 结尾的连接配置，使用其连接信息创建随机命名的 `jingpo_checks_*_test` 数据库；迁移和测试只操作新库，结束或普通失败后删除自己创建的库。不读取应用 `.env`，不采用 `DATABASE_URL`。强制杀进程可能留下临时库，核对名称和进程后再清理。

## GitHub 上怎么看

`.github/workflows/checks.yml` 在 push、PR 和手动触发时并行运行 `Hook policy checks`、`BE checks`、`FE checks`、`Agent checks`，最后由稳定的 `Required checks` 汇总任务确认全部成功。分支保护只需要求 `Required checks` 通过；新增 CI job 时把它加入汇总任务，不用再改 GitHub 规则。后端使用独立 PostgreSQL service container，不需要生产密钥；分支保护仍需仓库管理员另行配置。

```mermaid
flowchart LR
    Change[本地改动或 GitHub 提交] --> Entry[统一 make 命令]
    Entry --> BE[临时库迁移与 BE 测试]
    Entry --> FE[类型、lint 与构建]
    Entry --> Agent[Agent 受控测试]
```

图中三项在 CI 并行运行，本地 `make check` 默认顺序运行。

## 验证边界

常规检查不调用真实付费模型、不连接开发业务库、不进行生产部署。检查使用测试模型占位配置和不可用的本地模型地址，防止误用已有模型密钥。

## 本次接入验证（2026-10-20）

- 本地 BE 空库升级、模型差异检查通过；82 项测试中 73 项通过、9 项 opt-in 检查跳过，包含本次新增的 3 项临时库保护测试；临时库已清理。
- FE 类型检查和独立生产构建通过，lint 检出既有 14 项错误，涉及 `functions/` 的运行时全局变量配置、`AssistantPanel.tsx` 和 `AccountWorkspace.tsx`；未降低检查标准。
- Agent 首轮检出 `test_not_found_equivalents` 断言失败；其他 Agent 的并行评测修改完成后复跑，55 项受控测试全部通过，本次未修改这些评测文件。
- 工作流通过 `actionlint` 校验，文档与补丁通过 `git diff --check`。
- GitHub 托管运行尚未执行；需提交并推送后在 Actions 中确认三个任务结果。

历史 V2/V8 升级演练及真实 SMTP 测试保留原 opt-in 入口，常规检查不启用，会在 unittest 输出中明确标记 skipped；当前迁移检查覆盖空库升级到 head 和 `alembic check`。升级历史数据、真实 BE/Agent 联调、浏览器流程与真实模型评测仍按各项目说明单独验收。CI 通过不等于这些验收通过。
