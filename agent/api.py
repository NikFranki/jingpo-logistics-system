"""Authenticated V8 Agent API. Run separately from BE on port 8001."""
import asyncio
from contextlib import asynccontextmanager
import json
from time import monotonic
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import load_settings
from graph import build_graph
from identity import IdentityClient, IdentityError, IdentityGuard
from model import create_chat_model
from registry import build_tools
from web_service import AgentService, ServiceError


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: UUID
    message: str = Field(min_length=1, max_length=4000, strict=True)

    @field_validator("message")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


def event(name, data):
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(graph=None, debug=False, identity_client=None):
    @asynccontextmanager
    async def lifespan(app):
        selected_graph, selected_debug, auth = graph, debug, identity_client
        if selected_graph is None:
            settings = load_settings()
            selected_graph = build_graph(create_chat_model(settings), build_tools(settings.be_base_url))
            selected_debug = settings.debug
            auth = auth or IdentityClient(settings.be_base_url)
        if auth is None:
            # Injected graphs still use real authentication unless an explicit test client is supplied.
            import os
            auth = IdentityClient(os.getenv("BE_BASE_URL", "http://127.0.0.1:8000"))
        app.state.identity_client = auth
        app.state.service = AgentService(selected_graph, selected_debug, identity_client=auth)
        yield
        for session in list(app.state.service.sessions.values()):
            await app.state.service.invalidate(session)

    app = FastAPI(title="JINGPO 查询助手", lifespan=lifespan)

    @app.exception_handler(IdentityError)
    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc):
        return JSONResponse(status_code=exc.status, content={
            "error": {"code": exc.code, "message": exc.message},
        }, headers={"Cache-Control": "no-store"})

    async def authenticate(request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise IdentityError()
        return await request.app.state.identity_client.authenticate(header[7:])

    @app.get("/agent/health")
    async def health():
        return {"status": "ok"}

    @app.post("/agent/api/v1/sessions", status_code=201)
    async def create_session(request: Request):
        identity = await authenticate(request)
        session = await request.app.state.service.create_session(identity)
        return JSONResponse(status_code=201, content={"session_id": session.id, "expires_after_seconds": 1800},
                            headers={"Cache-Control": "no-store"})

    @app.delete("/agent/api/v1/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: UUID, request: Request):
        identity = await authenticate(request)
        await request.app.state.service.remove_session(str(session_id), identity)

    @app.post("/agent/api/v1/sessions/{session_id}/messages")
    async def message(session_id: UUID, body: MessageInput, request: Request):
        identity = await authenticate(request)
        service = request.app.state.service
        session = await service.get_session(str(session_id), identity)
        ident = str(body.message_id)
        cached = service.acquire(session, ident, body.message)
        guard = IdentityGuard(request.app.state.identity_client)

        async def stream():
            task = pending_progress = None
            try:
                await guard.recheck(identity)
                if session.invalid:
                    raise IdentityError(409, "AUTH_CHANGED", "账号权限已变化，请开始新对话。")
                if cached is not None:
                    yield event("result", cached)
                    return
                queue = asyncio.Queue(maxsize=32)
                def progress(text):
                    if not queue.full():
                        queue.put_nowait(text)
                task = asyncio.create_task(service.run_turn(session, ident, body.message, progress, identity, guard))
                yield event("progress", {"message": "思考中"})
                while True:
                    pending_progress = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait({task, pending_progress}, timeout=2,
                                                 return_when=asyncio.FIRST_COMPLETED)
                    await guard.recheck(identity)
                    if task in done:
                        result = task.result()
                        if session.invalid:
                            raise IdentityError(409, "AUTH_CHANGED", "账号权限已变化，请开始新对话。")
                        yield event("result", result)
                        break
                    if pending_progress in done:
                        yield event("progress", {"message": pending_progress.result()})
                    else:
                        yield ": keep-alive\n\n"
                    pending_progress.cancel()
                    await asyncio.gather(pending_progress, return_exceptions=True)
            except IdentityError as exc:
                await service.invalidate(session)
                yield event("error", {"code": exc.code, "message": exc.message})
            except asyncio.CancelledError:
                raise
            except Exception:
                yield event("error", {"code": "RUN_ERROR", "message": "助手查询失败，请重试或开始新对话。"})
            finally:
                if pending_progress is not None:
                    pending_progress.cancel()
                if task is not None:
                    def release(_):
                        session.busy = False
                        session.touched_at = monotonic()
                    task.add_done_callback(release)
                    if not task.done():
                        task.cancel()
                    try:
                        await asyncio.shield(task)
                    except (asyncio.CancelledError, Exception):
                        pass
                if cached is None and task is None:
                    session.busy = False
                    session.touched_at = monotonic()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        })

    return app


app = create_app()
