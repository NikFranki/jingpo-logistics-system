import asyncio
import json
from time import monotonic
from results import bound_result
from tracing import trace, safe_args
from pydantic import ValidationError

from langchain_core.messages import ToolMessage, SystemMessage, AIMessage, HumanMessage, RemoveMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from prompts import SYSTEM_PROMPT
from state import AgentState, TurnContext, TurnLimitError


MAX_MODEL_CALLS = 5
MAX_TOOL_CALLS = 8
MAX_MESSAGE_BYTES = 64 * 1024


def trim_messages_by_size(messages):
    kept = list(messages)

    while True:
        payload = [
            SystemMessage(content=SYSTEM_PROMPT),
            *kept,
        ]
        size = len(
            json.dumps(
                [message.model_dump(mode="json") for message in payload],
                ensure_ascii=False,
            ).encode("utf-8")
        )

        if size <= MAX_MESSAGE_BYTES:
            return kept

        next_turn = next(
            (
                index
                for index, message in enumerate(kept)
                if index > 0 and isinstance(message, HumanMessage)
            ),
            None,
        )

        if next_turn is None:
            raise TurnLimitError(
                "CONTEXT_LIMIT",
                "当前一轮消息过大，请开始新会话或缩小查询范围",
            )

        kept = kept[next_turn:]


def prepare_turn(state: AgentState) -> dict:
    messages = state["messages"]

    # 每条用户消息表示一轮交互的起点
    turn_starts = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, HumanMessage)
    ]

    # 保留 10 轮历史，以及当前刚输入的问题
    if len(turn_starts) > 11:
        messages = messages[turn_starts[-11]:]

    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *messages,
        ],
        "model_calls": 0,
        "tool_calls": 0,
        "stop_reason": None,
    }


def create_model_node(model_with_tools):
    async def model_node(
        state: AgentState,
        runtime: Runtime[TurnContext],
    ) -> dict:
        calls = state["model_calls"]

        if calls >= MAX_MODEL_CALLS:
            return {"stop_reason": "MODEL_CALL_LIMIT"}

        try:
            messages = trim_messages_by_size(state["messages"])
            timeout = runtime.context.remaining_timeout(45)
        except TurnLimitError as exc:
            return {"stop_reason": exc.code}

        # 将裁剪结果同步写回状态
        updates = {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *messages,
            ],
        }

        started = monotonic()
        runtime.context.set_progress("思考中")
        try:
            async with asyncio.timeout(timeout):
                response = await model_with_tools.ainvoke([
                    SystemMessage(content=SYSTEM_PROMPT),
                    *messages,
                ])
        except TimeoutError:
            trace(runtime.context, "model", started, status="REQUEST_TIMEOUT", model_calls=calls + 1)
            return {
                **updates,
                "model_calls": calls + 1,
                "stop_reason": "REQUEST_TIMEOUT",
            }
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            reason = "MODEL_AUTH_ERROR" if status_code in (401, 403) else "MODEL_RATE_LIMIT" if status_code == 429 else "MODEL_ERROR"
            trace(runtime.context, "model", started, status=reason, model_calls=calls + 1)
            return {**updates, "model_calls": calls + 1, "stop_reason": reason}

        if not isinstance(response, AIMessage) or response.invalid_tool_calls or len({c["id"] for c in response.tool_calls}) != len(response.tool_calls) or any(not c["id"] for c in response.tool_calls):
            trace(runtime.context, "model", started, status="INVALID_RESPONSE", model_calls=calls + 1)
            return {**updates, "model_calls": calls + 1, "stop_reason": "INVALID_RESPONSE"}
        trace(runtime.context, "model", started, status="ok", model_calls=calls + 1)
        updates["messages"].append(response)
        return {
            **updates,
            "model_calls": calls + 1,
            "stop_reason": None,
        }

    return model_node

def create_tools_node(tools_by_name):
    async def tools_node(
        state: AgentState,
        runtime: Runtime[TurnContext],
    ) -> dict:
        response = state["messages"][-1]
        messages = []
        calls = state["tool_calls"]
        stop_reason = None

        for call in response.tool_calls:
            runtime.context.set_progress("正在查询物流数据")
            started = monotonic()
            if stop_reason:
                result = {
                    "status": "error",
                    "error": "本轮已终止，该工具未执行",
                }
            else:
                try:
                    timeout = runtime.context.remaining_timeout(120)

                    if calls >= MAX_TOOL_CALLS:
                        raise TurnLimitError(
                            "TOOL_CALL_LIMIT",
                            "工具调用额度已用完",
                        )

                    calls += 1
                    selected_tool = tools_by_name.get(call["name"])

                    if selected_tool is None:
                        result = {"status": "error", "error": "未知工具"}
                    else:
                        async with asyncio.timeout(timeout):
                            result = await selected_tool.ainvoke(
                                call["args"]
                            )

                except TurnLimitError as exc:
                    stop_reason = exc.code
                    result = {"status": "error", "error": str(exc)}
                except TimeoutError:
                    stop_reason = "TURN_TIMEOUT"
                    result = {"status": "error", "error": "本轮执行超时"}
                except ValidationError:
                    result = {"status": "error", "error": "工具参数不合法"}
                except Exception:
                    result = {"status": "error", "error": "工具执行失败"}

            result_stop = result.get("meta", {}).get("stop_reason")
            if result_stop in {"HTTP_CALL_LIMIT", "TURN_TIMEOUT"}:
                stop_reason = result_stop
            result = bound_result(result)
            trace(runtime.context, "tool", started, operation=call["name"] if call["name"] in tools_by_name else "unknown", args=safe_args(call["args"]), status=result.get("status"), tool_calls=calls)
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=call["id"],
                )
            )

        return {
            "messages": messages,
            "tool_calls": calls,
            "stop_reason": stop_reason,
        }

    return tools_node

def route_after_model(state: AgentState) -> str:
    if state["stop_reason"]:
        return "stop"

    response = state["messages"][-1]

    if response.invalid_tool_calls:
        return "stop"

    if response.tool_calls:
        return "tools"

    if response.content:
        return "end"

    return "stop"


def route_after_tools(state: AgentState) -> str:
    if state["stop_reason"]:
        return "stop"

    if state["model_calls"] >= MAX_MODEL_CALLS:
        return "stop"

    return "model"


def stop_node(state: AgentState) -> dict:
    reason = state["stop_reason"]

    if reason is None:
        last_message = state["messages"][-1]
        if isinstance(last_message, ToolMessage):
            reason = "MODEL_CALL_LIMIT"
        else:
            reason = "INVALID_RESPONSE"

    explanations = {
        "MODEL_CALL_LIMIT": "已达到本轮模型调用上限，查询未完成。",
        "MODEL_AUTH_ERROR": "模型认证失败，请检查密钥及权限。",
        "MODEL_RATE_LIMIT": "模型请求被限流，请稍后重试。",
        "MODEL_ERROR": "模型请求失败，查询未完成，请稍后重试。",
        "INVALID_RESPONSE": "模型返回格式异常，查询未完成。",
        "TOOL_CALL_LIMIT": "已达到本轮工具调用上限，查询未完成。",
        "TURN_TIMEOUT": "本轮执行时间已用完，查询未完成。",
        "REQUEST_TIMEOUT": "模型请求超时，查询未完成。",
        "HTTP_CALL_LIMIT": "已达到本轮 HTTP 请求上限，查询未完成。",
        "CONTEXT_LIMIT": "当前一轮消息过大，查询未完成，请开始新会话或缩小查询范围。",
    }

    return {
        "stop_reason": reason,
        "messages": [
            AIMessage(content=explanations[reason])
        ],
    }

def build_graph(model, tools):
    tools_by_name = {tool.name: tool for tool in tools}
    model_with_tools = model.bind_tools(tools)

    builder = StateGraph(
        AgentState,
        context_schema=TurnContext,
    )

    builder.add_node("prepare_turn", prepare_turn)
    builder.add_node("model", create_model_node(model_with_tools))
    builder.add_node("tools", create_tools_node(tools_by_name))
    builder.add_node("stop", stop_node)

    builder.add_edge(START, "prepare_turn")
    builder.add_edge("prepare_turn", "model")

    builder.add_conditional_edges(
        "model",
        route_after_model,
        {
            "tools": "tools",
            "stop": "stop",
            "end": END,
        },
    )

    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "model": "model",
            "stop": "stop",
        },
    )

    builder.add_edge("stop", END)

    return builder.compile(checkpointer=InMemorySaver())
