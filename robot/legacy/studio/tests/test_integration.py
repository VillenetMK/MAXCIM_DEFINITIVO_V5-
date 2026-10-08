import asyncio
from collections import deque
from concurrent.futures import Future
import json
from pathlib import Path
import struct
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from http.server import ThreadingHTTPServer
from aiohttp import ClientSession

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jetson_hub import Hub
from jetson_agent import handler_class
from wire import encode, exchange
from navigation import Navigation, bounded
from integration import Integration

KEY='test-integration-key-not-a-real-secret-12345'


class VolatileBridge(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub=Hub()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_class(KEY,NS(snapshot=lambda:{}),self.hub))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_address[1])
        self.client=ClientSession()
    async def asyncTearDown(self):
        await self.client.close();await asyncio.to_thread(self.server.shutdown);self.server.server_close();self.thread.join(1)
    async def test_signed_roundtrip_replay_and_path_binding(self):
        request={'owner':'a'*32,'voice':True,'boot':'','after':0}
        result=await exchange(self.client,{'url':self.url,'key':KEY},request)
        self.assertEqual(result['events'],[])
        body,nonce,headers=encode(KEY,'/v2/intent',{'kind':'move','direction':'adelante','amount':.25})
        async with self.client.post(self.url+'/v2/intent',data=body,headers=headers) as r:
            self.assertEqual(r.status,200);self.assertTrue((await r.json())['id'])
        async with self.client.post(self.url+'/v2/intent',data=body,headers=headers) as r:self.assertEqual(r.status,403)
        async with self.client.post(self.url+'/v2/result',data=body,headers=headers) as r:self.assertEqual(r.status,403)
    async def test_boot_expiry_and_session_exclusivity(self):
        request={'owner':'a'*32,'voice':True,'boot':'','after':0}
        first=self.hub.poll(request)
        self.hub.submit({'kind':'move','direction':'adelante','amount':.2})
        self.assertEqual(self.hub.poll(request)['events'],[])
        request['boot']=first['boot']
        self.assertEqual(len(self.hub.poll(request)['events']),1)
        self.hub.events[0]['at']-=4
        self.assertEqual(self.hub.poll(request)['events'],[])
        with self.assertRaises(ValueError):self.hub.poll(dict(request,owner='b'*32))
        self.hub.enabled_until=0
        self.assertFalse(self.hub.submit({'kind':'arm','action':'saludar'})['accepted'])
    async def test_no_media_without_subscription_and_old_frames_expire(self):
        now=time.monotonic();self.hub.image=(now,b'jpeg')
        self.hub.audio.extend([(1,now,b'12'),(2,now,b'34')]);self.hub.audio_seq=2
        req={'owner':'a'*32,'boot':self.hub.boot,'after':0,'audio_after':0}
        self.assertNotIn('jpeg',self.hub.poll(req))
        active=self.hub.poll(dict(req,video=True,audio=True))
        self.assertIn('jpeg',active);self.assertIn('pcm',active)
        self.hub.image=(now-2,b'old');self.hub.audio=deque([(3,now-2,b'old')],maxlen=8)
        active=self.hub.poll(dict(req,video=True,audio=True))
        self.assertNotIn('jpeg',active);self.assertNotIn('pcm',active)


class Projection(unittest.TestCase):
    def test_requires_real_calibration_depth_and_unambiguous_person(self):
        h=Hub()
        with self.assertRaises(ValueError):h.resolve({'kind':'person','target':'Gabriel'})
        h.geometry={'verified':True,'intrinsics_verified':True,'optical_to_base':[0,0,1,0,-1,0,0,0,0,-1,0,0,0,0,0,1]}
        self.assertTrue(h.calibrated())
        header=NS(frame_id='camera_frame')
        h.info=NS(width=20,height=20,k=[10,0,10,0,10,10,0,0,1],header=header)
        h.depth=(time.monotonic(),NS(encoding='16UC1',width=20,height=20,header=header,
            data=struct.pack('<H',2000)*400,step=40,is_bigendian=False))
        point=h.resolve({'kind':'object','u':.5,'v':.5})
        self.assertEqual(point,[2.,0.,0.])
        with self.assertRaises(ValueError):h.resolve({'kind':'person','target':''})
        face={'reconocido':True,'nombre':'Gabriel','nombre_pila':'Gabriel','bbox':[8,8,12,12],'liveness':True}
        h.faces=(time.monotonic(),{'rostros':[face,face]})
        with self.assertRaises(ValueError):h.resolve({'kind':'person','target':'Gabriel'})
        h.depth=(time.monotonic()-2,h.depth[1])
        with self.assertRaises(ValueError):h.resolve({'kind':'object','u':.5,'v':.5})


class NavigationGate(unittest.TestCase):
    def nav(self):
        n=Navigation.__new__(Navigation);n.lock=threading.RLock();n.profile={'verified':True,'robot_radius':.35}
        n.pose=(0.,0.,0.);n.pose_at=n.scan_at=time.monotonic()
        n.scan=NS(ranges=[3.]*360,range_min=.1,range_max=10.,angle_increment=2*3.141592653589793/360)
        n.task=n.handle=None;n.generation=0;n.velocity=(0.,0.);n.last=''
        return n
    def test_obstacle_loss_and_dimensions_stop_motion(self):
        n=self.nav();n.start({'kind':'move','direction':'adelante','amount':.25})
        self.assertGreater(n.output()[0],0)
        n.scan.ranges[0]=.4
        self.assertEqual(n.output(),(0.,0.));self.assertIsNone(n.task)
        n.scan.ranges[0]=3.;n.pose_at-=2
        with self.assertRaises(ValueError):n.start({'kind':'move','direction':'adelante','amount':.25})
        n.pose_at=time.monotonic();n.profile['verified']=False
        with self.assertRaises(ValueError):n.start({'kind':'move','direction':'adelante','amount':.25})
    def test_distance_completion_limits_and_wrong_direction(self):
        n=self.nav()
        for value in (float('nan'),float('inf'),True,-1,2):
            with self.assertRaises(ValueError):bounded(value,.05,1)
        with self.assertRaises(ValueError):n.start({'kind':'move','direction':'izquierda','amount':.2})
        n.start({'kind':'move','direction':'adelante','amount':.25});n.pose=(.24,0.,0.)
        self.assertEqual(n.output(),(0.,0.));self.assertIsNone(n.task)
    def test_new_map_publisher_invalidates_destinations(self):
        n=self.nav();n.grid=object();n.map_at=time.monotonic();n.map_publisher=b'old';n.points={'mesa':(1,0,0)}
        msg=NS(header=NS(frame_id='map'),info=NS(width=10,height=10))
        n.on_map(msg,NS(publisher_gid=b'new'))
        self.assertEqual(n.points,{})
        self.assertEqual(n.map_publisher,b'new')

    def test_cancel_during_goal_acceptance(self):
        n=self.nav();n.points={'mesa':(2.,0.,0.)};n.map_pose=lambda:(0.,0.,0.);n.goal_clear=lambda x,y:None
        f=Future();n.client=NS(server_is_ready=lambda:True,send_goal_async=lambda goal:f)
        n.node=NS(get_clock=lambda:NS(now=lambda:NS(to_msg=lambda:None)))
        n.Goal=lambda:NS(pose=NS(header=NS(),pose=NS(position=NS(),orientation=NS())))
        n.start({'kind':'goto','target':'mesa'});n.cancel()
        calls=[];f.set_result(NS(accepted=True,cancel_goal_async=lambda:calls.append('cancel')))
        self.assertEqual(calls,['cancel']);self.assertIsNone(n.task)


class SessionPriority(unittest.IsolatedAsyncioTestCase):
    async def test_manual_override_and_expired_voice(self):
        sent=[]; cancelled=[]
        async def send(data):sent.append(data)
        async def arm(*args):return {}
        async def stop():pass
        nav=NS(task=True,cancel=lambda *args:cancelled.append(True))
        s=Integration(NS(send_json=send),NS(send_json=send),None,None,nav,arm,stop,'a'*32)
        s.voice=True
        await s.manual({'type':'drive','seq':1,'challenge':'x','linear':.05,'angular':0})
        self.assertFalse(s.voice);self.assertTrue(cancelled)
        with self.assertRaises(ValueError):await s.manual({'type':'drive','seq':1})
        await s.event({'id':'old','kind':'move','age':3})
        self.assertFalse(s.results[-1]['accepted'])
        s.closed=True
        await s.event({'id':'late','kind':'arm','age':0,'action':'saludar'})
        self.assertFalse(s.results[-1]['accepted'])


if __name__=='__main__':unittest.main()
