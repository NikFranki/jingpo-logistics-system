import asyncio
import sys
from uuid import uuid4
from prompt_toolkit import PromptSession

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES



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
    from cli_client import CLIClient
    from identity import IdentityError
    client = CLIClient.configured()
    resumed = await client.resume()
    print("输入 /login 浏览器登录，/logout 退出登录，/new 新对话，/exit 退出程序。")
    if resumed:
        print("已恢复个人登录。")
    while True:
        try:
            question = await read_question()
        except (EOFError, KeyboardInterrupt):
            break
        if question == "/exit":
            break
        try:
            if question == "/login":
                await client.login()
                continue
            if question == "/logout":
                await client.logout()
                print("已退出登录。")
                continue
            if question == "/new":
                await client.new_session()
                print("已开始新对话。")
                continue
            if not question:
                continue
            if len(question) > 4000:
                print("输入不能超过 4000 个字符。")
                continue
            if not client.token:
                print("请先输入 /login，在浏览器授权当前个人账号。")
                continue
            progress = {"message": "思考中"}
            spinner = asyncio.create_task(show_progress(progress))
            try:
                result = await client.query(question, str(uuid4()), lambda message: progress.update(message=message))
            finally:
                spinner.cancel()
                await asyncio.gather(spinner, return_exceptions=True)
                clear_progress()
            print(f"助手：{result['answer']}")
            for key, label in (("model_calls", "模型调用次数"), ("tool_calls", "工具调用次数"), ("http_calls", "HTTP 请求次数")):
                print(f"{label}：{result['usage'][key]}")
        except IdentityError as exc:
            print(exc.message)
        except (ValueError, KeyError):
            print("服务响应异常，请稍后重试。")
        except Exception:
            print("请求失败，请检查服务连接后重试。")
    try:
        await client.new_session()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
