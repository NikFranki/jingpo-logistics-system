"""Bounded, isolated browser sessions over the existing read-only graph."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from time import monotonic
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from memory import compact_session, delete_session
from state import TurnContext
from tracing import trace


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid4()))
    touched_at: float = field(default_factory=monotonic)
    busy: bool = False
    responses: OrderedDict = field(default_factory=OrderedDict, repr=False)


class AgentService:
    def __init__(self, graph, debug=False, ttl=1800, max_sessions=100, max_active=4):
        self.graph = graph
        self.debug = debug
        self.ttl, self.max_sessions, self.max_active = ttl, max_sessions, max_active
        self.sessions: dict[str, Session] = {}

    async def prune(self):
        now = monotonic()
        for session in list(self.sessions.values()):
            if not session.busy and now - session.touched_at >= self.ttl:
                self.sessions.pop(session.id, None)
                await delete_session(self.graph, session.id)

    async def create_session(self):
        await self.prune()
        if len(self.sessions) >= self.max_sessions:
            raise ServiceError(429, "SESSION_LIMIT", "助手会话较多，请稍后再试。")
        session = Session()
        self.sessions[session.id] = session
        return session

    async def get_session(self, ident):
        await self.prune()
        session = self.sessions.get(ident)
        if session is None:
            raise ServiceError(404, "SESSION_EXPIRED", "会话已过期，请开始新对话。")
        session.touched_at = monotonic()
        return session

    async def remove_session(self, ident):
        session = await self.get_session(ident)
        if session.busy:
            raise ServiceError(409, "SESSION_BUSY", "正在查询，请停止查询后再开始新对话。")
        # Detach synchronously before deleting checkpoints, so no new turn can start.
        self.sessions.pop(ident, None)
        await delete_session(self.graph, ident)

    def acquire(self, session, message_id, question):
        digest = sha256(question.encode()).hexdigest()
        cached = session.responses.get(message_id)
        if cached is not None:
            if cached[0] != digest:
                raise ServiceError(409, "MESSAGE_CONFLICT", "重试请求与原问题不一致，请重新发送。")
            return cached[1]
        if session.busy:
            raise ServiceError(409, "SESSION_BUSY", "当前对话正在查询，请稍后重试。")
        if sum(item.busy for item in self.sessions.values()) >= self.max_active:
            raise ServiceError(429, "SERVICE_BUSY", "助手正在处理其他查询，请稍后重试。")
        session.busy = True
        return None

    async def _restore(self, config, previous, question=None, answer=None, reason=None):
        messages = list(previous.get("messages", []))
        if question is not None:
            messages.extend([HumanMessage(content=question), AIMessage(content=answer)])
        await self.graph.aupdate_state(config, {
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages],
            "model_calls": 0, "tool_calls": 0, "stop_reason": reason,
        }, as_node="stop")

    async def run_turn(self, session, message_id, question, progress):
        config = {"configurable": {"thread_id": session.id}, "recursion_limit": 30}
        previous = deepcopy((await self.graph.aget_state(config)).values)
        context = TurnContext(debug=self.debug, progress_callback=progress)
        started = monotonic()
        try:
            async with asyncio.timeout(context.remaining_timeout(120)):
                result = await self.graph.ainvoke(
                    {"messages": [HumanMessage(content=question)]}, config=config, context=context,
                )
            answer = result["messages"][-1].content
            if not isinstance(answer, str):
                raise ValueError("unsupported model content")
            reason = result.get("stop_reason")
            if len(answer.encode("utf-8")) > 64 * 1024:
                answer = "回答过长，无法完整展示。请缩小查询范围后重试。"
                reason = "RESPONSE_LIMIT"
                await self._restore(config, previous, question, answer, reason)
        except asyncio.CancelledError:
            # A disconnected browser must not leave half a tool-call pair in memory.
            await self._restore(config, previous)
            await compact_session(self.graph, config)
            trace(context, "turn", started, status="TURN_CANCELLED", http_calls=context.http_calls)
            raise
        except Exception as exc:
            reason = "TURN_TIMEOUT" if isinstance(exc, TimeoutError) else "RUN_ERROR"
            answer = "本轮查询超时，请重新提问。" if reason == "TURN_TIMEOUT" else "本轮查询失败，请重新提问。"
            await self._restore(config, previous, question, answer, reason)
            result = {"model_calls": 0, "tool_calls": 0}
        await compact_session(self.graph, config)
        trace(context, "turn", started, status=reason or "ok", http_calls=context.http_calls)
        response = {
            "message_id": message_id, "answer": answer,
            "references": list(context.references.values()),
            "references_truncated": context.references_truncated,
            "warnings": context.warnings[:20], "stop_reason": reason,
            "usage": {"model_calls": result.get("model_calls", 0),
                      "tool_calls": result.get("tool_calls", 0), "http_calls": context.http_calls},
            "trace_id": context.trace_id,
        }
        session.responses[message_id] = (sha256(question.encode()).hexdigest(), response)
        while len(session.responses) > 10:
            session.responses.popitem(last=False)
        return response
