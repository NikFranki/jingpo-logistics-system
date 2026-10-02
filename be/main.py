# 引入依赖
import logging
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db import engine
from errors import NetworkError

# 引入 simulation 路由
from simulation.router import router as simulation_router
# 引入 orders 路由
from orders.router import router as orders_router
# 引入 network 路由
from network.router import router as network_router
from shipments.router import router as shipments_router
from transport.router import router as transport_router
from planning.router import router as planning_router
from scheduling.router import router as scheduling_router

from fastapi.middleware.cors import CORSMiddleware
from config import CORS_ORIGINS

# 创建应用
app = FastAPI()

logger = logging.getLogger(__name__)

@app.middleware("http")
async def add_request_id(
    request: Request,
    call_next,
):
    request_id = str(uuid4())
    request.state.request_id = request_id

    response = await call_next(request)
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

@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException):
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

app.include_router(simulation_router)
app.include_router(orders_router)
app.include_router(network_router)
app.include_router(shipments_router)
app.include_router(transport_router)
app.include_router(planning_router)
app.include_router(scheduling_router)

# 把 get /health 请求交给下面的函数
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/health/ready")
def readiness():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database not ready",
        )
    return {"status": "ready"}
