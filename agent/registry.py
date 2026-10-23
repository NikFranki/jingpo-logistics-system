"""One read-only tool registry shared by the CLI and HTTP service."""
from tools import (
    create_destination_change_shipment_scan_tool,
    create_destination_changes_tool,
    create_order_detail_tool,
    create_order_search_tool,
    create_shipment_schedule_tool,
    create_shipment_search_tool,
    create_shipment_tracking_tool,
    create_transport_task_detail_tool,
    create_transport_task_search_tool,
)


def build_tools(base_url: str, allow_actions: bool = False):
    tools = [factory(base_url) for factory in (
        create_shipment_tracking_tool, create_shipment_search_tool,
        create_order_detail_tool, create_order_search_tool,
        create_transport_task_detail_tool, create_transport_task_search_tool,
        create_shipment_schedule_tool, create_destination_changes_tool,
        create_destination_change_shipment_scan_tool,
    )]
    if allow_actions:
        from actions import build_action_tools
        tools.extend(build_action_tools(base_url))
    return tools
