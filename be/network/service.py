from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from models import Station, TransportRoute


def list_stations(session: Session) -> list[Station]:
    statement = select(Station).order_by(Station.code)
    return list(session.scalars(statement))


def list_routes(
    session: Session,
) -> list[tuple[TransportRoute, Station, Station]]:
    # 为什么需要 aliased()：同一条线路要连接 stations 两次，一次代表起点，一次代表终点。别名让数据库知道这两个角色不同
    origin = aliased(Station)
    destination = aliased(Station)

    statement = (
        select(TransportRoute, origin, destination)
        .join(
            origin,
            TransportRoute.origin_station_id == origin.id,
        )
        .join(
            destination,
            TransportRoute.destination_station_id == destination.id,
        )
        .order_by(TransportRoute.code)
    )

    return list(session.execute(statement).tuples())