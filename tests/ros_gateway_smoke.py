"""Ensayo DDS del gateway y puente V5 con actuadores simulados, nunca motores."""
import asyncio
import os
import signal
import subprocess
import tempfile
import time
from maxcim_api.bridge import RosBridge

async def main():
    log=tempfile.TemporaryFile(mode="w+")
    child=subprocess.Popen(["ros2","run","maxcim_control","gateway"],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    bridge=None
    try:
        bridge=RosBridge()
        deadline=time.monotonic()+10
        while not bridge.state()["connected"] or not bridge.client.service_is_ready():
            if child.poll() is not None or time.monotonic()>=deadline:
                raise AssertionError("Gateway DDS no disponible")
            await asyncio.sleep(.05)
        assert bridge.state()["mode"]=="ros2-simulation"
        await bridge.execute("panel-one","acquire",{})
        try:
            await bridge.execute("panel-two","acquire",{})
            raise AssertionError("Dos operadores aceptados")
        except ValueError:
            pass
        state=await bridge.execute("panel-one","drive",{"seq":1,"linear":.1,"angular":0})
        assert state["linear"]==.1
        await bridge.execute("panel-one","brake",{"seq":3})
        try:
            await bridge.execute("panel-one","drive",{"seq":2,"linear":.1,"angular":0})
            raise AssertionError("Orden antigua aceptada")
        except ValueError:
            pass
        await bridge.execute("panel-one","drive",{"seq":4,"linear":.1,"angular":0})
        await asyncio.sleep(.65)
        assert bridge.state()["linear"]==0
        await bridge.execute("observer","estop",{})
        state=await bridge.execute("engineer","reset",{})
        assert not state["estop"] and state["owner"] is None
        print("PASS: V5 DDS, interfaz generada, modo ROS-simulation, exclusión, brake secuenciado, watchdog y rearme")
    except BaseException:
        log.flush();log.seek(0);print(log.read());raise
    finally:
        if bridge:bridge.close()
        if child.poll() is None:
            os.killpg(child.pid,signal.SIGINT)
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=3)
        log.close()

if __name__=="__main__":asyncio.run(main())
