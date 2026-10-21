# JINGPO 协作入口

物流演示系统，由业务前端、物流后端和只读查询 Agent 组成。

## 仓库地图

| 目录 | 职责与入口 |
| --- | --- |
| `fe/` | 业务界面与请求代理；先读 [目录规则](fe/AGENTS.md)、[README](fe/README.md) |
| `be/` | 业务规则、接口与数据库；先读 [目录规则](be/AGENTS.md)、[README](be/README.md) |
| `agent/` | 自然语言只读查询、API 与 CLI；先读 [目录规则](agent/AGENTS.md)、[README](agent/README.md) |
| `docs/` | 产品、架构与技术方案；从 [文档导航](docs/README.md) 按需查阅 |
| `scripts/` | 仓库级辅助脚本；执行前确认作用范围 |
| `Makefile` | 统一检查入口 `make check`；范围与环境见 [检查说明](docs/checks.md) |
| Session 交接 | `make handoff SESSION_ID=<id> INPUT=<session-export.md>`；见 [交接说明](docs/handoffs/README.md) |
| `docker-compose.yml` | 本地服务编排；使用说明见 [DOCKER.md](DOCKER.md) |

## 通用协作规则

- 进入子项目先读其 `AGENTS.md`；子目录规则补充全局规则，不重复维护。
- 按任务读取相关规格与代码；发现文档不一致时明确说明，不把历史记录当作当前状态。
- 改动保持聚焦，保留其他协作者的工作；接口变化检查 FE、BE、Agent 的影响。
- 执行相关验证，如实区分已实现、测试通过、实际验收通过和待验证。
- 文档用大白话；规则或架构变化同步更新对应文档，入口变化才更新本文件。
- 详细规则、架构图和进度分别放专项文档，不在各级 `AGENTS.md` 追加流水账。
- 文档组织和写作要求见 [文档维护规则](docs/contributing.md)。

## 敏感文件与高影响操作

- `.env*` 中的真实密钥、凭据和连接串不得输出或提交；示例文件仅放占位值，已有本地配置不随意覆盖。
- 数据库备份、导出、日志及会话材料可能含个人信息；不得随意上传或删除。
- 已有数据库迁移不随意改写；涉及删数据、迁移或生产部署，先确认目标环境、范围与恢复方式。
- 修改登录权限、部署配置及数据清理脚本时，检查调用方和数据影响；部署入口见各子项目 `deploy-prod.md`。
