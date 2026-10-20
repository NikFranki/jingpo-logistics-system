# 查询 Agent 目录规则

继承根目录 [协作规则](../AGENTS.md)。本目录提供自然语言只读物流查询的 API 和 CLI。

## 重点目录与入口

| 入口 | 用途 |
| --- | --- |
| `api.py`、`web_service.py` | HTTP 服务与网页会话 |
| `cli.py`、`cli_client.py` | CLI 入口与服务调用 |
| `identity.py`、`be_client.py` | 可信身份与 BE 查询 |
| `graph.py`、`state.py`、`memory.py` | 查询流程与会话状态 |
| `tools.py`、`registry.py`、`prompts.py` | 查询工具与回答规则 |
| `model.py`、`config.py` | 模型与运行配置 |
| `tests/`、`verify_backend.py` | 受控测试与真实 BE 只读契约检查 |

启动、模型配置与评测入口见 [README](README.md)，线上配置见 [deploy-prod.md](deploy-prod.md)，专项规格见 [文档导航](../docs/README.md)。

## 查询与回答要求

- 通过 BE 只读 HTTP 接口查询，不直接访问数据库，不调用业务写操作。
- 身份、数据范围和权限以 BE 校验结果为准；身份上下文与凭据不能由模型或用户文本伪造。
- 会话按用户隔离；凭据不进入模型消息、提示词、普通会话状态或日志。
- 结论必须有查询依据；区分查无结果、权限限制、请求失败和数据未查全。
- 涉及完整列表、数量与汇总时，核对分页覆盖和查询预算，不能把部分数据当成全部。
- 区分计划、预测和实际结果；运输到站不等于买家签收。

## 验证与敏感信息

- 使用本目录虚拟环境；受控测试入口为 `python -m unittest discover -s tests -v`。
- BE 契约变化按需运行 `python verify_backend.py`；真实模型评测按对应规格执行，单独记录覆盖与结果。
- 真实调用前确认模型、目标服务与费用范围；受控测试通过不能写成真实模型验收通过。
- `.env`、个人 Bearer、会话内容与追踪日志按敏感资料处理，输出需脱敏。
