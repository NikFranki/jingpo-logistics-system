# 此 module 处理事务和业务

from datetime import datetime, timedelta, timezone
import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import OperationLog, SimulationSettings

from errors import (
    IdempotencyKeyReusedError,
    SimulationClockNotInitializedError,
)


# 相同内容得到相同摘要 不同内容得到不同摘要
def build_request_hash(minutes: int) -> str:
    content = f"ADVANCE_CLOCK:{minutes}"
    return hashlib.sha256(content.encode()).hexdigest()

def advance_clock(
    session: Session,
    minutes: int,
    idempotency_key: UUID,
) -> datetime:
    request_hash = build_request_hash(minutes)

    # 开启事务；正常结束自动提交，发生异常自动回滚
    with session.begin():
        statement = (
            select(SimulationSettings)
            .where(SimulationSettings.id == 1)
            .with_for_update() # 锁住时钟这一行，避免两个请求同时读取并覆盖
        )
        settings = session.scalar(statement)

        if settings is None:
            raise SimulationClockNotInitializedError

        # 查询 key 是否执行过
        existing_log = session.scalar(
            select(OperationLog).where(
                OperationLog.idempotency_key == idempotency_key
            )
        )

        # 执行过则返回旧结果
        if existing_log is not None:
            if existing_log.request_hash != request_hash:
                raise IdempotencyKeyReusedError

            return datetime.fromisoformat(
                existing_log.response_body["current_time"]
            )

        previous_time = settings.current_time
        settings.current_time += timedelta(minutes=minutes)
        settings.updated_at = datetime.now(timezone.utc)

        new_time = settings.current_time
        response_body = {
            "current_time": new_time.isoformat(),
        }

        # 没执行过才更新时间并写操作日志
        session.add(
            OperationLog(
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                action="ADVANCE_CLOCK",
                resource_type="CLOCK",
                resource_id=1,
                before_data={
                    "current_time": previous_time.isoformat(),
                },
                after_data=response_body,
                response_body=response_body,
                response_status=200,
                occurred_at=new_time,
            )
        )

    return new_time