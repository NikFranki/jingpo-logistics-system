"""Run the V10 question set against a fixed synthetic backend and the configured real model.

Answers are saved under /tmp for manual scoring; no real business records are used.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import re
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval_scoring import score_answer, failed, assessment, SCORING_VERSION
from config import load_settings
from graph import build_graph
from identity import Identity
from model import create_chat_model
from registry import build_tools
from state import TurnContext

CASES = json.loads((ROOT / "tests/eval_v10_cases.json").read_text())
SERVER_TIME = "2026-10-18T08:00:00Z"
TS = "2026-10-18T"
STATIONS = [
    {"id":"1","code":"SJZ","name":"石家庄站"},
    {"id":"2","code":"BJS","name":"北京站"},
    {"id":"3","code":"TJS","name":"天津站"},
]


def fixture_data():
    orders = []
    shipments = []
    for index in range(1, 13):
        shipment_id = 100 + index if index != 3 else None
        orders.append({
            "id":str(index), "order_no":f"ORD-{index:04d}", "product_name":"演示包裹",
            "quantity":1, "status":"CANCELLED" if index == 2 else "CREATED" if index == 3 else "SHIPPED",
            "created_by_user_id":"1", "cancelled_at":"2026-10-17T08:00:00Z" if index == 2 else None,
            "cancel_reason":"演示取消" if index == 2 else None,
            "earliest_handover_at":None,"latest_delivery_at":None,
            "sender_province_name":"河北省","sender_city_name":"石家庄市","sender_district_name":"长安区",
            "recipient_province_name":"北京市","recipient_city_name":"北京市","recipient_district_name":"朝阳区",
            "shipment":None if shipment_id is None else {"id":str(shipment_id),"shipment_no":f"SHP-{shipment_id:04d}","stage":"CANCELLED" if index == 2 else "IN_TRANSIT" if index == 1 else "PENDING_PICKUP"},
        })
        if shipment_id is None:
            continue
        stage = "IN_TRANSIT" if index == 1 else "CANCELLED" if index == 2 else "AT_STATION" if index == 4 else "PENDING_PICKUP"
        schedule = None
        active = None
        events = []
        if index == 1:
            schedule = {
                "shipment_id":"101","status":"CONFIRMED","reason":"时间调整","version":2,
                "line_id":"1","line_version":1,"scheduled_trip_id":None,"origin_station_id":"1",
                "destination_station_id":"2","server_time":SERVER_TIME,"configuration_risks":[],
                "legs":[
                    {"position":0,"route_id":"1","route_code":"SJZ-BJS","origin_station_id":"1","destination_station_id":"2","task_id":"301","task_no":"TASK-0301","task_status":"IN_TRANSIT","association_state":"ACTIVE","ready_at":None,"origin_station":STATIONS[0],"destination_station":STATIONS[1],"planned_departure_at":TS+"07:00:00Z","planned_arrival_at":TS+"07:30:00Z","forecast_departure_at":TS+"07:05:00Z","forecast_arrival_at":TS+"07:40:00Z","actual_departure_at":TS+"07:10:00Z","actual_arrival_at":None,"planned_travel_minutes":30,"travel_reference_minutes":30,"transfer_reference_minutes":None,"approved_transfer_minutes":None},
                    {"position":1,"route_id":"2","route_code":"BJS-TJS","origin_station_id":"2","destination_station_id":"3","task_id":"302","task_no":"TASK-0302","task_status":"WAITING_PREDECESSOR","association_state":"PLANNED","ready_at":TS+"07:40:00Z","origin_station":STATIONS[1],"destination_station":STATIONS[2],"planned_departure_at":TS+"08:00:00Z","planned_arrival_at":TS+"09:00:00Z","forecast_departure_at":None,"forecast_arrival_at":None,"actual_departure_at":None,"actual_arrival_at":None,"planned_travel_minutes":60,"travel_reference_minutes":60,"transfer_reference_minutes":None,"approved_transfer_minutes":None},
                ],
            }
            active = {"id":"301","route_code":"SJZ-BJS","origin_station_id":"1","destination_station_id":"2","status":"IN_TRANSIT"}
            events = [{"id":"1","event_type":"DEPART","occurred_at":TS+"07:10:00Z","station_id":"1","task_id":"301"}]
        elif index == 4:
            schedule = {"shipment_id":"104","status":"NEEDS_RECONFIRMATION","reason":"目的站更正","version":2,"line_id":"1","line_version":1,"scheduled_trip_id":None,"origin_station_id":"1","destination_station_id":"3","server_time":SERVER_TIME,"configuration_risks":[],"legs":[{"position":0,"route_id":"2","route_code":"BJS-TJS","origin_station_id":"2","destination_station_id":"3","task_id":"304","task_no":"TASK-0304","task_status":"CANCELLED","association_state":"RELEASED","ready_at":None,"origin_station":STATIONS[1],"destination_station":STATIONS[2],"planned_departure_at":None,"planned_arrival_at":None,"forecast_departure_at":None,"forecast_arrival_at":None,"actual_departure_at":None,"actual_arrival_at":None,"planned_travel_minutes":60,"travel_reference_minutes":60,"transfer_reference_minutes":None,"approved_transfer_minutes":None}]}
        shipments.append({
            "id":str(shipment_id),"shipment_no":f"SHP-{shipment_id:04d}","stage":stage,
            "destination_station_id":"2" if index == 1 else "3","last_scanned_station_id":"1" if index in (1,4) else None,
            "planned_origin_station_id":"1","earliest_handover_at":None,"latest_delivery_at":None,
            "sender_province_name":"河北省","sender_city_name":"石家庄市","sender_district_name":"长安区",
            "recipient_province_name":"北京市","recipient_city_name":"北京市","recipient_district_name":"朝阳区",
            "active_transport_task":active,"tracking_events":events,"schedule":schedule,
        })
    orders.append({"id":"21","order_no":"ORD-1001","product_name":"另一用户包裹","quantity":1,"status":"SHIPPED","created_by_user_id":"2","cancelled_at":None,"cancel_reason":None,"earliest_handover_at":None,"latest_delivery_at":None,"sender_province_name":None,"sender_city_name":None,"sender_district_name":None,"recipient_province_name":None,"recipient_city_name":None,"recipient_district_name":None,"shipment":{"id":"201","shipment_no":"SHP-0201","stage":"IN_TRANSIT"}})
    shipments.append({"id":"201","shipment_no":"SHP-0201","stage":"IN_TRANSIT","destination_station_id":"2","last_scanned_station_id":"1","planned_origin_station_id":"1","earliest_handover_at":None,"latest_delivery_at":None,"sender_province_name":None,"sender_city_name":None,"sender_district_name":None,"recipient_province_name":None,"recipient_city_name":None,"recipient_district_name":None,"active_transport_task":None,"tracking_events":[],"schedule":None})

    tasks = []
    for index in range(301, 313):
        status = "IN_TRANSIT" if index == 301 or index >= 305 else "WAITING_PREDECESSOR" if index == 302 else "ARRIVED" if index == 303 else "CANCELLED"
        delay_status = "OVERDUE" if index == 301 else "LATE_ARRIVAL" if index == 303 else "NOT_APPLICABLE" if status in ("WAITING_PREDECESSOR","CANCELLED") else "NONE"
        tasks.append({"id":str(index),"task_no":f"TASK-{index:04d}","route_code":"SJZ-BJS" if index % 2 else "BJS-TJS","status":status,"expected_arrival_at":TS+"07:30:00Z","departed_at":TS+"07:10:00Z" if status in ("IN_TRANSIT","ARRIVED") else None,"arrived_at":TS+"07:50:00Z" if status == "ARRIVED" else None,"delay_status":delay_status,"delay_minutes":60 if index == 301 else 20 if index == 303 else 0 if delay_status == "NONE" else None,"delay_monitoring_enabled":True,"scheduled_trip_id":None,"scheduled_trip_missed":False,"planned_departure_at":TS+"07:00:00Z","forecast_departure_at":TS+"07:05:00Z","forecast_arrival_at":TS+"07:40:00Z","forecast_stale":False,"scheduling_source":"SCHEDULE","schedule_revision":2,"waiting_members":[],"origin_station_id":"1","destination_station_id":"2","server_time":SERVER_TIME,"shipments":[{"id":"101","shipment_no":"SHP-0101","stage":"IN_TRANSIT"}]})

    changes = []
    for index in range(51):
        shipment_id = "101" if index < 26 else "104"
        changes.append({"id":str(500 + index),"shipment_id":shipment_id,"shipment_no":f"SHP-{shipment_id.zfill(4)}","previous_destination_station_id":"2","destination_station_id":"3","reason":f"演示更正 {index + 1}","occurred_at":f"2026-10-{17 if index < 26 else 16}T07:{index % 60:02d}:00Z"})
    history = [{"version":1,"reason":"初始计划","occurred_at":TS+"06:00:00Z","legs":schedule_legs("PLANNED")},{"version":2,"reason":"时间调整","occurred_at":TS+"06:30:00Z","legs":schedule_legs("ACTIVE")}]
    # Build all views from one canonical object set, never duplicate state literals.
    by_shipment = {item["id"]: item for item in shipments}
    by_shipment["101"]["destination_station_id"] = "3"
    by_shipment["101"]["schedule"]["destination_station_id"] = "3"
    for order in orders:
        if order["shipment"]:
            shipment = by_shipment[order["shipment"]["id"]]
            order["shipment"] = {key: shipment[key] for key in ("id", "shipment_no", "stage")}
    for task in tasks:
        middle = task["route_code"] == "BJS-TJS"
        task["origin_station_id"] = "2" if middle else "1"
        task["destination_station_id"] = "3" if middle else "2"
        task["shipments"] = [deepcopy(orders[0]["shipment"])] if task["id"] in ("301", "302") else []
        if task["id"] == "301":
            task["delay_minutes"] = 30
    history[1]["legs"] = deepcopy(by_shipment["101"]["schedule"]["legs"])
    history[0]["legs"] = deepcopy(history[1]["legs"])
    history[0]["legs"][0]["planned_departure_at"] = TS + "06:50:00Z"
    for entry in history:
        for leg in entry["legs"]:
            leg["actual_departure_at"] = None
            leg["actual_arrival_at"] = None
            leg["association_state"] = "PLANNED"
            leg["task_status"] = "WAITING_PREDECESSOR"
    for index, change in enumerate(changes):
        local = index if index < 26 else index - 26
        start = "3" if change["shipment_id"] == "101" else "2"
        previous = start if local % 2 == 0 else ("3" if start == "2" else "2")
        change["previous_destination_station_id"] = previous
        change["destination_station_id"] = "3" if previous == "2" else "2"
        change["occurred_at"] = f"2026-10-17T07:{index:02d}:00Z"
    return orders, shipments, tasks, changes, history


def schedule_legs(state):
    return [{"position":0,"route_id":"1","route_code":"SJZ-BJS","origin_station_id":"1","destination_station_id":"2","task_id":"301","task_no":"TASK-0301","task_status":"IN_TRANSIT","association_state":state,"planned_departure_at":TS+"07:00:00Z","planned_arrival_at":TS+"07:30:00Z","actual_departure_at":TS+"07:10:00Z","actual_arrival_at":None,"forecast_departure_at":TS+"07:05:00Z","forecast_arrival_at":TS+"07:40:00Z","forecast_stale":False,"ready_at":None,"waiting_members":[],"scheduling_source":"SCHEDULE","schedule_revision":2,"planned_travel_minutes":30,"travel_reference_minutes":30,"transfer_reference_minutes":None,"approved_transfer_minutes":None}]


ORDERS, SHIPMENTS, TASKS, CHANGES, HISTORY = fixture_data()
REQUESTS = []
REQUEST_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def respond(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path.removeprefix("/api/v1"), parse_qs(parsed.query)
        token = self.headers.get("Authorization", "")
        own = token == "Bearer eval-user"
        company = token in ("Bearer eval-company", "Bearer eval-degraded")
        with REQUEST_LOCK:
            REQUESTS.append({"path":parsed.path,"query":query,"principal":"user" if own else "company" if company else "unknown"})
        if not (own or company):
            return self.respond(401, {"error":{"code":"AUTH_REQUIRED"}})
        if token == "Bearer eval-degraded" and path.startswith("/stations"):
            return self.respond(503, {"error":{"code":"TEMPORARY"}})
        if path in ("/stations", "/ordering-options"):
            return self.respond(200, {"stations":STATIONS} if path == "/ordering-options" else STATIONS)
        if path == "/orders":
            values = [item for item in ORDERS if company or item["created_by_user_id"] == "1"]
            if query.get("order_no"): values = [item for item in values if item["order_no"] == query["order_no"][0]]
            if query.get("shipment_no"): values = [item for item in values if item.get("shipment") and item["shipment"]["shipment_no"] == query["shipment_no"][0]]
            if query.get("stage"): values = [item for item in values if item.get("shipment") and item["shipment"]["stage"] == query["stage"][0]]
            return self.page(values, query)
        if path.startswith("/orders/"):
            item = next((item for item in ORDERS if item["id"] == path.split("/")[2]), None)
            if item is None or (own and item["created_by_user_id"] != "1"): return self.respond(404, {})
            return self.respond(200, item)
        if path == "/shipments/destination-changes":
            if own: return self.respond(403, {})
            return self.page(list(reversed(CHANGES)), query)
        if path == "/shipments":
            values = [item for item in SHIPMENTS if company or item["id"] != "201"]
            if query.get("shipment_no"): values = [item for item in values if item["shipment_no"] == query["shipment_no"][0]]
            if query.get("stage"): values = [item for item in values if item["stage"] == query["stage"][0]]
            return self.page(values, query)
        if path.startswith("/shipments/"):
            pieces = path.split("/")
            shipment_id = pieces[2]
            item = next((item for item in SHIPMENTS if item["id"] == shipment_id), None)
            if item is None or (own and shipment_id == "201"): return self.respond(404, {})
            if len(pieces) == 3: return self.respond(200, item)
            if len(pieces) == 4 and pieces[3] == "schedule":
                return self.respond(404, {}) if item.get("schedule") is None else self.respond(200, item["schedule"])
            if len(pieces) == 4 and pieces[3] == "schedule-history":
                return self.page(HISTORY if shipment_id == "101" else [], query)
            if len(pieces) == 4 and pieces[3] == "destination-changes":
                values = [change for change in reversed(CHANGES) if change["shipment_id"] == shipment_id]
                return self.page(values, query)
        if path == "/transport-tasks":
            if own: return self.respond(403, {})
            values = TASKS
            if query.get("status"): values = [item for item in values if item["status"] == query["status"][0]]
            if query.get("route_code"): values = [item for item in values if item["route_code"] == query["route_code"][0]]
            if query.get("task_no"): values = [item for item in values if item["task_no"] == query["task_no"][0]]
            return self.page(values, query, task_page=True)
        if path.startswith("/transport-tasks/"):
            if own: return self.respond(403, {})
            item = next((item for item in TASKS if item["id"] == path.split("/")[2]), None)
            return self.respond(404, {}) if item is None else self.respond(200, item)
        return self.respond(404, {})

    def page(self, items, query, task_page=False):
        page = max(1, int(query.get("page", [1])[0]))
        page_size = max(1, int(query.get("page_size", [10])[0]))
        start = (page - 1) * page_size
        result = {"items":items[start:start + page_size],"total":len(items),"page":page,"page_size":page_size}
        if task_page: result["server_time"] = SERVER_TIME
        return self.respond(200, result)


class FakeIdentityGuard:
    async def recheck(self, identity):
        return None


async def run_case(model, base_url, case):
    company = case["identity"] == "company"
    identity = Identity("2" if company else "1", "eval-session-company" if company else "eval-session-user", 1,
                        "ops" if company else "user", datetime(2030,1,1,tzinfo=timezone.utc),
                        "company" if company else "own", "business", "eval-company" if company else "eval-user")
    if case.get("degraded"):
        identity = Identity(identity.user_id, identity.login_session_id, identity.auth_version, identity.role,
                            identity.expires_at, identity.data_scope, identity.answer_style, "eval-degraded")
    context = TurnContext(identity=identity, identity_client=FakeIdentityGuard())
    graph = build_graph(model, build_tools(base_url))
    with REQUEST_LOCK:
        REQUESTS.clear()
    result = await graph.ainvoke({"messages":[HumanMessage(content=case["question"])]},
                                config={"configurable":{"thread_id":str(uuid4())}}, context=context)
    answer = result["messages"][-1].content
    refs = sorted([[item["type"],item["id"]] for item in context.references.values()])
    expected = sorted(case.get("refs", []))
    missing = [ref for ref in expected if ref not in refs]
    calls, usages, tool_summaries = [], [], []
    tool_names = {}
    for message in result["messages"]:
        if isinstance(message, AIMessage):
            calls.extend({"name":call["name"],"args":call.get("args",{})} for call in message.tool_calls)
            tool_names.update({call["id"]: call["name"] for call in message.tool_calls})
            if message.usage_metadata: usages.append(message.usage_metadata)
        elif isinstance(message, ToolMessage):
            try:
                value = json.loads(message.content)
                tool_summaries.append({"name":tool_names.get(message.tool_call_id, message.name),"status":value.get("status"),"pagination":value.get("meta",{}).get("pagination"),"data_summary":(value.get("data") or {}).get("summary"),"without_order_ids":[item["id"] for item in (value.get("data") or {}).get("without_shipment", [])],"warning_count":len(value.get("warnings",[]))})
            except (ValueError, TypeError):
                tool_summaries.append({"name":message.name,"status":"unparseable"})
    missing_tool_facts = []
    expected_tool_facts = case.get("tool_facts")
    if expected_tool_facts:
        candidates = [item.get("data_summary") for item in tool_summaries if item.get("data_summary")]
        summary = next((item for item in candidates if item.get("all_records_complete") is not None), {})
        if summary.get("records_returned") != expected_tool_facts["records_returned"]:
            missing_tool_facts.append("records_returned")
        if summary.get("distinct_shipments_returned") != expected_tool_facts["distinct_shipments"]:
            missing_tool_facts.append("distinct_shipments")
        actual_counts = {str(item.get("shipment_id")):item.get("record_count") for item in summary.get("by_shipment", [])}
        if actual_counts != expected_tool_facts["aggregate_by_shipment"]:
            missing_tool_facts.append("aggregate_by_shipment")
        if not summary.get("all_records_complete"):
            missing_tool_facts.append("all_records_complete")
    if case.get("presence_facts"):
        candidates = [item for item in tool_summaries if item["name"] == "search_orders"]
        actual = candidates[-1] if candidates else {}
        summary = actual.get("data_summary") or {}
        for key, expected_value in case["presence_facts"].items():
            value = actual.get(key) if key == "without_order_ids" else summary.get(key)
            if value != expected_value:
                missing_tool_facts.append("presence:" + key)
    with REQUEST_LOCK:
        requests = list(REQUESTS)
    return {"id":case["id"],"group":case["group"],"question":case["question"],"answer":answer,
            "expected_references":expected,"references":refs,"missing_references":missing,
            "critical":case.get("critical",False),
            "expected_tool_facts":expected_tool_facts,"missing_tool_facts":missing_tool_facts,
            "stop_reason":result.get("stop_reason"),"model_calls":result.get("model_calls"),"tool_calls":len(calls),
            "http_calls":context.http_calls,"tool_plan":calls,"tool_results":tool_summaries,"backend_requests":requests,
            "usage_metadata":usages,"manual_review":"pending", **score_answer(case, answer)}


def validate_fixture(orders, shipments, tasks, changes, history):
    by_shipment = {item["id"]: item for item in shipments}
    by_task = {item["id"]: item for item in tasks}
    for order in orders:
        if order["shipment"]:
            linked = order["shipment"]
            assert all(linked[key] == by_shipment[linked["id"]][key] for key in ("id", "shipment_no", "stage")), "order/shipment mismatch"
    for shipment in shipments:
        schedule = shipment.get("schedule")
        if schedule and schedule["status"] == "CONFIRMED":
            assert schedule["legs"][-1]["destination_station_id"] == shipment["destination_station_id"], "plan destination mismatch"
        for leg in (schedule or {}).get("legs", []):
            task = by_task[leg["task_id"]]
            assert all(task[key] == leg[key] for key in ("route_code", "origin_station_id", "destination_station_id")), "task route mismatch"
            assert task["status"] == leg["task_status"], "task status mismatch"
    for shipment in shipments:
        logs = [item for item in changes if item["shipment_id"] == shipment["id"]]
        for before, after in zip(logs, logs[1:]):
            assert before["destination_station_id"] == after["previous_destination_station_id"], "change chain mismatch"
        if logs:
            assert logs[-1]["destination_station_id"] == shipment["destination_station_id"], "latest destination mismatch"
    current = by_shipment["101"]["schedule"]
    assert {leg["task_id"] for leg in history[-1]["legs"]} == {leg["task_id"] for leg in current["legs"]}, "current/history topology mismatch"


def serve_fixture():
    validate_fixture(ORDERS, SHIPMENTS, TASKS, CHANGES, HISTORY)
    server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


async def preflight_fixture(base_url):
    from be_client import (
        get_order, get_shipment, get_shipment_schedule, get_transport_task, get_stations,
        list_orders, list_shipments, list_transport_tasks, list_all_shipment_destination_changes,
        list_shipment_destination_changes, list_shipment_schedule_history,
    )
    from tools import _collect_pages, _destination_change_summary, _result
    from state import TurnLimitError
    company = Identity("2", "eval-company", 1, "ops", datetime(2030,1,1,tzinfo=timezone.utc), "company", "business", "eval-company")
    user = Identity("1", "eval-user", 1, "user", datetime(2030,1,1,tzinfo=timezone.utc), "own", "business", "eval-user")
    ctx = TurnContext(identity=company, identity_client=FakeIdentityGuard())
    assert len((await list_orders(base_url, ctx, page_size=20))["data"]["items"]) == 13
    assert (await get_order(base_url, 1, ctx))["data"]["shipment"]["id"] == "101"
    assert len((await list_shipments(base_url, ctx, page_size=100))["data"]["items"]) == 12
    assert (await get_shipment(base_url, 101, ctx))["data"]["schedule"]["legs"][1]["association_state"] == "PLANNED"
    assert (await get_shipment_schedule(base_url, 101, ctx))["data"]["version"] == 2
    assert len((await list_shipment_schedule_history(base_url, 101, ctx, page_size=20))["data"]["items"]) == 2
    assert len((await list_all_shipment_destination_changes(base_url, ctx, page_size=100))["data"]["items"]) == 51
    summary = _destination_change_summary(CHANGES, True)
    assert summary["records_returned"] == 51 and summary["distinct_shipments_returned"] == 2
    assert {item["shipment_id"]:item["record_count"] for item in summary["by_shipment"]} == {"101":26,"104":25}
    changes = (await list_shipment_destination_changes(base_url, 101, ctx, page_size=50))["data"]["items"]
    assert len(changes) == 26 and changes[0]["id"] == "525"
    assert len((await list_transport_tasks(base_url, ctx, status="IN_TRANSIT", page_size=20))["data"]["items"]) == 9
    assert (await get_transport_task(base_url, 301, ctx))["data"]["delay_status"] == "OVERDUE"
    user_ctx = TurnContext(identity=user, identity_client=FakeIdentityGuard())
    assert len((await list_orders(base_url, user_ctx, page_size=20))["data"]["items"]) == 12
    assert (await get_stations(base_url, user_ctx))["data"][0]["name"] == "石家庄站"
    calls = []
    async def fake_page(page):
        calls.append(page)
        items = [{"id":str(i)} for i in range((page - 1) * 2 + 1, min(page * 2, 5) + 1)]
        return {"data":{"items":items,"page":page,"page_size":2,"total":5},"request_id":None}
    collected, pagination = await _collect_pages(fake_page, _result(), 1, 2, True)
    assert calls == [1, 2, 3] and len(collected) == 5 and pagination["all_records_complete"]
    calls.clear()
    async def exhausted_page(page):
        if page == 1:
            return {"data":{"items":[{"id":"1"}],"page":1,"page_size":1,"total":2},"request_id":None}
        raise TurnLimitError("HTTP_CALL_LIMIT", "budget")
    partial_result = _result()
    collected, pagination = await _collect_pages(exhausted_page, partial_result, 1, 1, True)
    assert len(collected) == 1 and not pagination["all_records_complete"] and partial_result["status"] == "partial"
    print("synthetic fixture schema and role-scope preflight passed")


async def main_async(round_name, cases):
    import asyncio
    settings = load_settings()
    model = create_chat_model(settings)
    server, base = serve_fixture()
    try:
        outputs = []
        for case in cases:
            print(f"[{round_name}] {case['id']} ({case['group']})", flush=True)
            try:
                outputs.append(await run_case(model, base, case))
            except Exception as exc:
                outputs.append({"id":case["id"],"group":case["group"],"question":case["question"],"expected_references":case.get("refs", []),"missing_references":case.get("refs", []),"error_type":type(exc).__name__,"manual_review":"failed"})
        for item in outputs:
            item["assessment"] = assessment(item)
        total_refs = sum(len(item.get("expected_references",[])) for item in outputs)
        covered_refs = total_refs - sum(len(item.get("missing_references",[])) for item in outputs)
        tracked = sorted([*ROOT.glob("*.py"), *ROOT.glob("tests/*.py"), ROOT / "tests/eval_v10_cases.json"])
        digest = hashlib.sha256()
        for source in tracked:
            digest.update(source.relative_to(ROOT).as_posix().encode())
            digest.update(source.read_bytes())
        output = {"round":round_name,"sample_version":CASES["version"],"scoring_version":SCORING_VERSION,"git_commit":__import__("subprocess").check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),"working_tree_code_sha256":digest.hexdigest(),"provider":settings.provider,"model":settings.model,"started_at":datetime.now(timezone.utc).isoformat(),"case_count":len(cases),"scope":{"included":len(cases),"excluded":[]},"reference_coverage":{"covered":covered_refs,"expected":total_refs},"cases":outputs}
        path = Path("/tmp") / "jingpo-agent-v10" / f"{round_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output,ensure_ascii=False,indent=2))
        failures = sum(failed(item) for item in outputs)
        print(json.dumps({"report":str(path),"cases":len(outputs),"failed_machine_checks":failures,"infra_or_stop_cases":[item["id"] for item in outputs if item.get("stop_reason") or item.get("error_type")],"reference_coverage":output["reference_coverage"],"manual_review":"pending"},ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", help="Unique label, for example baseline-1")
    parser.add_argument("--holdout-only", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--case-id", help="只运行指定题目，用于补跑基础设施错误")
    parser.add_argument("--rescore", type=Path, help="离线重评已有报告，不调用模型")
    args = parser.parse_args()
    import asyncio
    if args.rescore:
        report = json.loads(args.rescore.read_text())
        if report.get("sample_version") != CASES["version"]:
            parser.error("样本版本不同，不能把旧数据报告算作当前样本验收")
        definitions = {case['id']: case for case in CASES['cases']}
        for item in report['cases']:
            case = definitions[item['id']]
            item.update(score_answer(case, item.get('answer', '')))
            item['assessment'] = assessment(item)
            item['expected_references'] = case.get('refs', [])
            item['missing_references'] = [ref for ref in case.get('refs', []) if ref not in item.get('references', [])]
        report['rescored_from'] = str(args.rescore)
        report['source_sha256'] = hashlib.sha256(args.rescore.read_bytes()).hexdigest()
        report['reference_coverage'] = {"expected": sum(len(x["expected_references"]) for x in report["cases"]), "covered": sum(len(x["expected_references"]) - len(x["missing_references"]) for x in report["cases"])}
        report['scoring_version'] = SCORING_VERSION
        report['rescored_at'] = datetime.now(timezone.utc).isoformat()
        report['failed_machine_checks'] = [item['id'] for item in report['cases'] if failed(item)]
        report['manual_review'] = 'pending'
        path = args.rescore.with_name(args.rescore.stem + '.rescored.json')
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'report': str(path), 'failed': report['failed_machine_checks']}, ensure_ascii=False))
    elif args.preflight:
        server, base = serve_fixture()
        try:
            asyncio.run(preflight_fixture(base))
        finally:
            server.shutdown()
            server.server_close()
    else:
        if not args.round:
            parser.error("--round is required for model evaluation")
        selected = CASES["cases"][-5:] if args.holdout_only else CASES["cases"]
        if args.case_id:
            selected = [case for case in selected if case["id"] == args.case_id]
            if not selected:
                parser.error(f"unknown case id: {args.case_id}")
        asyncio.run(main_async(args.round, selected))
