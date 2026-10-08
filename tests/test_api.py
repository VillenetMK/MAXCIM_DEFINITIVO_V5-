import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from aiohttp.test_utils import TestClient, TestServer
from maxcim_api.server import create_app, STORE, BRIDGE, CLIENTS

class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(database=str(Path(self.tmp.name)/"team.db"), mode="simulation")
        self.app[STORE].create_user("engineer","test-password-123","engineer")
        self.app[STORE].create_user("viewer","test-password-123","observer")
        self.client=TestClient(TestServer(self.app));await self.client.start_server()
        self.headers={"X-Control-Client":"one-panel-123456789"}

    async def asyncTearDown(self):
        await self.client.close();self.tmp.cleanup()

    async def login(self,name="engineer"):
        response=await self.client.post("/api/login",json={"username":name,"password":"test-password-123"})
        self.assertEqual(response.status,200)
        self.headers["X-CSRF-Token"]=(await response.json())["csrf"]

    async def command(self,name,payload=None,headers=None):
        return await self.client.post("/api/robot/command",json={"command":name,"payload":payload or {}},headers=headers or self.headers)

    async def test_index_and_assets_are_served(self):
        response=await self.client.get("/");self.assertEqual(response.status,200)
        self.assertIn("MAXCIM",await response.text())
        self.assertIn("default-src 'self'",response.headers["Content-Security-Policy"])
        self.assertEqual((await self.client.get("/assets/app.js")).status,200)

    async def test_anonymous_cannot_read_or_command(self):
        self.assertEqual((await self.client.get("/api/tasks")).status,401)
        self.assertEqual((await self.command("acquire")).status,401)

    async def test_login_cookie_has_protection_and_no_password_logged(self):
        await self.login()
        cookie=self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))["maxcim_session"]
        self.assertTrue(cookie.value)
        rows=self.app[STORE].audit_rows()
        self.assertNotIn("test-password",json.dumps(rows))

    async def test_csrf_and_external_origin_rejected(self):
        await self.login()
        self.assertEqual((await self.command("acquire",headers={"X-Control-Client":"one-panel-123456789"})).status,403)
        self.assertEqual((await self.command("acquire",headers={**self.headers,"Origin":"https://foreign.example"})).status,403)

    async def test_observer_cannot_operate_but_can_stop(self):
        await self.login("viewer")
        self.assertEqual((await self.command("acquire")).status,403)
        self.assertEqual((await self.command("estop")).status,200)
        self.assertTrue(self.app[BRIDGE].state()["estop"])
        self.assertEqual((await self.command("reset")).status,403)

    async def test_two_tabs_in_same_session_cannot_share_control(self):
        await self.login();self.assertEqual((await self.command("acquire")).status,200)
        other={**self.headers,"X-Control-Client":"other-panel-123456789"}
        self.assertEqual((await self.command("acquire",headers=other)).status,409)
        self.assertEqual((await self.command("drive",{"seq":1,"linear":.1,"angular":0},headers=other)).status,409)

    async def test_drive_brake_reordered_command(self):
        await self.login();await self.command("acquire")
        self.assertEqual((await self.command("drive",{"seq":1,"linear":.1,"angular":0})).status,200)
        await self.command("brake",{"seq":3})
        self.assertEqual((await self.command("drive",{"seq":2,"linear":.1,"angular":0})).status,409)
        self.assertEqual(self.app[BRIDGE].state()["linear"],0)

    async def test_lost_client_watchdog_stops(self):
        await self.login();await self.command("acquire")
        await self.command("drive",{"seq":1,"linear":.1,"angular":0})
        await asyncio.sleep(.45)
        self.assertEqual(self.app[BRIDGE].state()["linear"],0)

    async def test_stalled_websocket_close_does_not_delay_watchdog(self):
        await self.login();await self.command("acquire")
        await self.command("drive",{"seq":1,"linear":.1,"angular":0})
        cookie=self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))["maxcim_session"].value
        token=self.app[STORE].session(cookie)["token"]
        closing, unblock = asyncio.Event(), asyncio.Event()
        class StalledSocket:
            async def send_json(self, packet):
                raise ConnectionError("Disconnected peer")
            async def close(self, **kwargs):
                closing.set()
                await unblock.wait()
        peer=(StalledSocket(),token,token+":one-panel-123456789")
        self.app[CLIENTS].add(peer)
        try:
            await asyncio.wait_for(closing.wait(),1)
            await asyncio.sleep(.45)
            self.assertEqual(self.app[BRIDGE].state()["linear"],0)
            ws=await self.client.ws_connect("/api/events?client=one-panel-123456789")
            packet=await ws.receive_json(timeout=1)
            self.assertEqual(packet["data"]["linear"],0)
            await ws.close()
        finally:
            unblock.set()
            self.app[CLIENTS].discard(peer)

    async def test_task_optimistic_lock_and_notes_persist(self):
        await self.login()
        created=await self.client.post("/api/tasks",json={"title":"Calibrar base","owner":"Gabriel"},headers=self.headers)
        task=(await created.json())["id"]
        path=f"/api/tasks/{task}"
        self.assertEqual((await self.client.patch(path,json={"version":1,"state":"working"},headers=self.headers)).status,200)
        self.assertEqual((await self.client.patch(path,json={"version":1,"state":"done"},headers=self.headers)).status,409)
        await self.client.post("/api/notes",json={"body":"Primera prueba pendiente"},headers=self.headers)
        self.assertEqual((await (await self.client.get("/api/notes")).json())[0]["body"],"Primera prueba pendiente")

    async def test_websocket_state_is_filtered_and_owned(self):
        await self.login();await self.command("acquire")
        ws=await self.client.ws_connect("/api/events?client=one-panel-123456789")
        packet=await ws.receive_json(timeout=2)
        self.assertTrue(packet["data"]["owns_control"])
        self.assertNotIn("owner",packet["data"]);self.assertEqual(packet["data"]["mode"],"simulation")
        await ws.close()

    async def test_logout_succeeds_with_gateway_offline(self):
        await self.login()
        self.app[BRIDGE].execute=AsyncMock(side_effect=ValueError("Offline"))
        self.assertEqual((await self.client.post("/api/logout",json={},headers=self.headers)).status,200)
        self.assertEqual((await self.client.get("/api/me")).status,401)

    async def test_session_revocation_closes_websocket(self):
        await self.login()
        ws=await self.client.ws_connect("/api/events?client=one-panel-123456789")
        await ws.receive_json(timeout=2)
        self.app[STORE].db.execute("DELETE FROM sessions");self.app[STORE].db.commit()
        await ws.receive(timeout=2);self.assertTrue(ws.closed)

    async def test_observer_cannot_create_tasks_or_users(self):
        await self.login("viewer")
        for path,data in (("/api/tasks",{"title":"X"}),("/api/users",{"username":"X","role":"admin","password":"012345678901"})):
            self.assertEqual((await self.client.post(path,json=data,headers=self.headers)).status,403)

    async def test_malformed_and_oversized_commands_rejected(self):
        await self.login()
        self.assertEqual((await self.client.post("/api/robot/command",json=["drive"],headers=self.headers)).status,400)
        self.assertEqual((await self.command("execute_shell",{"code":"bad"})).status,400)
        self.assertEqual((await self.command("arm",{"action":"X"*17000})).status,413)
