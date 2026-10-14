"""Object links come from known tool result paths, never from model prose."""
import re

MAX_REFERENCES = 100
OBJECT_ID = re.compile(r"^[1-9][0-9]{0,18}$")


def collect_references(tool_name: str, result: dict, context) -> None:
    data = result.get("data")
    if result.get("status") == "error" or not isinstance(data, dict):
        return

    def add(kind, ident, number=None):
        if not isinstance(ident, str) or not OBJECT_ID.fullmatch(ident):
            return
        key = (kind, ident)
        if key not in context.references and len(context.references) >= MAX_REFERENCES:
            context.references_truncated = True
            return
        previous = context.references.get(key, {})
        context.references[key] = {
            "type": kind, "id": ident,
            "number": number if isinstance(number, str) and len(number) <= 128 else previous.get("number"),
        }

    def object_item(kind, item):
        if isinstance(item, dict):
            field = {"order": "order_no", "shipment": "shipment_no", "transport_task": "task_no"}[kind]
            add(kind, item.get("id"), item.get(field))

    kind = {
        "search_orders": "order", "get_order": "order",
        "search_shipments": "shipment", "get_shipment_tracking": "shipment",
        "search_transport_tasks": "transport_task", "get_transport_task": "transport_task",
    }.get(tool_name)
    if kind:
        object_item(kind, data)
        for field in ("items", "candidates"):
            for item in data.get(field, []):
                object_item(kind, item)
    if tool_name == "get_order":
        object_item("shipment", data.get("shipment"))
    if tool_name == "get_transport_task":
        for item in data.get("shipments", []):
            object_item("shipment", item)
    if tool_name in {"get_shipment_tracking", "get_shipment_schedule"}:
        schedule = data.get("schedule") or {}
        add("shipment", schedule.get("shipment_id"))
        for schedule_item in [schedule, *data.get("history", [])]:
            for leg in schedule_item.get("legs", []):
                add("transport_task", leg.get("task_id"), leg.get("task_no"))
        object_item("transport_task", data.get("active_transport_task"))
        for item in data.get("tasks", []):
            object_item("transport_task", item)
        for event in data.get("tracking_events", []):
            add("transport_task", event.get("task_id"))
    if tool_name == "get_shipment_destination_changes":
        add("shipment", data.get("shipment_id"))
    if tool_name == "find_shipments_with_destination_changes":
        for item in data.get("items", []):
            add("shipment", item.get("shipment_id"), item.get("shipment_no"))
