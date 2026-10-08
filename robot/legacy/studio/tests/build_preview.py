"""Build an isolated visual preview, with no robot or network access.

python studio/tests/build_preview.py /tmp/max-studio-preview.html
The output runs from file:// and includes a visible simulated-command log.
"""
import json
from pathlib import Path
import sys

STATIC = Path(__file__).resolve().parents[1] / 'static'
FIXTURES = Path(__file__).parent / 'fixtures'
SERVOS = {0:('Hombro izquierdo',0,145,135),1:('Codo izquierdo',0,200,110),2:('Elevación izquierda',0,75,75),4:('Hombro derecho',125,270,135),5:('Codo derecho',0,150,50),6:('Elevación derecha',0,270,0)}
ARMS = {'available':True,'serial_connected':True,'sequence_running':False,
        'quick_actions':json.loads((FIXTURES/'acciones_rapidas.json').read_text()),
        'steps':json.loads((FIXTURES/'pasos_individuales.json').read_text()),
        'movements':{'SECUENCIA_DE_PRUEBA':{'sequence':['B1-EXT','B2-EXT'],'mode':'parallel'}},
        'servos':{str(k):dict(name=v[0],min=v[1],max=v[2],current=v[3]) for k,v in SERVOS.items()}}
MOCK = r'''
// This socket is in-memory only. CSP also forbids every network connection.
window.WebSocket=class FakeSocket {
 static OPEN=1;readyState=1;bufferedAmount=0;count=0;drives=0;
 constructor(){setTimeout(()=>this.onopen?.(),30);}
 emit(data){this.onmessage?.({data:JSON.stringify(data)});}
 send(raw){const m=JSON.parse(raw);
 if(m.type==='auth'){
   this.emit({type:'connected',demo:true});this.emit({type:'arms_state',data:ARMS});
   this.emit({type:'jetson_state',data:{available:true,load:.45,cores:6,ram_used_gb:2.3,ram_total_gb:7.37,temperature_c:42,disk_free_gb:379,camera_usb:true,microphone_usb:true,processes:{camera:false,voice:false}}});
   this.timer=setInterval(()=>{
     this.emit({type:'state',ready:true,scan_ok:true,odom_ok:true,linear:0,challenge:'base-'+(++this.count)});
     this.emit({type:'arm_challenge',challenge:'arm-'+this.count});
   },80);
 }else if(m.type==='stop'||m.type==='stop_all')this.emit({type:'stopped',request:m.request});
 else if(m.type==='arm')this.emit({type:'arm_result',message:'Simulación: movimiento recibido. Ningún robot conectado.'});
 if(['drive','arm','stop','stop_all'].includes(m.type)){
   if(m.type==='drive')document.getElementById('qaDriveCount').textContent=++this.drives;
   document.getElementById('qaLastCommand').textContent=JSON.stringify(Object.fromEntries(Object.entries(m).filter(([k])=>!['challenge','request','seq'].includes(k))));
   const log=document.getElementById('qaLog');const row=document.createElement('li');row.textContent=document.getElementById('qaLastCommand').textContent;log.prepend(row);while(log.children.length>40)log.lastChild.remove();
 }
 }
 close(){clearInterval(this.timer);this.readyState=3;this.onclose?.();}
};
window.addEventListener('DOMContentLoaded',()=>{
 document.getElementById('address').value='https://simulation.invalid';document.getElementById('token').value='preview-only';
 document.getElementById('connectForm').requestSubmit();
});
'''.replace('ARMS',json.dumps(ARMS,ensure_ascii=False))

def build(output):
    html=(STATIC/'index.html').read_text()
    html=html.replace('<meta charset="utf-8">','<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data:; connect-src \'none\'; form-action \'none\'">')
    html=html.replace('<link rel="icon" href="favicon.svg">','<link rel="icon" href="data:,">')
    html=html.replace('<link rel="stylesheet" href="style.css">','<style>'+(STATIC/'style.css').read_text()+'</style>')
    for name in ('app.js','studio.js','live.js'):
        html=html.replace(f'<script src="{name}" defer></script>','')
    qa='''<details style="margin:0 20px 150px calc(var(--sidebar) + 20px);font:11px monospace"><summary>Registro de prueba · solo simulación</summary><p>Órdenes de base: <output id="qaDriveCount">0</output></p><p id="qaLastCommand">Sin órdenes</p><ol id="qaLog"></ol></details>'''
    scripts=MOCK+'\n'+(STATIC/'app.js').read_text()+'\n'+(STATIC/'studio.js').read_text()+'\n'+(STATIC/'live.js').read_text()
    html=html.replace('</body>',qa+'<script>'+scripts+'</script></body>')
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(html)

if __name__=='__main__':
    output=Path(sys.argv[1]);build(output);print(output)
