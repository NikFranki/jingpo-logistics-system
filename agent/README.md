# 物流查询 Agent

本地 CLI，使用 LangGraph、LangChain 组件和 ChatDeepSeek，通过 BE 的只读接口查询订单、运单、V7 运输计划、运输任务和延误。支持当前会话内追问，不执行发货、排程确认、发车、签收或其他写操作。

Agent V1 已完成并通过用户最终 review。业务主流程、响应校验、日志和会话清理已实现；受控测试及真实接口核验记录见 [plan.md](../docs/技术方案/v1/agent/plan.md) 第 12 节。真实模型的自然语言表现沿用用户此前分步验证，未新增专项验证。

## V2 适配（历史）

V2 使用六个通用运单阶段；在站位置由最后扫描站点说明，当前运输线路由 `active_transport_task` 说明。查询延误时同时读取当前任务和轨迹关联的历史任务，包含尚未发车、没有任务轨迹的待发车任务。历史任务晚到与当前任务超时分别说明。

2026-09-30：20 项受控测试通过，真实 V2 后端七项只读契约检查通过；通过受控模型调用真实查询工具，确认 AB、BC 运输中及 AB 待发车三种当前任务关联。此次没有新增真实模型自然语言评测。完整记录见 [V2 验收记录](../docs/技术方案/v2/be/plan.md)。Agent 需要与 V2 后端、前端一起升级。

## V7 适配（代码已接入，验收待完成）

V7 适配支持读取运单完整分段计划、计划历史、目的站更正历史，以及任务的计划车次、动态预测、实际时间和共享等待信息。延误组合查询从当前 schedule、当前执行任务和轨迹历史任务收集关联，并按任务 ID 去重；未来计划段即使没有轨迹也会纳入核对。`PLANNED`、`ACTIVE`、`RELEASED` 分别表示未来关联、当前执行关联、已解除关联，不能互相混称。分段预计到达不是买家签收时间。

对应规格与实施记录见 [V7 Agent 规格](../docs/技术方案/v7/agent/spec.md) 和 [V7 Agent 实施计划](../docs/技术方案/v7/agent/plan.md)。本次已接入只读工具和响应字段；本机 BE 当前不可连接，真实契约核验待后端启动后进行。受控测试与真实模型回答也尚未验收。新接口只读，工具不包含预览、确认、取消或物流写操作。

## 1. 先启动后端

需要 PostgreSQL 和 BE 正常运行。首次准备数据库、迁移及 PostgreSQL 启动方式见 [BE README](../be/README.md) 第 1～5 节。已有环境不用重新建库或生成迁移。

在项目根目录打开一个终端：

```bash
cd be
source .venv/bin/activate
export DATABASE_URL="postgresql+psycopg:///jingpo_logistics"
python -m uvicorn main:app --reload
```

按本机实际连接配置调整 DATABASE_URL。保持终端运行，访问 [数据库就绪检查](http://127.0.0.1:8000/health/ready) 和 [Swagger](http://127.0.0.1:8000/docs)。

## 2. 准备 Agent 环境

另开终端，从项目根目录执行。当前本机环境为 Python 3.12.5，建议使用 Python 3.12。

```bash
cd agent
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

已有 agent/.venv 时跳过创建，只激活并安装依赖。Agent 和 BE 使用各自的虚拟环境。

requirements.txt 只声明代码直接使用的依赖，并固定本机已安装版本；其余间接依赖由 pip 安装，没有使用 pip freeze 全量导出。新增显式声明的 langchain-core 和 pydantic 分别用于消息/工具接口和参数校验。该文件不是完整依赖锁文件。

## 3. 配置环境变量

首次配置且没有 .env 时，在 agent 目录执行：

```bash
cp -n .env.example .env
```

编辑 .env，选择模型供应商和型号，并分别填入对应平台的密钥。已有 .env 继续使用，无需覆盖。示例型号是占位符，不是实际可请求的型号。

| 变量 | 说明 |
| --- | --- |
| LLM_PROVIDER | `deepseek` 或 `qwen`，默认 `deepseek` |
| LLM_MODEL | 必填，实际模型名称 |
| DEEPSEEK_API_KEY | 使用 DeepSeek 时填写；也兼容旧变量 LLM_API_KEY |
| DASHSCOPE_API_KEY | 使用 Qwen/百炼时填写；也兼容旧变量 LLM_API_KEY |
| LLM_BASE_URL | 可选；DeepSeek 默认 `https://api.deepseek.com`，Qwen 默认 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| BE_BASE_URL | 默认 http://127.0.0.1:8000 |
| AGENT_DEBUG | 默认 false；true/1 开启脱敏诊断日志，false/0 关闭 |

配置优先级：**进程已有环境变量 > agent/.env > 代码默认值**。即使已有环境变量为空，也不会被 .env 覆盖；必填项随后会报错。程序只加载 agent 目录下的 .env，不自动选择 .env.production 等文件。

修改 .env 后重启 Agent；若曾在终端 export 同名变量，先 unset 对应变量，才能采用文件中的值。未来运行环境也可直接注入这些环境变量。仓库忽略 .env，仅提交无密钥的 .env.example。

模型创建集中在 model.py 的 create_chat_model。通过修改 `LLM_PROVIDER`、`LLM_MODEL` 和对应密钥即可切换，不必改代码。例如切到 Qwen 时设 `LLM_PROVIDER=qwen`、`LLM_MODEL=qwen3.8-max`，并填写 `DASHSCOPE_API_KEY`；切回 DeepSeek 时改为 `LLM_PROVIDER=deepseek`、对应模型名和 `DEEPSEEK_API_KEY`。Qwen 通过百炼 OpenAI 兼容接口调用；北京地域默认使用 DashScope 兼容接口，也可在百炼控制台复制业务空间专属地址后配置到 `LLM_BASE_URL`。API Key 与地域/端点须匹配。切换后重启 Agent。

## 4. 启动与对话

在 agent 目录、已激活虚拟环境的终端执行：

```bash
python cli.py
```

可以输入（把 XXX 换成实际完整编号）：

- `查询订单 XXX 的物流，是否延误？`
- `查询运单 XXX 的轨迹`
- `它现在是否延误？`
- `查询 AB 线路运输中的任务，每页 2 条`
- `下一页`
- `查询任务编号 XXX 的详情`

`/new` 开始新会话；`/exit` 退出。进程退出后对话不恢复。每次回答后显示模型、工具、HTTP 调用次数和结束原因；设置 AGENT_DEBUG=true 可在标准错误输出查看每轮 trace_id、模型/工具/HTTP 耗时、工具名、脱敏参数、结果状态及 BE request_id。业务编号以哈希摘要显示；不输出用户原文、模型回答、推理内容、完整地址或密钥。

查询将请求真实模型，产生模型服务费用；业务数据来自当前 BE。业务编号仅精确匹配，纯数字要明确是订单、运单还是任务的数据库 ID。

延误以 BE 的服务器时间和派生字段为准。区段计划到达时间不是买家收货时间；最后扫描站点也不是实时定位。

## 5. 调用链与文件职责

输入 → 模型提出工具调用 → 工具校验参数 → HTTP 查询 BE → ToolMessage 回传 → 模型回答或继续查询。

| 文件 | 职责 |
| --- | --- |
| cli.py | 输入循环、会话切换、异常恢复及输出 |
| config.py / model.py | 读取配置、创建模型适配器 |
| graph.py / state.py | LangGraph 循环、会话状态和每轮预算 |
| tools.py | 订单、运单、运输计划、目的站更正历史和运输任务查询；订单→运单→任务关联 |
| be_client.py | 固定 BE 路径的 GET 请求及 HTTP 错误处理 |
| prompts.py | 澄清、工具选择和有依据的回答规则 |
| responses.py | 校验 BE 实际使用的响应字段，丢弃无关字段 |
| results.py | 保留合法 JSON，限制工具消息与轨迹数量 |
| tracing.py | 按需输出脱敏 JSON 日志 |
| memory.py | 每轮结束压缩检查点、删除弃用会话 |
| tests/ | 不使用真实模型或真实 BE 的受控测试 |
| verify_backend.py | 真实 BE 只读响应契约核验，不调用模型 |

每轮最多 5 次模型调用、8 次工具尝试、24 次 HTTP 请求。第 5 次模型仍可调用工具，执行后停止，不发起第 6 次模型请求；如果第 5 次直接给出最终回答，则正常结束。

单次模型等待最多 45 秒，HTTP 最多 10 秒，整轮最多 120 秒。输入最多 4,000 字符，保留最多 10 轮历史加当前轮，并按 64 KiB 消息上限裁剪。工具消息最多 32 KiB；轨迹保留 BE 最新在前顺序的最近 50 条，meta.collections 记录裁剪前总数及返回数，meta.truncated 标记裁剪。任务发现使用完整轨迹和 schedule；V7 schedule 分段最多摘要 20 段，延误组合最多读取 18 个去重任务，超出时明确标记不完整。单个字段本身过大时明确返回 RESULT_TOO_LARGE，不截断成误导性的业务字段。

CLI 在正常结束或恢复后，仅保留当前状态的一个检查点；/new、/exit 删除旧会话。清理不在图运行中执行，已有调用配对与后续追问保留。直接嵌入 build_graph 的其他入口也需在终止后调用 compact_session，不能在并发运行同一会话时清理。

## 6. 常见问题

- 配置报错：检查必填项、占位符及终端中同名环境变量。
- 模型认证失败／限流：分别检查密钥权限或稍后重试；其他模型请求失败检查型号和网络。日志不输出原始异常。
- 后端响应格式异常：成功状态码下的非法 JSON、缺失字段、错误类型会转换为 INVALID_RESPONSE；关联站点、schedule、历史或任务查询失败时保留其他已确认事实并标记 partial。
- 无法连接后端：检查 BE_BASE_URL、BE 进程和数据库就绪检查。
- 查询对象不存在：对照 Swagger 核对对象类型、数据库 ID 或完整编号。
- 达到调用上限：本轮查询未完成，缩小范围后继续提问；下一轮预算重置。

## 7. 收尾验证

在 agent 目录执行受控测试和依赖检查：

```bash
python -m unittest discover -s tests -v
python -m pip check
```

这些测试使用模拟模型与 HTTP，覆盖六工具链路、预算、超时恢复、响应错误、日志脱敏和检查点清理，不消耗模型额度、不修改数据库。模拟模型测试不能证明真实模型一定选择正确工具或抵抗所有文本注入。

BE 已启动时可执行只读核验：

```bash
python verify_backend.py
```

它查询三类列表、各列表第一条的详情和站点，验证真实响应字段；空列表的详情标记 skipped，不声称验证通过。输出仅检查名、状态和 request_id，不展示业务明细。可用 --base-url 指定 BE 地址，脚本不加载 .env。

本次未重建干净环境；直接依赖沿用本机已安装版本并用 pip check 核对。实际验证模型名未从私有 .env 读取，仍需用户提供不含密钥的型号名称。
