# 此模块负责处理 HTTP

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException

from sqlalchemy.orm import Session

from db import get_db
from models import SimulationSettings
from simulation.schemas import (
    SimulationClockAdvanceRequest,
    SimulationClockResponse,
)

from errors import (
    IdempotencyKeyReusedError,
    SimulationClockNotInitializedError,
)
from simulation.service import advance_clock


router = APIRouter(
    prefix="/api/v1/simulation",
    tags=["simulation"],
)


@router.get("/clock", response_model=SimulationClockResponse)
def get_clock(
    # db 的类型是 Session，并且由 FastAPI 通过 Depends(get_session) 提供。
    # FastAPI 调用 get_db()
    session: Annotated[Session, Depends(get_db)],
) -> SimulationClockResponse:
    # 根据主键 1 查询数据
    settings = session.get(SimulationSettings, 1)

    if settings is None:
        raise HTTPException(
            status_code=503,
            detail="Simulation clock is not initialized",
        )

    # 转成响应模型
    return SimulationClockResponse(
        current_time=settings.current_time,
    )

@router.post(
    "/clock/advance",
    response_model=SimulationClockResponse,
)
def advance_simulation_clock(
    request: SimulationClockAdvanceRequest,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> SimulationClockResponse:
    try:
        current_time = advance_clock(
            session=session,
            minutes=request.minutes,
            idempotency_key=idempotency_key,
        )
    except SimulationClockNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Simulation clock is not initialized",
        )
    except IdempotencyKeyReusedError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with different content",
        )

    return SimulationClockResponse(
        current_time=current_time,
    )