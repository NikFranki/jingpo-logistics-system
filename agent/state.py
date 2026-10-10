from typing import Annotated, Callable, TypedDict
from dataclasses import dataclass, field
from time import monotonic
from uuid import uuid4

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # messages：保存用户、模型和工具消息；add_messages 负责合并新增消息
    messages: Annotated[list[AnyMessage], add_messages]
    # model_calls：记录本轮已调用模型的次数
    model_calls: int
    # stop_reason：记录异常或达到上限的原因，正常结束时为 None
    stop_reason: str | None
    tool_calls: int

class TurnLimitError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# 每轮创建一个 TurnContext，最多执行 120 秒、24 次 HTTP 请求
@dataclass
class TurnContext:
    deadline: float = field(
        default_factory=lambda: monotonic() + 120
    )
    http_calls: int = 0
    debug: bool = False
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    progress_callback: Callable[[str], None] | None = field(default=None, repr=False)

    def set_progress(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def remaining_timeout(self, maximum: float) -> float:
        remaining = self.deadline - monotonic()

        if remaining <= 0:
            raise TurnLimitError(
                "TURN_TIMEOUT",
                "本轮执行时间已用完",
            )

        return min(maximum, remaining)

    def reserve_http_call(self) -> None:
        self.remaining_timeout(10)

        if self.http_calls >= 24:
            raise TurnLimitError(
                "HTTP_CALL_LIMIT",
                "本轮 HTTP 请求次数已达上限",
            )

        self.http_calls += 1
