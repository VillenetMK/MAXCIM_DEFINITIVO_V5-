"""A session owns manual control, voice actions and bounded live media together."""
import asyncio
import contextlib
import time
from wire import exchange


class Integration:
    def __init__(self, ws, upstream, client, config, navigation, arm_post, stop_arms, owner, arms_url='http://127.0.0.1:5000'):
        self.ws,self.upstream,self.client,self.config = ws,upstream,client,config
        self.nav,self.arm_post,self.stop_arms,self.owner = navigation,arm_post,stop_arms,owner
        self.arms_url=arms_url
        self.voice = self.video = self.audio = False
        self.seq,self.audio_seq,self.drive_seq,self.browser_seq = 0,0,0,-1
        self.boot = ''
        self.challenge,self.challenge_at = '',0
        self.last_link = 0
        self.results = []
        self.closed = False
        self.last_status = 0
        self.command_lock = asyncio.Lock()

    def state(self):
        result = self.nav.status() if self.nav else {'base_blocked':'Navegación no disponible','points':[]}
        return dict(result, voice=self.voice, video=self.video, audio=self.audio,
                    jetson_link=time.monotonic()-self.last_link < 1)

    async def emit(self, data):
        await asyncio.wait_for(self.ws.send_json(data), .3)

    async def halt(self, disable=False):
        if disable:self.voice=False
        if self.nav:self.nav.cancel()
        await self.upstream.send_json({'type':'stop'})
        self.challenge = ''
        await self.stop_arms()

    async def manual(self, data):
        async with self.command_lock:
            kind = data.get('type')
            if kind=='drive':
                seq = data.get('seq')
                if type(seq) is not int or seq <= self.browser_seq:
                    raise ValueError('Orden manual repetida')
                self.browser_seq=seq
            if self.voice or (self.nav and self.nav.task):
                self.voice=False
                if self.nav:self.nav.cancel('Control manual')
                await self.stop_arms()
            if kind=='drive':
                self.drive_seq+=1
                data=dict(data,seq=self.drive_seq)
            await self.upstream.send_json(data)

    async def event(self, event):
        result={'id':event['id'],'state':'rejected','accepted':False}
        try:
            if self.closed:raise ValueError('Sesión cerrada')
            if event.get('age',999)>.8:
                raise ValueError('Orden vencida; no se ejecutó')
            kind=event.get('kind')
            if kind=='stop':
                await self.halt(disable=True)
                result.update(state='stopped',accepted=True,message='Base detenida y cola de brazos cancelada')
            elif not self.voice:
                raise ValueError('El control por voz está desactivado')
            elif kind=='arm':
                if self.nav and self.nav.task:
                    raise ValueError('Detén la base antes de mover los brazos')
                await self.arm_post('/studio/lease')
                answer=await self.arm_post('/studio/voice',{'action':event.get('action')})
                result.update(state='started',accepted=True,message=answer.get('message'))
            else:
                if not self.nav:raise ValueError('Navegación no disponible')
                async with self.client.get(self.arms_url+'/api/status') as response:
                    arms=await response.json()
                if arms.get('sequence_running'):
                    raise ValueError('Espera a que termine el movimiento de brazos')
                message=self.nav.start(event)
                result.update(state='started',accepted=True,message=message)
        except Exception as error:
            result['error']=str(error) if isinstance(error,ValueError) else 'No se pudo ejecutar la orden'
        self.results.append(result)
        await self.emit({'type':'voice_result','data':result})

    async def poll(self):
        while not self.ws.closed:
            began=time.monotonic()
            try:
                data=await asyncio.wait_for(exchange(self.client,self.config,{
                    'owner':self.owner,'boot':self.boot,'after':self.seq,'voice':self.voice,
                    'video':self.video,'audio':self.audio,'audio_after':self.audio_seq,
                    'runtime':self.state(),'results':self.results[-32:]}),timeout=.7)
                if data.get('error'):raise ValueError(data['error'])
                self.results.clear()
                self.last_link=time.monotonic()
                self.boot,self.seq,self.audio_seq=data['boot'],data['seq'],data['audio_seq']
                async with self.command_lock:
                    for event in data.get('events',[])[:16]:
                        # Account for transport time as well as the event's age at the source.
                        event['age']+=time.monotonic()-began
                        await self.event(event)
                media={key:data[key] for key in ('jpeg','pcm') if key in data}
                if media:await self.emit({'type':'live_media','data':media})
                self.calibrated=data.get('camera_calibrated',False)
            except Exception:
                if self.voice or (self.nav and self.nav.task):
                    await self.halt(disable=True)
            await asyncio.sleep(max(.02,.2-(time.monotonic()-began)))

    async def tick(self):
        while not self.ws.closed:
            if self.voice and self.nav:
                if time.monotonic()-self.last_link>1:
                    await self.halt(disable=True)
                else:
                    output=self.nav.output()
                    if output is not None and self.challenge and time.monotonic()-self.challenge_at<.25:
                        async with self.command_lock:
                            if not self.voice or self.closed or not self.nav.task and output!=(0.,0.):
                                continue
                            self.drive_seq+=1
                            await self.upstream.send_json({'type':'drive','seq':self.drive_seq,
                                'challenge':self.challenge,'linear':output[0],'angular':output[1]})
                            self.challenge=''
            if time.monotonic()-self.last_status>.5:
                await self.emit({'type':'integration_state','data':dict(self.state(),camera_calibrated=getattr(self,'calibrated',False))})
                self.last_status=time.monotonic()
            await asyncio.sleep(.08)

    async def close(self):
        self.closed=True
        if self.nav:self.nav.cancel('Sesión cerrada')
        self.voice=self.video=self.audio=False
        # Release the Jetson lease promptly; on failure its one-second expiry applies.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(exchange(self.client,self.config,{'owner':self.owner,
                'boot':self.boot,'after':self.seq,'voice':False,'results':self.results[-32:]}),.4)
