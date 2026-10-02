from collections.abc import Generator
# sqlalchemy 数据库 orm 工具
# sqlalchemy 描述表长什么样；Alembic 记录数据库结构如何变化；
from sqlalchemy import create_engine
# sqlalchemy orm 工具
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# 配置文件导出的 DATABASE_URL
from config import DATABASE_URL

# 给 SQLAlchemy 一个“所有数据表的共同入口”，收集所有的表
class Base(DeclarativeBase):
    pass

# 管理数据库连接
engine = create_engine(DATABASE_URL, connect_args={"options": "-c timezone=UTC"})

# 一次请求操作数据库的工作区
SessionLocal = sessionmaker(bind=engine)

# 为请求提供 Session，请求结束后自动关闭（创建数据库 Session -> yield 给接口使用 -> 接口执行完成 -> 退出 with，自动关闭 Session）
def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
