# 物流查询 Agent

提供本地 CLI 和独立 Agent API 服务，使用 LangGraph、LangChain 组件和模型服务，通过 BE 的只读接口查询订单、运单、V7 运输计划、运输任务和延误。支持当前会话内追问，不执行发货、排程确认、发车、签收或其他写操作。

Agent V1 已完成并通过用户最终 review。业务主流程、响应校验、日志和会话清理已实现；受控测试及真实接口核验记录见 [plan.md](../docs/技术方案/v1/agent/plan.md) 第 12 节。真实模型的自然语言表现沿用用户此前分步验证，未新增专项验证。

## V2 适配（历史）

V2 使用六个通用运单阶段；在站位置由最后扫描站点说明，当前运输线路由 `active_transport_task` 说明。查询延误时同时读取当前任务和轨迹关联的历史任务，包含尚未发车、没有任务轨迹的待发车任务。历史任务晚到与当前任务超时分别说明。

2026-09-30：20 项受控测试通过，真实 V2 后端七项只读契约检查通过；通过受控模型调用真实查询工具，确认 AB、BC 运输中及 AB 待发车三种当前任务关联。此次没有新增真实模型自然语言评测。完整记录见 [V2 验收记录](../docs/技术方案/v2/be/plan.md)。Agent 需要与 V2 后端、前端一起升级。

## V7 适配（代码已接入，验收待完成）

V7 适配支持读取运单完整分段计划、计划历史、目的站更正历史，以及任务的计划车次、动态预测、实际时间和共享等待信息。运单列表每页最多 100 条；`find_shipments_with_destination_changes` 直接分页读取目的站更正审计记录，每条记录带运单号和 ID，不逐单扫描，也不为确认更正而查询运输任务。延误组合查询从当前 schedule、当前执行任务和轨迹历史任务收集关联，并按任务 ID 去重；未来计划段即使没有轨迹也会纳入核对。`PLANNED`、`ACTIVE`、`RELEASED` 分别表示未来关联、当前执行关联、已解除关联，不能互相混称。分段预计到达不是买家签收时间。

对应规格与实施记录见 [V7 Agent 规格](../docs/技术方案/v7/agent/spec.md) 和 [V7 Agent 实施计划](../docs/技术方案/v7/agent/plan.md)。已接入只读工具和响应字段；全局目的站更正查询已连接本地 BE 验证。前端助手新增 API 会话与关联对象查询，详见下方说明；其他 V7 业务场景仍按实施记录逐项验收。新接口只读，工具不包含预览、确认、取消或物流写操作。

## V8 个人身份与权限

网页助手与 CLI 已按 BE V8 接入个人只读身份、会话归属、权限版本复核和工具范围。Agent 校验 BE 的 `data_scope`、`answer_style`、`permissions`，按后端身份声明限制查询范围和回答风格。CLI 通过浏览器授权并统一调用 Agent API。受控测试和隔离真实 BE 联调通过；本轮权限字段适配未运行测试，真实模型、浏览器授权页面与完整容器运行仍待验收。详见 [V8 规格](../docs/技术方案/v8/agent/spec.md) 和 [交付记录](../docs/技术方案/v8/agent/plan.md)。

## V9 Render 部署

Agent 生产服务部署到 Render Web Service，浏览器通过 Cloudflare Pages Function 的 `/agent/*` 路径访问，CLI 直接使用 Render HTTPS 地址。线上部署及网页助手可用状态已由用户确认。日常设置、环境变量和部署验收步骤见 [Agent 生产部署](deploy-prod.md)；完整架构和接口说明见 [V9 Agent 部署规格](../docs/技术方案/v9/agent/spec.md)。本地 Vite 与 Docker Compose 继续使用 8001 端口。

## 1. 先启动后端

需要 PostgreSQL 和已升级 V8 的 BE 正常运行，并完成账号初始化/验证；旧匿名 BE 不满足 Agent 身份契约。首次准备数据库、迁移及 PostgreSQL 启动方式见 [BE README](../be/README.md) 第 1～5 节。已有环境不用重新建库或生成迁移。

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

Agent API 使用 .env 中的模型配置；CLI 只需要 BE_BASE_URL、AGENT_BASE_URL，不要求本地模型密钥。编辑 .env，选择模型供应商和型号，并分别填入对应平台的密钥。已有 .env 继续使用，无需覆盖。示例型号是占位符，不是实际可请求的型号。

| 变量 | 说明 |
| --- | --- |
| LLM_PROVIDER | `deepseek` 或 `qwen`，默认 `deepseek` |
| LLM_MODEL | 必填，实际模型名称 |
| DEEPSEEK_API_KEY | 使用 DeepSeek 时填写；也兼容旧变量 LLM_API_KEY |
| DASHSCOPE_API_KEY | 使用 Qwen/百炼时填写；也兼容旧变量 LLM_API_KEY |
| LLM_BASE_URL | 可选；DeepSeek 默认 `https://api.deepseek.com`，Qwen 默认 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| BE_BASE_URL | 默认 http://127.0.0.1:8000 |
| AGENT_BASE_URL | CLI 使用，默认 http://127.0.0.1:8001；远程部署使用 HTTPS |
| AGENT_DEBUG | 默认 false；true/1 开启脱敏诊断日志，false/0 关闭 |

配置优先级：**进程已有环境变量 > agent/.env > 代码默认值**。即使已有环境变量为空，也不会被 .env 覆盖；必填项随后会报错。程序只加载 agent 目录下的 .env，不自动选择 .env.production 等文件。

修改 .env 后重启 Agent；若曾在终端 export 同名变量，先 unset 对应变量，才能采用文件中的值。未来运行环境也可直接注入这些环境变量。仓库忽略 .env，仅提交无密钥的 .env.example。

模型创建集中在 model.py 的 create_chat_model。通过修改 `LLM_PROVIDER`、`LLM_MODEL` 和对应密钥即可切换，不必改代码。例如切到 Qwen 时设 `LLM_PROVIDER=qwen`、`LLM_MODEL=qwen3.8-max`，并填写 `DASHSCOPE_API_KEY`；切回 DeepSeek 时改为 `LLM_PROVIDER=deepseek`、对应模型名和 `DEEPSEEK_API_KEY`。Qwen 通过百炼 OpenAI 兼容接口调用；北京地域默认使用 DashScope 兼容接口，也可在百炼控制台复制业务空间专属地址后配置到 `LLM_BASE_URL`。API Key 与地域/端点须匹配。切换后重启 Agent。

## 4. 启动与对话

先按第 5 节启动 Agent API，再在 agent 目录、已激活虚拟环境的另一终端执行：

```bash
python cli.py
```

启动后先输入 `/login`，浏览器显示当前账号后输入终端展示的授权码并批准；成功后可以输入（把 XXX 换成实际完整编号）：

- `查询订单 XXX 的物流，是否延误？`
- `查询运单 XXX 的轨迹`
- `它现在是否延误？`
- `查询 AB 线路运输中的任务，每页 2 条`
- `下一页`
- `查询任务编号 XXX 的详情`

`/login` 浏览器登录/切号；`/logout` 撤销本次 CLI 登录；`/new` 删除旧会话；`/exit` 清对话并退出程序。登录凭据由系统凭据库保存，重新启动需在线验证；系统凭据库不可用时明确提示仅进程内保存，不写明文 token 文件。每次回答后显示模型、工具、HTTP 调用次数和结束原因；设置 AGENT_DEBUG=true 可在标准错误输出查看每轮 trace_id、模型/工具/HTTP 耗时、工具名、脱敏参数、结果状态及 BE request_id。业务编号以哈希摘要显示；不输出用户原文、模型回答、推理内容、完整地址或密钥。

查询将请求真实模型，产生模型服务费用；业务数据来自当前 BE。业务编号仅精确匹配，纯数字要明确是订单、运单还是任务的数据库 ID。

延误以 BE 的服务器时间和派生字段为准。区段计划到达时间不是买家收货时间；最后扫描站点也不是实时定位。

## 5. 前端助手与独立 API

在 agent 目录启动（BE 保持在 8000）：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn api:app --host 127.0.0.1 --port 8001 --reload
```

然后启动 FE，右下角点击「查询助手」。开发代理将 `/agent` 转到 `127.0.0.1:8001`，模型密钥只从 Agent 服务端 `.env` 读取。窗口可移动、缩放、放大和收起；收起或页面内跳转保留对话，刷新页面开始新会话。「新对话」清理旧会话，「停止查询」断开请求并回滚未完成的一轮。

回答下面的对象链接展示类型、业务编号和 ID，来自已校验的工具结果，不从模型回答提取。列表或历史查询可能涉及多个对象；这里展示本次查询涉及的对象，不表示它们一定存在互相依赖。

API 使用单进程内存会话：空闲 30 分钟过期，最多 100 个会话、同时 4 个查询，同一会话每次只能查询一轮。每个会话绑定当前用户、来源登录和权限版本，所有会话接口都要求只读 Bearer。等待模型期间每 2 秒在线复核，结果及缓存发送前再次复核；失效取消查询并清旧会话。服务重启清空会话，多实例共享会话尚未实现。生产代理需要保留 SSE 流、关闭响应缓冲、设置 150 秒读取超时，详见 [V8 身份与 SSE 契约](../docs/技术方案/v8/agent/spec.md)。本地 FE 需设置 `VITE_AGENT_V8_ENABLED=true` 并重启 Vite；Compose 已配置 Agent 服务、FE 构建开关及 `/agent/` 代理。模型配置从 `agent/.env` 运行时注入，不进入镜像。

## 6. 调用链与文件职责

输入 → 模型提出工具调用 → 工具校验参数 → HTTP 查询 BE → ToolMessage 回传 → 模型回答或继续查询。

| 文件 | 职责 |
| --- | --- |
| cli.py / cli_client.py | 异步输入、浏览器授权、系统凭据库、Agent API/SSE 客户端 |
| identity.py | 在线身份校验、权限复核、只读角色工具范围及每轮校验预算 |
| api.py / web_service.py | 独立 HTTP/SSE 服务、浏览器会话、并发保护、重试与取消恢复 |
| registry.py / references.py | CLI/API 共享只读工具注册；从工具结果提取已确认的对象引用 |
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

检查点由 Agent API 在每轮结束后压缩；CLI 的 /new、/exit 通过 API 删除旧会话。断流保留本轮前已完成历史，身份失效清掉整段会话，不恢复旧权限上下文。

## 7. 常见问题

- 配置报错：检查必填项、占位符及终端中同名环境变量。
- 模型认证失败／限流：分别检查密钥权限或稍后重试；其他模型请求失败检查型号和网络。日志不输出原始异常。
- 后端响应格式异常：成功状态码下的非法 JSON、缺失字段、错误类型会转换为 INVALID_RESPONSE；关联站点、schedule、历史或任务查询失败时保留其他已确认事实并标记 partial。
- 无法连接后端：检查 BE_BASE_URL、BE 进程和数据库就绪检查。
- 查询对象不存在：对照 Swagger 核对对象类型、数据库 ID 或完整编号。
- 达到调用上限：本轮查询未完成，缩小范围后继续提问；下一轮预算重置。

## 8. 收尾验证

在 agent 目录执行受控测试和依赖检查：

```bash
python -m unittest discover -s tests -v
python -m pip check
```

这些测试使用模拟模型与 HTTP，覆盖六工具链路、预算、超时恢复、响应错误、日志脱敏和检查点清理，不消耗模型额度、不修改数据库。模拟模型测试不能证明真实模型一定选择正确工具或抵抗所有文本注入。

BE 已启动且 CLI 已通过 /login 把个人凭据保存到系统凭据库时，可执行只读核验：

```bash
python verify_backend.py
```

它按当前角色查询允许的列表、各列表第一条详情和站点选项，验证真实响应字段；普通用户跳过全局任务查询。空列表的详情标记 skipped，不声称验证通过。输出仅检查名、状态和 request_id，不展示业务明细。可用 --base-url 指定 BE 地址；从 .env 读取地址，个人凭据只读系统凭据库，不接受命令行 token。

V8 隔离真实 BE 检查：`python tests/verify_v8_integration.py`。脚本创建专属临时 *_test 数据库并运行迁移，验证结束停止临时 BE、删除自己创建的测试库；不连接开发库、不调用真实模型。实际结果见 V8 交付记录。
