# 物流查询 Agent

本地 CLI，使用 LangGraph、LangChain 组件和 ChatDeepSeek，通过 BE 的只读接口查询订单、运单、运输任务和延误。支持当前会话内追问，不执行发货、发车、签收或其他写操作。

业务主流程已实现，异常处理、日志等收尾项见 [plan.md](../docs/技术方案/v1/agent/plan.md) 第 9 节；尚未完成完整 V1 验收。

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

编辑 .env，把 LLM_MODEL 换成你已验证可用、支持工具调用的 DeepSeek 型号，把 LLM_API_KEY 换成自己的密钥。已有 .env 继续使用，无需覆盖。示例型号是占位符，不是实际可请求的型号。

| 变量 | 说明 |
| --- | --- |
| LLM_PROVIDER | 当前仅支持 deepseek，默认也是 deepseek |
| LLM_MODEL | 必填，实际模型名称 |
| LLM_API_KEY | 必填，模型服务密钥 |
| LLM_BASE_URL | 默认 https://api.deepseek.com |
| BE_BASE_URL | 默认 http://127.0.0.1:8000 |

配置优先级：**进程已有环境变量 > agent/.env > 代码默认值**。即使已有环境变量为空，也不会被 .env 覆盖；必填项随后会报错。程序只加载 agent 目录下的 .env，不自动选择 .env.production 等文件。

修改 .env 后重启 Agent；若曾在终端 export 同名变量，先 unset 对应变量，才能采用文件中的值。未来运行环境也可直接注入这些环境变量。仓库忽略 .env，仅提交无密钥的 .env.example。

模型创建集中在 model.py 的 create_chat_model；目前只实现 DeepSeek，换供应商需要新增适配逻辑，不能仅修改 provider。

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

`/new` 开始新会话；`/exit` 退出。进程退出后对话不恢复。每次回答后显示模型、工具、HTTP 调用次数和结束原因；详细调用日志尚在 TODO。

查询将请求真实模型，产生模型服务费用；业务数据来自当前 BE。业务编号仅精确匹配，纯数字要明确是订单、运单还是任务的数据库 ID。

延误以 BE 的演示时间和派生字段为准。区段计划到达时间不是买家收货时间；最后扫描站点也不是实时定位。

## 5. 调用链与文件职责

输入 → 模型提出工具调用 → 工具校验参数 → HTTP 查询 BE → ToolMessage 回传 → 模型回答或继续查询。

| 文件 | 职责 |
| --- | --- |
| cli.py | 输入循环、会话切换、异常恢复及输出 |
| config.py / model.py | 读取配置、创建模型适配器 |
| graph.py / state.py | LangGraph 循环、会话状态和每轮预算 |
| tools.py | 六个查询工具及订单→运单→任务关联 |
| be_client.py | 固定 BE 路径的 GET 请求及 HTTP 错误处理 |
| prompts.py | 澄清、工具选择和有依据的回答规则 |

每轮最多 5 次模型调用、8 次工具尝试、24 次 HTTP 请求。第 5 次模型仍可调用工具，执行后停止，不发起第 6 次模型请求；如果第 5 次直接给出最终回答，则正常结束。

单次模型等待最多 45 秒，HTTP 最多 10 秒，整轮最多 120 秒。输入最多 4,000 字符，保留最多 10 轮历史加当前轮，并按 64 KiB 消息上限裁剪。工具结果裁剪和旧检查点清理仍待补齐。

## 6. 常见问题

- 配置报错：检查必填项、占位符及终端中同名环境变量。
- 模型请求失败：检查型号、密钥权限、账户额度和网络；当前错误提示未进一步区分认证和限流。
- 无法连接后端：检查 BE_BASE_URL、BE 进程和数据库就绪检查。
- 查询对象不存在：对照 Swagger 核对对象类型、数据库 ID 或完整编号。
- 达到调用上限：本轮查询未完成，缩小范围后继续提问；下一轮预算重置。

本文按当前代码与本机包元数据整理，未重新创建干净环境或执行真实模型请求。当前实际模型名未从私有 .env 读取，留待用户记录无密钥的型号名称。
