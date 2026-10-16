"""Personal CLI login and SSE client; no model key or local query graph."""
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
import webbrowser

import httpx
from dotenv import load_dotenv

from identity import IdentityClient, IdentityError


def safe_url(value):
    url = urlsplit(value)
    if (url.username or url.password or url.query or url.fragment or not url.hostname
            or url.scheme not in {"http", "https"}
            or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("登录和查询地址须使用 HTTPS，本机地址可使用 HTTP。")
    return value.rstrip("/")


class CredentialStore:
    def __init__(self, be_url, agent_url):
        self.service = "jingpo-agent:" + sha256((be_url + "|" + agent_url).encode()).hexdigest()[:20]
        self.backend = None
        try:
            import keyring
            if keyring.get_keyring().priority > 0:
                self.backend = keyring
        except Exception:
            pass

    def load(self):
        try:
            return self.backend.get_password(self.service, "personal") if self.backend else None
        except Exception:
            return None

    def save(self, token):
        try:
            if self.backend:
                self.backend.set_password(self.service, "personal", token)
                return True
        except Exception:
            pass
        return False

    def clear(self):
        try:
            if self.backend:
                self.backend.delete_password(self.service, "personal")
        except Exception:
            pass


class CLIClient:
    def __init__(self, be_url, agent_url, store=None):
        self.be_url, self.agent_url = safe_url(be_url), safe_url(agent_url)
        self.store = store or CredentialStore(self.be_url, self.agent_url)
        self.auth = IdentityClient(self.be_url)
        self.token = None
        self.session_id = None

    @classmethod
    def configured(cls):
        load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
        return cls(os.getenv("BE_BASE_URL", "http://127.0.0.1:8000"),
                   os.getenv("AGENT_BASE_URL", "http://127.0.0.1:8001"))

    async def resume(self):
        token = await asyncio.to_thread(self.store.load)
        if token:
            try:
                await self.auth.authenticate(token)
                self.token = token
                return True
            except IdentityError:
                await asyncio.to_thread(self.store.clear)
        return False

    async def request(self, method, path, *, be=False, **kwargs):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with httpx.AsyncClient(base_url=self.be_url if be else self.agent_url,
                                         timeout=10, follow_redirects=False) as client:
                result = await client.request(method, path, headers=headers, **kwargs)
        except httpx.RequestError:
            raise IdentityError(503, "SERVICE_UNAVAILABLE", "无法连接服务，请检查 BE 和 Agent API 是否启动。") from None
        if result.status_code >= 400:
            code = "REQUEST_FAILED"
            try:
                error = result.json().get("error", {})
                if isinstance(error, dict):
                    code = error.get("code", code)
            except (ValueError, AttributeError):
                pass
            if result.status_code == 401 or code in {"AUTH_CHANGED", "AUTH_REQUIRED"}:
                self.session_id = None
                self.token = None
                await asyncio.to_thread(self.store.clear)
            raise IdentityError(result.status_code, code, "请求未完成；登录失效时请使用 /login，其他情况请稍后重试。")
        return result

    async def login(self):
        if self.token:
            await self.logout()
        grant = None
        delivered = False
        completed = False
        try:
            response = await self.request("POST", "/api/v1/auth/cli/authorize", be=True)
            grant = response.json()
            uri = safe_url(grant["verification_uri"])
            device = grant["device_code"]
            code = grant["user_code"]
            if not isinstance(device, str) or not isinstance(code, str) or not code.isalnum() or len(code) > 20:
                raise ValueError("invalid device grant")
            interval = max(5, min(60, int(grant["interval"])))
            deadline = monotonic() + min(600, max(1, int(grant["expires_in"])))
            print(f"请在浏览器打开 {uri}，输入授权码 {code} 并确认当前账号。")
            await asyncio.to_thread(webbrowser.open, uri)
            while monotonic() < deadline:
                await asyncio.sleep(interval)
                async with httpx.AsyncClient(base_url=self.be_url, timeout=10, follow_redirects=False) as client:
                    result = await client.post("/api/v1/auth/cli/token", json={"device_code": device})
                data = result.json()
                if result.status_code == 200:
                    token = data["access_token"]
                    delivered = True
                    self.token = token
                    await self.auth.authenticate(token)
                    saved = await asyncio.to_thread(self.store.save, token)
                    completed = True
                    print("登录成功，凭据已保存到系统凭据库。" if saved else "登录成功；系统凭据库不可用，仅在本次进程中保存登录。")
                    return
                error = data.get("error")
                if error == "authorization_pending":
                    continue
                if error == "slow_down":
                    interval = min(60, interval + 5)
                    continue
                raise IdentityError(401, "AUTH_REQUIRED", "浏览器授权已拒绝或过期，请重新 /login。")
            raise IdentityError(401, "AUTH_REQUIRED", "浏览器授权超时，请重新 /login。")
        finally:
            if delivered and not completed:
                try:
                    await self.logout()
                except Exception:
                    pass
            if grant and not delivered:
                try:
                    await self.request("POST", "/api/v1/auth/cli/cancel", be=True,
                                       json={"device_code": grant["device_code"]})
                except Exception:
                    pass

    async def new_session(self):
        if self.session_id:
            try:
                await self.request("DELETE", f"/agent/api/v1/sessions/{self.session_id}")
            finally:
                self.session_id = None

    async def logout(self):
        try:
            try:
                await self.new_session()
            except Exception:
                pass
            if self.token:
                await self.request("POST", "/api/v1/auth/logout", be=True)
        finally:
            self.session_id = None
            self.token = None
            await asyncio.to_thread(self.store.clear)

    async def query(self, question, message_id, progress):
        if not self.token:
            raise IdentityError()
        if not self.session_id:
            response = await self.request("POST", "/agent/api/v1/sessions")
            self.session_id = response.json()["session_id"]
        try:
            async with httpx.AsyncClient(base_url=self.agent_url, timeout=150, follow_redirects=False) as client:
                async with client.stream("POST", f"/agent/api/v1/sessions/{self.session_id}/messages",
                    headers={"Authorization": f"Bearer {self.token}"},
                    json={"message_id": message_id, "message": question}) as response:
                    if response.status_code != 200:
                        await response.aread()
                        try:
                            code = response.json().get("error", {}).get("code", "REQUEST_FAILED")
                        except (ValueError, AttributeError):
                            code = "REQUEST_FAILED"
                        if response.status_code in (401, 403):
                            code = "AUTH_REQUIRED"
                        if response.status_code == 404:
                            self.session_id = None
                        raise IdentityError(response.status_code, code, "助手请求失败，请检查登录状态或使用 /new 重试。")
                    name, chunks = "", []
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            name = line[6:].strip()
                        elif line.startswith("data:"):
                            chunks.append(line[5:].lstrip())
                        elif not line and chunks:
                            data = json.loads("\n".join(chunks))
                            chunks = []
                            if name == "progress":
                                progress(data["message"])
                            elif name == "error":
                                raise IdentityError(409 if data["code"] == "AUTH_CHANGED" else 503, data["code"], data["message"])
                            elif name == "result":
                                if data.get("message_id") != message_id or not isinstance(data.get("answer"), str):
                                    raise ValueError("invalid answer")
                                return data
            raise IdentityError(503, "INCOMPLETE_RESPONSE", "连接已中断，未收到完整回答。")
        except IdentityError as exc:
            if exc.code in {"AUTH_REQUIRED", "AUTH_CHANGED", "ACCOUNT_DISABLED"}:
                self.session_id = None
                if exc.code != "AUTH_CHANGED":
                    self.token = None
                    await asyncio.to_thread(self.store.clear)
            raise
