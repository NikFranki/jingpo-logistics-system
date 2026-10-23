# 引入依赖
import asyncio
import logging
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db import engine
from errors import NetworkError

# 引入 orders 路由
from orders.router import router as orders_router
# 引入 network 路由
from network.router import router as network_router
from shipments.router import router as shipments_router
from transport.router import router as transport_router
from scheduling.router import router as scheduling_router
from regions.router import router as regions_router
from lines.router import router as lines_router
from auth.router import router as auth_router, admin_router
from auth.external import router as external_auth_router
from agent_actions.router import router as agent_actions_router
from auth.dependencies import authorize_business
from auth.access import protect_response
from auth.logging import AuthAccessLogFilter

from fastapi.middleware.cors import CORSMiddleware
from config import CORS_ORIGINS

# 创建应用
app = FastAPI()

logger = logging.getLogger(__name__)
logging.getLogger('uvicorn.access').addFilter(AuthAccessLogFilter())

_proposal_cleanup_task = None


@app.on_event('startup')
async def start_agent_proposal_cleanup():
    async def cleanup_loop():
        while True:
            await asyncio.sleep(60)
            try:
                from agent_actions.service import cleanup_expired_proposals
                await asyncio.to_thread(cleanup_expired_proposals)
            except Exception:
                logger.exception('Agent proposal cleanup failed')

    global _proposal_cleanup_task
    _proposal_cleanup_task = asyncio.create_task(cleanup_loop())


@app.on_event('shutdown')
async def stop_agent_proposal_cleanup():
    if _proposal_cleanup_task is not None:
        _proposal_cleanup_task.cancel()
        await asyncio.gather(_proposal_cleanup_task, return_exceptions=True)

@app.middleware("http")
async def add_request_id(
    request: Request,
    call_next,
):
    request_id = str(uuid4())
    request.state.request_id = request_id

    response = await call_next(request)
    response = await protect_response(request, response)
    if request.url.path.startswith(('/api/v1/auth', '/api/v1/admin', '/api/v1/agent-actions')):
        response.headers['Cache-Control'] = 'no-store'
    response.headers["X-Request-ID"] = request_id

    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

@app.exception_handler(NetworkError)
async def handle_network_error(request: Request, exc: NetworkError):
    request_id = request.state.request_id
    return JSONResponse(status_code=exc.status, content={
        "error": {"code": exc.code, "message": exc.message, "details": None},
        "request_id": request_id,
    }, headers={"X-Request-ID": request_id})

@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException):
    request_id = request.state.request_id

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": str(exc.detail),
                "details": None,
            },
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )

@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request,
    exc: RequestValidationError,
):
    request_id = request.state.request_id

    details = [
        {
            "field": ".".join(str(item) for item in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": details,
            },
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )

@app.exception_handler(Exception)
async def handle_unexpected_error(
    request: Request,
    exc: Exception,
):
    request_id = getattr(request.state, "request_id", str(uuid4()))

    logger.error(
        "Unhandled error request_id=%s",
        request_id,
        exc_info=(type(exc), exc, exc.__traceback__),
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Internal server error",
                "details": None,
            },
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(external_auth_router)
app.include_router(agent_actions_router)
for business_router in (orders_router, network_router, shipments_router, transport_router,
                        scheduling_router, regions_router, lines_router):
    app.include_router(business_router, dependencies=[Depends(authorize_business)])

# 把 get /health 请求交给下面的函数
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/health/ready")
def readiness():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text('SELECT 1 FROM auth_users LIMIT 1'))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database not ready",
        )
    return {"status": "ready"}


@app.get("/api/v1/server-time", dependencies=[Depends(authorize_business)])
def read_server_time():
    from business_time import server_now
    return {"server_time": server_now()}
