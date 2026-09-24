# 此模块处理校验输入输出
# 定义 接口返回给调用方什么数据
# 主要负责
# 类型判断
# 必填字段
# 基础格式校验
# 基础范围校验
# 定义接口返回结构

from datetime import datetime

from typing import Literal

from pydantic import BaseModel

# 表示只能传 {"minutes": 30} or {"minutes": 120}
class SimulationClockAdvanceRequest(BaseModel):
    minutes: Literal[30, 120]


class SimulationClockResponse(BaseModel):
    current_time: datetime