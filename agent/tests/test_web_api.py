import asyncio
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from api import create_app
from graph import build_graph, normalize_model_args
from references import collect_references
from state import TurnContext
from web_service import AgentService, ServiceError
from test_harness import FakeModel, call, noop
from test_harness import HarnessHelpers, ORDER, SHIPMENT, TASK, SCHEDULE, tool_results
from tools import create_order_detail_tool, create_shipment_tracking_tool
import httpx


@tool
async def get_order(order_id: int) -> dict:
    """Return a controlled order and its shipment."""
    return {"status": "ok", "data": {
        "id": str(order_id), "order_no": "ORDER-3",
        "shipment": {"id": "2", "shipment_no": "SHIP-2", "stage": "IN_TRANSIT"},
    }}


def final_event(response):
    frames = [frame for frame in response.text.split("\n\n") if frame.startswith("event: result")]
    return json.loads(frames[-1].split("data: ", 1)[1])


class ApiTests(unittest.TestCase):
    def test_real_graph_stream_references_replay_and_isolation(self):
        model = FakeModel([
            call("get_order", {"order_id": 3}), AIMessage(content="订单 3 已关联运单；模型猜测 ID 999。"),
            AIMessage(content="下一页"), AIMessage(content="请提供运单号"),
        ])
        graph = build_graph(model, [get_order])
        with TestClient(create_app(graph)) as client:
            one = client.post("/agent/api/v1/sessions").json()["session_id"]
            two = client.post("/agent/api/v1/sessions").json()["session_id"]
            body = {"message_id": str(uuid4()), "message": "查询订单 ID 3"}
            path = f"/agent/api/v1/sessions/{one}/messages"
            response = client.post(path, json=body)
            self.assertEqual(response.status_code, 200)
            self.assertIn("event: progress", response.text)
            answer = final_event(response)
            self.assertEqual(answer["references"], [
                {"type": "order", "id": "3", "number": "ORDER-3"},
                {"type": "shipment", "id": "2", "number": "SHIP-2"},
            ])
            self.assertEqual(final_event(client.post(path, json=body)), answer)
            self.assertEqual(len(model.inputs), 2)
            self.assertEqual(client.post(path, json={**body, "message": "different"}).status_code, 409)
            client.post(path, json={"message_id": str(uuid4()), "message": "它呢"})
            client.post(f"/agent/api/v1/sessions/{two}/messages", json={"message_id": str(uuid4()), "message": "它呢"})
            self.assertTrue(any("查询订单 ID 3" == message.content for message in model.inputs[2]))
            self.assertFalse(any("查询订单 ID 3" == message.content for message in model.inputs[3]))
            self.assertEqual(client.delete(f"/agent/api/v1/sessions/{one}").status_code, 204)
            self.assertEqual(client.post(path, json=body).status_code, 404)
            self.assertNotIn(one, graph.checkpointer.storage)

    def test_validation_happens_before_model(self):
        model = FakeModel([])
        with TestClient(create_app(build_graph(model, [noop]))) as client:
            ident = client.post("/agent/api/v1/sessions").json()["session_id"]
            path = f"/agent/api/v1/sessions/{ident}/messages"
            for text in ["", " ", "a" * 4001]:
                self.assertEqual(client.post(path, json={"message_id": str(uuid4()), "message": text}).status_code, 422)
            self.assertEqual(client.post(path, json={"message_id": "bad", "message": "hello"}).status_code, 422)
            self.assertFalse(model.inputs)

    def test_oversized_answer_is_replaced_in_session_memory(self):
        model = FakeModel([AIMessage(content="x" * 65537), AIMessage(content="next")])
        with TestClient(create_app(build_graph(model, [noop]))) as client:
            ident = client.post("/agent/api/v1/sessions").json()["session_id"]
            path = f"/agent/api/v1/sessions/{ident}/messages"
            first = final_event(client.post(path, json={"message_id": str(uuid4()), "message": "query"}))
            self.assertEqual(first["stop_reason"], "RESPONSE_LIMIT")
            second = final_event(client.post(path, json={"message_id": str(uuid4()), "message": "next"}))
            self.assertIsNone(second["stop_reason"])
            self.assertTrue(all(len(str(message.content)) < 65537 for message in model.inputs[1]))


class AsyncSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_limits_expiry_and_checkpoint_prune(self):
        graph = build_graph(FakeModel([]), [noop])
        service = AgentService(graph, ttl=30, max_sessions=2, max_active=1)
        first = await service.create_session()
        second = await service.create_session()
        with self.assertRaises(ServiceError) as limit:
            await service.create_session()
        self.assertEqual(limit.exception.code, "SESSION_LIMIT")
        service.acquire(first, str(uuid4()), "one")
        with self.assertRaises(ServiceError) as busy:
            service.acquire(first, str(uuid4()), "two")
        self.assertEqual(busy.exception.code, "SESSION_BUSY")
        with self.assertRaises(ServiceError) as active:
            service.acquire(second, str(uuid4()), "two")
        self.assertEqual(active.exception.code, "SERVICE_BUSY")
        first.touched_at -= 60
        await service.prune()
        self.assertIn(first.id, service.sessions)  # An active query is never evicted.
        first.busy = False
        await service.prune()
        self.assertNotIn(first.id, service.sessions)

    async def test_asgi_disconnect_rolls_back_and_releases_session(self):
        class SlowModel(FakeModel):
            async def ainvoke(self, messages):
                await asyncio.sleep(30)

        graph = build_graph(SlowModel([]), [noop])
        app = create_app(graph)
        async with app.router.lifespan_context(app):
            session = await app.state.service.create_session()
            disconnect = asyncio.Event()
            sent_request = False
            progress_count = 0

            async def receive():
                nonlocal sent_request
                if not sent_request:
                    sent_request = True
                    return {"type": "http.request", "body": json.dumps({"message_id": str(uuid4()), "message": "query"}).encode()}
                await disconnect.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                nonlocal progress_count
                if message["type"] == "http.response.body" and b"event: progress" in message.get("body", b""):
                    progress_count += 1
                    if progress_count == 2:
                        disconnect.set()

            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"}, "http_version": "1.1",
                     "method": "POST", "path": f"/agent/api/v1/sessions/{session.id}/messages", "raw_path": b"/",
                     "root_path": "", "scheme": "http", "query_string": b"", "headers": [(b"content-type", b"application/json")],
                     "client": ("127.0.0.1", 1000), "server": ("127.0.0.1", 8001)}
            await asyncio.wait_for(app(scope, receive, send), 2)
            for _ in range(20):
                if not session.busy:
                    break
                await asyncio.sleep(0.01)
            self.assertFalse(session.busy)
            snapshot = await graph.aget_state({"configurable": {"thread_id": session.id}})
            self.assertFalse(snapshot.next)
            self.assertFalse(snapshot.values.get("messages"))
            self.assertFalse(session.responses)

    async def test_reference_paths_and_bound(self):
        context = TurnContext()
        collect_references("get_shipment_schedule", {"status": "ok", "data": {"schedule": {
            "shipment_id": "2", "line_id": "999", "legs": [{"task_id": "7", "task_no": "TASK-7"}],
        }}}, context)
        self.assertEqual(list(context.references), [("shipment", "2"), ("transport_task", "7")])
        collect_references("get_order", {"status": "error", "data": {"id": "999"}}, context)
        collect_references("unknown", {"status": "ok", "data": {"id": "999"}}, context)
        self.assertNotIn(("order", "999"), context.references)
        collect_references("search_shipments", {"status": "ok", "data": {"items": [
            {"id": str(index), "shipment_no": f"S-{index}"} for index in range(1, 150)
        ]}}, context)
        self.assertEqual(len(context.references), 100)
        self.assertTrue(context.references_truncated)


class ModelArgumentTests(HarnessHelpers, unittest.IsolatedAsyncioTestCase):
    async def test_provider_string_arguments_complete_real_tool_chain(self):
        tools = [create_order_detail_tool("http://be"), create_shipment_tracking_tool("http://be")]
        payloads = {"/api/v1/orders/3": ORDER, "/api/v1/shipments/2": SHIPMENT,
                    "/api/v1/shipments/2/schedule": SCHEDULE, "/api/v1/transport-tasks/7": TASK,
                    "/api/v1/stations": [{"id": "1", "code": "A", "name": "A"}, {"id": "2", "code": "B", "name": "B"}]}
        context = TurnContext()
        model = FakeModel([call("get_order", {"order_id": "3"}),
                           call("get_shipment_tracking", {"shipment_id": "2", "include_tasks": "true"}, "second"),
                           AIMessage(content="done")])
        with self.mocked(lambda request: httpx.Response(200, json=payloads[request.url.path])):
            result, _ = await self.invoke(model, tools, context)
        self.assertEqual(context.http_calls, 5)
        self.assertEqual([item["status"] for item in tool_results(result)], ["ok", "ok"])
        self.assertEqual(set(context.references), {("order", "3"), ("shipment", "2"), ("transport_task", "7")})
        selected = tools[0]
        self.assertEqual(normalize_model_args(selected, {"order_no": "12345"}), {"order_no": "12345"})
        for value in ["0", "-3", "03", "3.0", " 3", "1e2", True]:
            self.assertEqual(normalize_model_args(selected, {"order_id": value}), {"order_id": value})
