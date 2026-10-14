import asyncio
import sys
from uuid import uuid4
from copy import deepcopy
from prompt_toolkit import PromptSession

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from config import load_settings
from graph import build_graph
from model import create_chat_model
from registry import build_tools
from state import TurnContext
from memory import compact_session, delete_session
from tracing import trace
from time import monotonic


async def show_progress(status):
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    index = 0
    while True:
        text = f"\r{frames[index % len(frames)]} {status['message']}…"
        if sys.stderr.isatty():
            sys.stderr.write(text)
            sys.stderr.flush()
        else:
            sys.stderr.write(f"{status['message']}…\n")
            sys.stderr.flush()
            await asyncio.sleep(2)
            continue
        index += 1
        await asyncio.sleep(0.1)


def clear_progress():
    if sys.stderr.isatty():
        sys.stderr.write("\r\x1b[2K")
        sys.stderr.flush()


async def read_question():
    if sys.stdin.isatty():
        return (await PromptSession().prompt_async("你：")).strip()
    return input("你：").strip()

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
    graph = build_graph(model, build_tools(settings.be_base_url))

    thread_id = str(uuid4())
    print("输入 /new 开始新会话，/exit 退出。")

    while True:
        try:
            question = await read_question()
        except (EOFError, KeyboardInterrupt):
            await delete_session(graph, thread_id)
            break

        if question == "/exit":
            await delete_session(graph, thread_id)
            break

        if question == "/new":
            await delete_session(graph, thread_id)
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

        progress = {"message": "思考中"}
        context = TurnContext(
            debug=settings.debug,
            progress_callback=lambda message: progress.update(message=message),
        )
        started = monotonic()
        progress_task = asyncio.create_task(show_progress(progress))

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
            progress_task.cancel()
            await asyncio.gather(progress_task, return_exceptions=True)
            clear_progress()
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
            trace(context, "turn", started, status=reason, http_calls=context.http_calls)
            await compact_session(graph, run_config)
            continue
        else:
            progress_task.cancel()
            await asyncio.gather(progress_task, return_exceptions=True)
            clear_progress()

        trace(context, "turn", started, status=result["stop_reason"] or "ok", model_calls=result["model_calls"], tool_calls=result["tool_calls"], http_calls=context.http_calls)
        await compact_session(graph, run_config)
        print(f"助手：{result['messages'][-1].content}")
        print(f"模型调用次数：{result['model_calls']}")
        print(f"工具调用次数：{result['tool_calls']}")
        print(f"结束原因：{result['stop_reason'] or '正常结束'}")
        print(f"HTTP 请求次数：{context.http_calls}")


if __name__ == "__main__":
    asyncio.run(main())
