/* DOM interaction checks (no renderer and no hardware).
   NODE_PATH=/path/to/node_modules node studio/tests/check_ui.cjs preview.html */
const {JSDOM, VirtualConsole}=require('jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const errors=[];
const consoleBridge=new VirtualConsole();
consoleBridge.on('jsdomError', e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(process.argv[2],'utf8'), {
  url:process.argv[3] || 'http://simulation.invalid/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:consoleBridge,
  beforeParse(w){
    w.document.hasFocus=()=>true;w.scrollTo=()=>{};
    w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
    w.HTMLDialogElement.prototype.close=function(){this.open=false;};
    w.HTMLCanvasElement.prototype.getContext=()=>({fillRect(){},fillText(){}});
  }
});
const w=dom.window,d=w.document,delay=ms=>new Promise(r=>setTimeout(r,ms));
const click=s=>{const b=d.querySelector(s);assert(b,s);b.click();};
const key=(type,k)=>d.dispatchEvent(new w.KeyboardEvent(type,{key:k,code:'Key'+k.toUpperCase(),bubbles:true}));
const count=()=>Number(d.getElementById('qaDriveCount').textContent);
const log=()=>[...d.querySelectorAll('#qaLog li')].map(el=>JSON.parse(el.textContent));
(async()=>{try{
 await delay(200);
 assert.equal(errors.length,0,errors.join('\n'));
 assert.match(d.getElementById('status').textContent,/Listo/);
 assert.equal(d.querySelectorAll('.joint').length,6);
 assert.equal(d.querySelectorAll('.action-item').length,11);
 key('keydown','w');key('keydown','a');await delay(180);key('keyup','a');key('keyup','w');await delay(80);
 assert(log().some(m=>m.type==='drive' && m.linear===.05 && m.angular===.25),'W+A still uses configured velocities');
 assert.equal(log()[0].type,'stop','release stops');
 key('keydown','w');await delay(110);click('[data-view="brazos"]');
 assert.equal(log()[0].type,'stop','navigation stops base');
 const before=count();key('keydown','w');await delay(170);key('keyup','w');assert.equal(count(),before,'hidden driving view cannot move');
 assert(d.getElementById('cabina').hidden);assert(!d.getElementById('brazos').hidden);
 assert.equal(d.querySelector('[data-view="brazos"]').getAttribute('aria-current'),'page');
 await delay(90);click('.joint-controls button');assert(log().some(m=>m.type==='arm' && m.action==='joint'),'joint control');
 assert(!d.getElementById('armFeedback').hidden,'visible cross-view feedback');
 click('[data-view="biblioteca"]');
 const search=d.getElementById('actionSearch');search.value='militar';search.dispatchEvent(new w.Event('input'));assert.equal(d.querySelectorAll('.action-item').length,2,'search');
 search.value='';search.dispatchEvent(new w.Event('input'));
 const favorite=d.querySelector('.favorite'),id=favorite.dataset.favoriteId;favorite.click();
 assert.equal(d.getElementById('favoriteCount').textContent,'1');
 assert(JSON.parse(w.localStorage.getItem('max.studio.favorites')).includes(id),'favorite saved locally');
 click('[data-filter="favorites"]');assert.equal(d.querySelectorAll('.action-item').length,1,'favorites filter');
 assert(d.querySelector('#featuredActions button').title===id.slice(id.indexOf(':')+1),'favorite promoted to cockpit');
 await delay(90);click('#actionLibrary [data-arm-command]');assert(log().some(m=>m.type==='arm' && m.action==='quick'),'execute favorite');
 click('.favorite');assert.equal(d.querySelectorAll('.action-item').length,0,'remove favorite');
 click('[data-filter="movement"]');assert.equal(d.querySelectorAll('.action-item').length,1,'sequences');
 click('[data-filter="step"]');assert.equal(d.querySelectorAll('.action-item').length,21,'steps');
 click('[data-view="jetson"]');assert.match(d.getElementById('jetsonCamera').textContent,/detectada/);assert.match(d.getElementById('jetsonVoiceNodes').textContent,/IA: apagado/);
 click('[data-view="cabina"]');click('#openConnection');
 const dialogCount=count();key('keydown','w');await delay(170);key('keyup','w');assert.equal(count(),dialogCount,'dialog disables driving');
 click('#closeConnection');key('keydown','s');key('keydown','d');await delay(180);key('keyup','s');key('keyup','d');await delay(80);
 assert(log().some(m=>m.type==='drive' && m.linear===-.05 && m.angular===.25),'S+D preserves reverse semantics');
 click('#stopAll');assert(log().some(m=>m.type==='stop_all'),'global stop');
 click('#openConnection');click('#connect');assert.match(d.getElementById('status').textContent,/Desconectado/);assert([...d.querySelectorAll('[data-arm-command],[data-direction]')].every(b=>b.disabled),'disconnect disables commands');
 assert.equal(errors.length,0,errors.join('\n'));
 console.log('PASS: simulated connection, WASD combinations/release, stop on navigation, hidden view/dialog key guards, joints, feedback, search, favorites, filters, Jetson states, global stop and disconnect. DOM only; layout needs a renderer.');
}finally{w.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
