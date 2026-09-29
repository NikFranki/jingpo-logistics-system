import asyncio
from uuid import uuid4
from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from config import load_settings
from graph import build_graph
from model import create_chat_model
from tools import (
    create_transport_task_detail_tool,
    create_transport_task_search_tool,
    create_order_detail_tool,
    create_order_search_tool,
    create_shipment_search_tool,
    create_shipment_tracking_tool,
)
from state import TurnContext

async def restore_turn(
    graph,
    run_config,
    previous_state,
    question,
    reason,
    message,
):
    await graph.aupdate_state(
        run_config,
        {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *previous_state.get("messages", []),
                HumanMessage(content=question),
                AIMessage(content=message),
            ],
            "model_calls": 0,
            "tool_calls": 0,
            "stop_reason": reason,
        },
        as_node="stop",
    )

async def main() -> None:
    settings = load_settings()
    model = create_chat_model(settings)
    tracking_tool = create_shipment_tracking_tool(settings.be_base_url)
    search_tool = create_shipment_search_tool(settings.be_base_url)
    order_tool = create_order_detail_tool(settings.be_base_url)
    order_search_tool = create_order_search_tool(settings.be_base_url)
    task_tool = create_transport_task_detail_tool(settings.be_base_url)
    task_search_tool = create_transport_task_search_tool(settings.be_base_url)
    graph = build_graph(model, [
        tracking_tool, search_tool, order_tool, order_search_tool,
        task_tool, task_search_tool,
    ])

    thread_id = str(uuid4())
    print("输入 /new 开始新会话，/exit 退出。")

    while True:
        question = input("你：").strip()

        if question == "/exit":
            break

        if question == "/new":
            thread_id = str(uuid4())
            print("已开始新会话。")
            continue

        if not question:
            continue

        if len(question) > 4000:
            print("助手：输入不能超过 4000 个字符，请缩短后重试。")
            continue

        run_config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 30,
        }

        snapshot = await graph.aget_state(run_config)
        previous_state = deepcopy(snapshot.values)

        context = TurnContext()

        try:
            async with asyncio.timeout(context.remaining_timeout(120)):
                result = await graph.ainvoke(
                    {
                        "messages": [HumanMessage(content=question)],
                    },
                    config=run_config,
                    context=context,
                )
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                reason = "TURN_TIMEOUT"
                message = "本轮执行超时，查询未完成，请重新提问。"
            else:
                reason = "RUN_ERROR"
                message = "本轮执行异常，查询未完成，请重新提问。"

            await restore_turn(
                graph,
                run_config,
                previous_state,
                question,
                reason,
                message,
            )
            print(f"助手：{message}")
            continue

        print(f"助手：{result['messages'][-1].content}")
        print(f"模型调用次数：{result['model_calls']}")
        print(f"工具调用次数：{result['tool_calls']}")
        print(f"结束原因：{result['stop_reason'] or '正常结束'}")
        print(f"HTTP 请求次数：{context.http_calls}")


if __name__ == "__main__":
    asyncio.run(main())
