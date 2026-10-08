import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'control_web'))
sys.path.insert(0,str(ROOT/'studio'))
from core import Controller
spec=importlib.util.spec_from_file_location('base_web',ROOT/'control_web/server.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
spec=importlib.util.spec_from_file_location('studio_web',ROOT/'studio/server.py')
studio=importlib.util.module_from_spec(spec);spec.loader.exec_module(studio)
TOKEN='test-only-token-for-studio-12345'

class Gateway(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[]
        self.control=Controller()
        # Bind test servers to known ports; Host validation remains active.
        self.base=TestServer(base.create_app(self.control,TOKEN,hosts={'127.0.0.1:18980'},demo=True),port=18980)
        await self.base.start_server()
        arm=web.Application()
        async def status(request):
            return web.json_response({'serial_connected':True,'servos':{},'sequence_running':False})
        async def post(request):
            self.calls.append((request.path,await request.json()))
            return web.json_response({'status':'started','message':'test'})
        arm.router.add_get('/api/status',status);arm.router.add_post('/{path:.*}',post)
        self.arms=TestServer(arm,port=18981);await self.arms.start_server()
        self.client=TestClient(TestServer(studio.create_app(TOKEN,{'127.0.0.1:18988'},base_url='http://127.0.0.1:18980',arms_url='http://127.0.0.1:18981'),port=18988))
        await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close();await asyncio.sleep(.15);await self.base.close();await self.arms.close()
    async def receive(self,ws,kind):
        for _ in range(40):
            m=await ws.receive_json(timeout=2)
            if m['type']==kind:return m
        self.fail('No message '+kind)
    async def connect(self,token=TOKEN):
        ws=await self.client.ws_connect('/ws',origin='http://127.0.0.1:18988')
        await ws.send_json({'type':'auth','token':token});return ws
    async def test_auth_and_exclusive(self):
        ws=await self.connect('wrong');self.assertEqual((await self.receive(ws,'error'))['message'],'Código de acceso incorrecto');await ws.close()
        ws=await self.connect();await self.receive(ws,'connected')
        second=await self.connect();self.assertIn('ocupado',(await self.receive(second,'error'))['message']);await second.close();await ws.close()
    async def test_arm_replay_stale_and_stop(self):
        ws=await self.connect();await self.receive(ws,'connected');nonce=(await self.receive(ws,'arm_challenge'))['challenge']
        msg={'type':'arm','seq':1,'challenge':nonce,'action':'quick','name':'SALUDOMILITAR'}
        await ws.send_json(msg);await self.receive(ws,'arm_result')
        await ws.send_json(msg);await self.receive(ws,'arm_error')
        self.assertEqual(sum(p=='/api/quick_actions/run' for p,_ in self.calls),1)
        await ws.send_json({'type':'arm','seq':2,'challenge':'expired','action':'home'});await self.receive(ws,'arm_error')
        await ws.send_json({'type':'stop_all','request':1});await self.receive(ws,'notice')
        self.assertTrue(any(p=='/studio/stop' for p,_ in self.calls))
        await ws.close()
    async def test_base_deadman_and_disconnect(self):
        ws=await self.connect();await self.receive(ws,'connected');state=await self.receive(ws,'state')
        await ws.send_json({'type':'drive','seq':1,'challenge':state['challenge'],'linear':.05,'angular':.25})
        await asyncio.sleep(.06);self.assertEqual(self.control.target,(.05,.25))
        await ws.close();await asyncio.sleep(.08);self.assertEqual(self.control.target,(0.,0.))
    async def test_origin_and_static(self):
        response=await self.client.get('/');self.assertEqual(response.status,200)
        response=await self.client.get('/server.py');self.assertEqual(response.status,404)
        with self.assertRaises(Exception):await self.client.ws_connect('/ws',origin='https://elsewhere.test')

if __name__=='__main__':unittest.main()
