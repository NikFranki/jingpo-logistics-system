# Separate planned task sharing from transport execution

**Status: Accepted**

运单可以共享符合线路分段与时间窗条件的运输任务，不要求订单时间与车次时刻完全相同；不同目的地的运单可以共享共同经过的前段，并分别安排后续运输。未来计划关联与当前执行占用分别记录，计划或任务创建不代表货物已就绪、已发车或已到达；实际位置和履约进度只由后端操作事实确认。
