"""Run separately from BE: python -m uvicorn api:app --port 8001."""
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


def create_app(graph=None, debug=False):
    @asynccontextmanager
    async def lifespan(app):
        selected_graph = graph
        selected_debug = debug
        if selected_graph is None:
            settings = load_settings()
            selected_graph = build_graph(create_chat_model(settings), build_tools(settings.be_base_url))
            selected_debug = settings.debug
        app.state.service = AgentService(selected_graph, selected_debug)
        yield
        for ident in list(app.state.service.sessions):
            await selected_graph.checkpointer.adelete_thread(ident)

    app = FastAPI(title="JINGPO 查询助手", lifespan=lifespan)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={
            "error": {"code": exc.code, "message": exc.message},
        })

    @app.get("/agent/health")
    async def health():
        return {"status": "ok"}

    @app.post("/agent/api/v1/sessions", status_code=201)
    async def create_session(request: Request):
        session = await request.app.state.service.create_session()
        return {"session_id": session.id, "expires_after_seconds": 1800}

    @app.delete("/agent/api/v1/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: UUID, request: Request):
        await request.app.state.service.remove_session(str(session_id))

    @app.post("/agent/api/v1/sessions/{session_id}/messages")
    async def message(session_id: UUID, body: MessageInput, request: Request):
        service = request.app.state.service
        session = await service.get_session(str(session_id))
        ident = str(body.message_id)
        cached = service.acquire(session, ident, body.message)

        async def stream():
            if cached is not None:
                yield event("result", cached)
                return
            queue = asyncio.Queue()
            task = asyncio.create_task(service.run_turn(session, ident, body.message, queue.put_nowait))
            pending_progress = None
            try:
                yield event("progress", {"message": "思考中"})
                while True:
                    pending_progress = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait(
                        {task, pending_progress}, timeout=15, return_when=asyncio.FIRST_COMPLETED,
                    )
                    if task in done:
                        yield event("result", task.result())
                        break
                    if pending_progress in done:
                        yield event("progress", {"message": pending_progress.result()})
                    else:
                        yield ": keep-alive\n\n"
                    pending_progress.cancel()
                    await asyncio.gather(pending_progress, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                yield event("error", {"code": "RUN_ERROR", "message": "助手查询失败，请重试或开始新对话。"})
            finally:
                if pending_progress is not None:
                    pending_progress.cancel()
                def release(_):
                    session.busy = False
                    session.touched_at = monotonic()
                # Keep the lock until rollback completes, including on browser disconnect.
                task.add_done_callback(release)
                if not task.done():
                    task.cancel()
                try:
                    await asyncio.shield(task)
                except (asyncio.CancelledError, Exception):
                    pass

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })

    return app


app = create_app()
