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
from identity import IdentityError, IdentityGuard
from state import TurnContext
from tracing import trace

MAX_SESSION_ACTIONS = 20


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid4()))
    touched_at: float = field(default_factory=monotonic)
    busy: bool = False
    owner: tuple = field(default_factory=tuple)
    auth_version: int = 0
    invalid: bool = False
    task: object = field(default=None, repr=False)
    responses: OrderedDict = field(default_factory=OrderedDict, repr=False)
    actions: OrderedDict = field(default_factory=OrderedDict, repr=False)


class AgentService:
    def __init__(self, graph, debug=False, ttl=1800, max_sessions=100, max_active=4, identity_client=None, allow_actions=False):
        self.identity_client = identity_client
        self.allow_actions = allow_actions
        self.graph = graph
        self.debug = debug
        self.ttl, self.max_sessions, self.max_active = ttl, max_sessions, max_active
        self.sessions: dict[str, Session] = {}

    @staticmethod
    def action_capacity(session):
        # Keep recent terminal correlations for idempotent sync, but never evict
        # an unresolved proposal whose owner may still be confirming it.
        while len(session.actions) >= MAX_SESSION_ACTIONS:
            terminal = next((key for key, value in session.actions.items() if value.get('terminal')), None)
            if terminal is None:
                break
            session.actions.pop(terminal)
        return len(session.actions) < MAX_SESSION_ACTIONS

    async def prune(self):
        now = monotonic()
        for session in list(self.sessions.values()):
            if not session.busy and now - session.touched_at >= self.ttl:
                self.sessions.pop(session.id, None)
                await delete_session(self.graph, session.id)

    async def create_session(self, identity):
        await self.prune()
        if len(self.sessions) >= self.max_sessions:
            raise ServiceError(429, "SESSION_LIMIT", "助手会话较多，请稍后再试。")
        session = Session(owner=identity.owner, auth_version=identity.auth_version)
        self.sessions[session.id] = session
        return session

    async def get_session(self, ident, identity):
        await self.prune()
        session = self.sessions.get(ident)
        if session is None or session.invalid or session.owner != identity.owner:
            raise ServiceError(404, "SESSION_EXPIRED", "会话已过期，请开始新对话。")
        if session.auth_version != identity.auth_version:
            await self.invalidate(session)
            raise IdentityError(409, "AUTH_CHANGED", "账号权限已变化，请开始新对话。")
        session.touched_at = monotonic()
        return session

    async def remove_session(self, ident, identity):
        session = await self.get_session(ident, identity)
        if session.busy:
            raise ServiceError(409, "SESSION_BUSY", "正在查询，请停止查询后再开始新对话。")
        # Detach synchronously before deleting checkpoints, so no new turn can start.
        self.sessions.pop(ident, None)
        await delete_session(self.graph, ident)

    async def invalidate(self, session):
        session.invalid = True
        session.responses.clear()
        session.actions.clear()
        self.sessions.pop(session.id, None)
        if session.task and session.task is not asyncio.current_task() and not session.task.done():
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
        await delete_session(self.graph, session.id)

    def acquire(self, session, message_id, question):
        if session.invalid:
            raise ServiceError(404, "SESSION_EXPIRED", "会话已失效，请开始新对话。")
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

    async def run_turn(self, session, message_id, question, progress, identity, guard=None):
        config = {"configurable": {"thread_id": session.id}, "recursion_limit": 30}
        previous = deepcopy((await self.graph.aget_state(config)).values)
        session.task = asyncio.current_task()
        context = TurnContext(debug=self.debug, progress_callback=progress,
                              allow_actions=self.allow_actions and self.action_capacity(session), agent_session_id=session.id,
                              message_id=message_id,
                              identity=identity, identity_client=guard or IdentityGuard(self.identity_client))
        started = monotonic()
        try:
            async with asyncio.timeout(context.remaining_timeout(120)):
                await context.recheck_identity()
                async def watch_identity():
                    while True:
                        await asyncio.sleep(2)
                        await context.recheck_identity()
                query = asyncio.create_task(self.graph.ainvoke(
                    {"messages": [HumanMessage(content=question)]}, config=config, context=context))
                watcher = asyncio.create_task(watch_identity())
                try:
                    done, _ = await asyncio.wait({query, watcher}, return_when=asyncio.FIRST_COMPLETED)
                    if watcher in done:
                        await watcher
                    result = await query
                    await context.recheck_identity()
                finally:
                    query.cancel()
                    watcher.cancel()
                    await asyncio.gather(query, watcher, return_exceptions=True)
            answer = result["messages"][-1].content
            if not isinstance(answer, str):
                raise ValueError("unsupported model content")
            reason = result.get("stop_reason")
            if len(answer.encode("utf-8")) > 64 * 1024:
                answer = "回答过长，无法完整展示。请缩小查询范围后重试。"
                reason = "RESPONSE_LIMIT"
                await self._restore(config, previous, question, answer, reason)
        except IdentityError:
            await self.invalidate(session)
            context.references.clear()
            context.warnings.clear()
            raise
        except asyncio.CancelledError:
            # A disconnected browser must not leave half a tool-call pair in memory.
            if session.invalid:
                await delete_session(self.graph, session.id)
            else:
                await self._restore(config, previous)
                await compact_session(self.graph, config)
            trace(context, "turn", started, status="TURN_CANCELLED", http_calls=context.http_calls)
            raise
        except Exception as exc:
            reason = "TURN_TIMEOUT" if isinstance(exc, TimeoutError) else "RUN_ERROR"
            answer = "本轮查询超时，请重新提问。" if reason == "TURN_TIMEOUT" else "本轮查询失败，请重新提问。"
            await self._restore(config, previous, question, answer, reason)
            result = {"model_calls": 0, "tool_calls": 0}
        try:
            await context.recheck_identity()
            if session.invalid:
                raise IdentityError(409, "AUTH_CHANGED", "账号权限已变化，请开始新对话。")
        except IdentityError:
            await self.invalidate(session)
            raise
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
        if len(context.action_candidates) == 1:
            candidate = context.action_candidates[0]
            response['action_candidate'] = candidate
            session.actions[candidate['candidate_id']] = {
                'candidate': candidate, 'message_id': message_id, 'proposal_id': None,
            }
        session.responses[message_id] = (sha256(question.encode()).hexdigest(), response)
        while len(session.responses) > 10:
            session.responses.popitem(last=False)
        return response

    async def sync_action(self, session, identity, candidate_id, proposal_id):
        action = session.actions.get(candidate_id)
        if action is None:
            raise ServiceError(404, 'ACTION_NOT_FOUND', '该操作提案不存在或已过期。')
        candidate = action['candidate']
        if (candidate.get('agent_session_id') != session.id or
                candidate.get('message_id') != action['message_id']):
            raise ServiceError(404, 'ACTION_NOT_FOUND', '该操作提案不存在或已过期。')
        if action['proposal_id'] and action['proposal_id'] != proposal_id:
            raise ServiceError(404, 'ACTION_NOT_FOUND', '该操作提案不存在或已过期。')
        proposal = await self.identity_client.read_action_proposal(identity, proposal_id)
        if (proposal.get('candidate_id') != candidate_id or
                proposal.get('agent_session_id') != session.id or
                proposal.get('message_id') != action['message_id'] or
                proposal.get('action_type') != candidate.get('action_type')):
            raise ServiceError(404, 'ACTION_NOT_FOUND', '该操作提案不存在或已过期。')
        if proposal['status'] == 'PREVIEWED':
            raise ServiceError(409, 'ACTION_PENDING', '操作仍在等待确认或执行。')
        action['proposal_id'] = proposal_id
        verified_result = proposal.get('result') or {}
        result = {
            'candidate_id': candidate_id, 'proposal_id': proposal_id,
            'status': proposal['status'], 'action_type': proposal['action_type'],
            'subject': verified_result.get('subject') or proposal.get('subject'),
            'result_summary': verified_result or None,
        }
        action['terminal'] = True
        action['sync_result'] = result
        return result
