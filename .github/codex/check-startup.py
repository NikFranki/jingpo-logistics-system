"""Exercise the real Codex CLI against a local Responses API; no key is needed."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[2]
PATCH = "*** Begin Patch\n*** Add File: startup-ok.txt\n+ok\n*** End Patch"


class ResponsesHandler(BaseHTTPRequestHandler):
    calls = 0
    errors = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        try:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/v1/responses", self.path
            assert request["model"] == "deepseek-flash"
            assert request["reasoning"]["effort"] == "high"
            assert any(tool.get("name") == "apply_patch" for tool in request["tools"])
            type(self).calls += 1
            assert self.calls <= 2, "Unexpected extra model request"
            if self.calls == 1:
                output = {
                    "type": "custom_tool_call", "id": "tool_1", "call_id": "call_1",
                    "name": "apply_patch", "input": PATCH, "status": "completed",
                }
            else:
                assert any(item.get("type") == "custom_tool_call_output" for item in request["input"])
                output = {
                    "type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "STARTUP_OK", "annotations": []}],
                }
            response = {
                "id": f"resp_{self.calls}", "object": "response", "model": "deepseek-flash",
                "created_at": 1, "status": "completed", "output": [output],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
            events = [
                ("response.created", {"response": {**response, "status": "in_progress", "output": []}}),
                ("response.output_item.done", {"output_index": 0, "item": output}),
                ("response.completed", {"response": response}),
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for sequence, (event, data) in enumerate(events):
                data.update(type=event, sequence_number=sequence)
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
        except Exception as error:
            self.errors.append(str(error))
            self.send_error(500, str(error))


def check_startup(codex):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ResponsesHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="codex-startup-") as temporary:
            directory = Path(temporary)
            codex_home = directory / "codex-home"
            workspace = directory / "workspace"
            codex_home.mkdir()
            workspace.mkdir()
            catalog = ROOT / ".github/codex/deepseek-models.json"
            config = (
                'model = "deepseek-flash"\nmodel_provider = "startup-check"\n'
                'model_reasoning_effort = "high"\nweb_search = "disabled"\n'
                f'model_catalog_json = {json.dumps(str(catalog))}\n'
                '[model_providers.startup-check]\nname = "Local startup check"\n'
                f'base_url = "http://127.0.0.1:{server.server_port}/v1"\nwire_api = "responses"\n'
                'request_max_retries = 0\nstream_max_retries = 0\n'
            )
            (codex_home / "config.toml").write_text(config)
            environment = {
                key: value for key, value in os.environ.items()
                if key in {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "SYSTEMROOT"}
            }
            environment["CODEX_HOME"] = str(codex_home)
            result = subprocess.run(
                [codex, "exec", "--skip-git-repo-check", "--ephemeral", "--json",
                 "--cd", str(workspace), "--config", 'default_permissions=":workspace"',
                 "--output-last-message", str(directory / "answer.txt"),
                 "Use apply_patch to create startup-ok.txt containing ok, then reply STARTUP_OK."],
                env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
            )
            assert not ResponsesHandler.errors, ResponsesHandler.errors
            assert result.returncode == 0, result.stderr[-6000:] + result.stdout[-6000:]
            assert ResponsesHandler.calls == 2, ResponsesHandler.calls
            assert (workspace / "startup-ok.txt").read_text() == "ok\n"
            assert (directory / "answer.txt").read_text().strip() == "STARTUP_OK"
            print("PASS: real Codex CLI loaded the catalog, called apply_patch, wrote a file, and completed.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex", help="Codex CLI executable")
    check_startup(parser.parse_args().codex)
