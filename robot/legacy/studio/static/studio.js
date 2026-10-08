'use strict';
(() => {
  const el = id => document.getElementById(id);
  const api = window.MAXStudio;
  let connected = false, arms = null, armChallenge = '', armSeq = 0, armAt = 0;
  let selectedFilter = 'quick', catalogueKey = '', jointsKey = '', sendingArm = false;
  let favorites = new Set();
  try { const saved = JSON.parse(localStorage.getItem('max.studio.favorites') || '[]'); if (Array.isArray(saved)) favorites = new Set(saved.filter(v=>typeof v==='string')); } catch {}
  let noticeTimer;
  const kinds = {quick:'quick_actions',movement:'movements',step:'steps'};
  const labels = {
    SALUDOMILITAR:'Saludo militar',SALUDOMILITARD:'Saludo militar · derecho',
    DARMANODERECHA:'Dar la mano derecha',DARMANOIZQUIERDA:'Dar la mano izquierda',
    'EXTENDER-DOS-BRAZOS':'Extender ambos brazos','HOME-DOS-BRAZOS':'Reposo de ambos brazos',
    'HOME-BRAZO-DERECHO':'Reposo del brazo derecho','HOME-BRAZO-IZQUIERDO':'Reposo del brazo izquierdo',
    BAILE:'Baile',BRAZO_DERECHO_ARRIBA:'Levantar brazo derecho',BRAZO_IZQUIERDO_ARRIBA:'Levantar brazo izquierdo'
  };
  const title = name => labels[name] || name.replaceAll('_',' ').replaceAll('-',' ').toLowerCase().replace(/^./, c=>c.toUpperCase());
  const directionNames = {forward:'Avanzando',back:'Retrocediendo',left:'Giro izquierdo',right:'Giro derecho','forward-left':'Curva izquierda','forward-right':'Curva derecha','back-left':'Reversa izquierda','back-right':'Reversa derecha'};
  const node = (tag, text, cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  function available(){return connected && arms?.available && !arms.sequence_running && !sendingArm;}
  function notice(text){
    el('armNotice').textContent=text;
    const toast=el('armFeedback');
    if(toast.textContent===text && !toast.hidden)return;
    toast.textContent=text;toast.hidden=false;
    clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{toast.hidden=true;},5500);
  }
  function openConnection(){api.stopAll();el('connectionDialog').showModal();}
  el('openConnection').onclick = openConnection;
  el('connectionShortcut').onclick = openConnection;
  el('closeConnection').onclick = () => el('connectionDialog').close();
  el('connectionDialog').addEventListener('cancel', () => api.stopAll());
  el('stopAll').onclick=()=>{api.stopAll();notice('Cancelando los pasos pendientes de brazos.');};
  el('stop').addEventListener('click',()=>api.stopAll());
  document.addEventListener('keydown',e=>{
    if(e.code==='Space' && !['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName) && !e.target.isContentEditable){e.preventDefault();api.stopAll();}
  });
  window.addEventListener('blur',()=>{if(connected)api.stopAll();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden && connected)api.stopAll();});
  setInterval(()=>{if(connected && !document.hidden)api.send({type:'heartbeat'});},250);
  el('legacyBase').href=location.protocol+'//'+location.hostname+':8080/';
  function paintArms(){
    const ok=available();
    document.querySelectorAll('[data-arm-command]').forEach(b=>b.disabled=!ok);
    document.querySelectorAll('.joints input').forEach(input=>input.disabled=!ok);
    el('homeAll').disabled=!ok;
    el('armsHealth').textContent=!connected?'○ Sin conexión':arms?.available?'✓ ESP32 conectado':'— No disponible';
    el('armActivity').textContent=!connected?'Sin sesión activa':sendingArm?'Enviando orden…':arms?.sequence_running?'Ejecutando movimiento':arms?.available?'Listo para un movimiento':'Esperando al ESP32';
  }
  function armCommand(action,data={}){
    if(!available())return;
    if(!armChallenge || performance.now()-armAt>500){notice('Esperando conexión actualizada. Vuelve a pulsar.');return;}
    sendingArm=true;paintArms();
    api.arm({action,...data,seq:++armSeq,challenge:armChallenge});armChallenge='';
    notice('Enviando la orden…');
  }
  function actionButton(name,kind,featured=false){
    const b=node('button',featured?title(name):'Ejecutar ↗');b.type='button';b.dataset.armCommand='true';
    b.title=name;b.onclick=()=>armCommand(kind,{name});
    if(featured)b.append(node('span','↗'));
    else { b.textContent='Ejecutar'; b.append(node('span','↗')); }
    return b;
  }
  function catalogEntries(){
    return Object.entries(kinds).flatMap(([kind,key])=>Object.entries(arms?.[key] || {}).map(([name,data])=>({kind,name,data,id:kind+':'+name})));
  }
  function renderLibrary(){
    const host=el('actionLibrary');host.replaceChildren();
    const all=catalogEntries();
    el('favoriteCount').textContent=all.filter(entry=>favorites.has(entry.id)).length;
    const entries=all.filter(entry=>selectedFilter==='favorites'?favorites.has(entry.id):entry.kind===selectedFilter);
    const query=el('actionSearch').value.trim().toLocaleLowerCase();
    const filtered=entries.filter(({name})=>(title(name)+' '+name).toLocaleLowerCase().includes(query));
    el('libraryCount').textContent=filtered.length+' MOVIMIENTO'+(filtered.length===1?'':'S');
    if(!filtered.length){
      const text=!connected?'Conecta con MAX para cargar tus movimientos guardados.':query?'No hay movimientos con ese nombre.':selectedFilter==='favorites'?'Marca la estrella de un movimiento para encontrarlo aquí.':'No hay movimientos disponibles en esta categoría.';
      host.append(node('p',text,'empty'));return;
    }
    filtered.forEach(({name,data,kind,id},i)=>{
      const card=node('article',undefined,'action-item'), info=node('div'),top=node('div',undefined,'action-top');
      top.append(node('span',String(i+1).padStart(2,'0')+' / '+({quick:'EXPRESIÓN',movement:'SECUENCIA',step:'PASO'}[kind]),'action-num'));
      const favorite=node('button',favorites.has(id)?'★':'☆','favorite');favorite.type='button';
      favorite.setAttribute('aria-pressed',String(favorites.has(id)));
      favorite.setAttribute('aria-label',(favorites.has(id)?'Quitar de favoritos: ':'Añadir a favoritos: ')+title(name));
      favorite.onclick=()=>{
        if(favorites.has(id))favorites.delete(id);else favorites.add(id);
        try{localStorage.setItem('max.studio.favorites',JSON.stringify([...favorites]));}catch{}
        renderLibrary();renderFeatured();
        const replacement=[...host.querySelectorAll('.favorite')].find(button=>button.dataset.favoriteId===id);
        (replacement || document.querySelector('[data-filter="favorites"]')).focus({preventScroll:true});
      };
      favorite.dataset.favoriteId=id;top.append(favorite);info.append(top,node('h3',title(name)));
      const detail=kind==='step'?`Canal ${data.channel} · ${data.angle}° · intervalo ${data.interval ?? 5} ms`:`${data.sequence?.length || 0} bloques · ${data.mode==='parallel'?'en paralelo':'en secuencia'}`;
      info.append(node('p',detail));card.append(info,actionButton(name,kind));host.append(card);
    });paintArms();
  }
  function renderFeatured(){
    const host=el('featuredActions');host.replaceChildren();
    const preferred=['SALUDOMILITAR','DARMANODERECHA','DARMANOIZQUIERDA','EXTENDER-DOS-BRAZOS'];
    const all=catalogEntries(), pinned=all.filter(entry=>favorites.has(entry.id));
    const defaults=preferred.map(name=>all.find(entry=>entry.kind==='quick' && entry.name===name)).filter(Boolean);
    const seen=new Set();
    const entries=[...pinned,...defaults].filter(entry=>!seen.has(entry.id)&&seen.add(entry.id)).slice(0,4);
    for(const {name,kind} of entries)host.append(actionButton(name,kind,true));
    if(!entries.length)host.append(node('p','Conecta para cargar tus expresiones.','empty'));
    el('actionCount').textContent=pinned.length?'TUS FAVORITOS':Object.keys(arms?.quick_actions||{}).length+' EXPRESIONES';
    paintArms();
  }
  function renderJoints(){
    const config=arms?.servos || {};
    const signature=JSON.stringify(Object.entries(config).map(([ch,c])=>[ch,c.min,c.max,c.name]));
    if(signature!==jointsKey){
      jointsKey=signature;el('leftJoints').replaceChildren();el('rightJoints').replaceChildren();
      Object.entries(config).forEach(([ch,c])=>{
        const row=node('div',undefined,'joint'),top=node('div',undefined,'joint-top');
        const short=c.name.replace(/^Brazo \d (IZQ|DER) /,'');
        const output=node('output',c.current+'°');output.id='jointValue'+ch;
        const input=node('input');input.type='range';input.min=c.min;input.max=c.max;input.step=1;input.value=c.current;input.id='joint'+ch;input.setAttribute('aria-label',c.name);
        output.setAttribute('for',input.id);
        top.append(node('span',short),output);
        input.oninput=()=>{api.stop();output.textContent=input.value+'°';input.dataset.edited='true';};
        const controls=node('div',undefined,'joint-controls'),move=node('button','Mover');move.dataset.armCommand='true';move.onclick=()=>{armCommand('joint',{channel:Number(ch),angle:Number(input.value)});};
        controls.append(input,move);
        const info=node('div',undefined,'joint-info');info.append(node('span',`${c.min}° — ${c.max}°`),node('span','Objetivo: '+c.current+'°'));
        info.lastChild.id='jointTarget'+ch;
        row.append(top,controls,info);(Number(ch)<3?el('leftJoints'):el('rightJoints')).append(row);
      });
    }
    Object.entries(config).forEach(([ch,c])=>{
      el('jointTarget'+ch).textContent='Objetivo: '+c.current+'°';
      const input=el('joint'+ch);
      if(!input.dataset.edited && document.activeElement!==input){input.value=c.current;el('jointValue'+ch).textContent=c.current+'°';}
    });paintArms();
  }
  el('homeAll').onclick=()=>armCommand('quick',{name:'HOME-DOS-BRAZOS'});
  el('actionSearch').oninput=renderLibrary;
  document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{
    selectedFilter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>{x.classList.toggle('selected',x===b);x.setAttribute('aria-pressed',String(x===b));});renderLibrary();
  });
  window.addEventListener('studio:base',e=>{
    connected=e.detail.connected;
    el('baseHealth').textContent=!connected?'○ Sin conexión':e.detail.ready?'✓ Lista para conducir':'— En espera';
    el('directionLabel').textContent=directionNames[e.detail.active] || 'En reposo';
    if(!connected){paintJetson(null);sendingArm=false;armChallenge='';arms=null;el('scanBadge').textContent='Sin conexión';el('mapBadge').textContent='Sin conexión';}
    paintArms();
  });
  window.addEventListener('studio:message',e=>{
    const m=e.detail;
    if(m.type==='connected'){armSeq=0;el('connectionDialog').close();api.send({type:'heartbeat'});el('driveTitle').focus();}
    if(m.type==='arm_challenge'){armChallenge=m.challenge;armAt=performance.now();}
    if(m.type==='arms_state'){
      arms=m.data;
      if(!arms.available)notice(arms.message||arms.serial_error||'ESP32 no disponible');
      const key=JSON.stringify([arms.quick_actions,arms.movements,arms.steps]);
      if(key!==catalogueKey){catalogueKey=key;renderFeatured();renderLibrary();}
      if(arms.servos)renderJoints();paintArms();
    }
    if(['arm_result','arm_error','notice'].includes(m.type)){sendingArm=false;notice(m.message);paintArms();}
    if(m.type==='visual')drawVisual(m.data);
    if(m.type==='jetson_state')paintJetson(m.data);
  });
  function paintJetson(data){
    const ok=connected && data?.available;
    el('jetsonBadge').textContent=ok?'✓ En línea':connected?'— Sin respuesta':'○ Sin conexión';
    const value=(n,suffix='')=>Number.isFinite(n)?n+suffix:'—';
    el('jetsonLoad').textContent=ok?value(data.load)+' / '+value(data.cores)+' CPU':'—';
    el('jetsonRam').textContent=ok?value(data.ram_used_gb)+' / '+value(data.ram_total_gb)+' GB':'—';
    el('jetsonTemperature').textContent=ok?value(data.temperature_c,' °C'):'—';
    el('jetsonDisk').textContent=ok?value(data.disk_free_gb,' GB'):'—';
    el('jetsonCamera').textContent=ok?(data.camera_usb?'✓ Cámara detectada por USB':'— Cámara no detectada'):'Sin datos de la cámara.';
    el('jetsonMic').textContent=ok?(data.microphone_usb?'✓ ReSpeaker detectado por USB':'— ReSpeaker no detectado'):'Sin datos del micrófono.';
    const state=n=>data?.processes?.[n]?'activo':'apagado';
    el('jetsonVisionNodes').textContent=ok?`Cámara: ${state('camera')} · Reconocimiento: ${state('faces')} · Proximidad: ${state('proximity')}`:'—';
    el('jetsonVoiceNodes').textContent=ok?`Micrófono: ${state('microphone')} · IA: ${state('voice')} · Memoria: ${state('memory')} · Pantalla: ${state('screen')}`:'—';
    el('jetsonNote').textContent=ok?'Actualización cada 3 segundos. El estado de procesos no confirma la recepción de audio o imágenes.':connected?(data?.message||'Jetson sin respuesta. Los controles de la Raspberry siguen disponibles.'):'Conecta con MAX para consultar la Jetson.';
  }
  function emptyCanvas(id,label){const c=el(id),ctx=c.getContext('2d');ctx.fillStyle='#eef0e9';ctx.fillRect(0,0,c.width,c.height);ctx.fillStyle='#666b6c';ctx.font='15px sans-serif';ctx.textAlign='center';ctx.fillText(label,c.width/2,c.height/2);}
  let lastMap=0;
  function drawVisual(data){
    const canvas=el('scanCanvas'),ctx=canvas.getContext('2d'),cx=300,cy=185,scale=26;
    ctx.fillStyle='#eef0e9';ctx.fillRect(0,0,600,360);ctx.strokeStyle='#c7cdc3';ctx.lineWidth=1;
    [2,4,6].forEach(r=>{ctx.beginPath();ctx.arc(cx,cy,r*scale,0,Math.PI*2);ctx.stroke();});
    ctx.beginPath();ctx.moveTo(cx,15);ctx.lineTo(cx,345);ctx.moveTo(40,cy);ctx.lineTo(560,cy);ctx.stroke();
    if(data.scan_fresh){ctx.fillStyle='#222629';for(const [x,y] of data.scan||[]){if(Math.hypot(x,y)<=6)ctx.fillRect(cx-y*scale-2,cy-x*scale-2,4,4);}}
    ctx.fillStyle='#222629';ctx.beginPath();ctx.moveTo(cx,cy-9);ctx.lineTo(cx-7,cy+6);ctx.lineTo(cx+7,cy+6);ctx.closePath();ctx.fill();
    el('scanBadge').textContent=data.scan_fresh?'✓ En vivo':'— Lecturas atrasadas';
    const map=data.map;
    if(map && map.revision!==lastMap){
      lastMap=map.revision;const temp=document.createElement('canvas');temp.width=map.width;temp.height=map.height;
      const t=temp.getContext('2d'),pixels=t.createImageData(map.width,map.height),cells=atob(map.cells);
      for(let i=0;i<cells.length;i++){const value=[202,246,37][cells.charCodeAt(i)] ?? 202;pixels.data.set([value,value,value,255],i*4);}t.putImageData(pixels,0,0);
      const m=el('mapCanvas').getContext('2d');m.fillStyle='#eef0e9';m.fillRect(0,0,600,360);m.imageSmoothingEnabled=false;
      const scale=Math.min(560/map.width,320/map.height);m.save();m.translate((600-map.width*scale)/2,(360+map.height*scale)/2);m.scale(1,-1);m.drawImage(temp,0,0,map.width*scale,map.height*scale);m.restore();
    }
    el('mapBadge').textContent=map?`${(map.width*map.resolution).toFixed(1)} × ${(map.height*map.resolution).toFixed(1)} m · ${data.map_age ?? '—'} s`:'Sin mapa';
    el('poseText').textContent=data.pose?`Odometría: x ${data.pose.x.toFixed(2)} m · y ${data.pose.y.toFixed(2)} m. El mapa no envía destinos.`:'Odometría: sin datos';
  }
  emptyCanvas('scanCanvas','Conecta para ver el LiDAR');emptyCanvas('mapCanvas','Conecta para ver el mapa');
  paintArms();
  // Connection is explicit: the console can be explored while MAX is offline.
})();
/* Presentation only. A hidden driving view must never retain keyboard control. */
'use strict';
(() => {
  const views = {
    cabina: ['Conducción', '01 / CONTROL MANUAL', 'A los mandos.', 'Todo a mano. Cada movimiento, bajo tu control.'],
    brazos: ['Brazos', '02 / CONTROL ARTICULAR', 'Precisión en cada gesto.', 'Seis articulaciones. Los mismos límites calibrados.'],
    biblioteca: ['Movimientos', '03 / REPERTORIO V8', 'Que MAX se exprese.', 'Encuentra, guarda como favorito y ejecuta tus movimientos.'],
    entorno: ['Entorno', '04 / PERCEPCIÓN', 'El mundo de MAX.', 'Una vista del barrido y del espacio que lo rodea.'],
    jetson: ['Jetson', '05 / INTELIGENCIA', 'Al otro lado, la Jetson.', 'Una mirada al equipo, sus dispositivos y sus procesos.']
  };
  function showView(id) {
    if (!Object.hasOwn(views, id)) return;
    window.MAXStudio.stop();
    document.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = panel.id !== id; });
    document.querySelectorAll('[data-view]').forEach(button => {
      if (button.dataset.view === id) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    const [name, eyebrow, title, description] = views[id];
    document.getElementById('viewCrumb').textContent = name;
    document.getElementById('viewEyebrow').textContent = eyebrow;
    document.getElementById('viewTitle').textContent = title;
    document.getElementById('viewDescription').textContent = description;
    document.getElementById('keyboardState').textContent = id === 'cabina' ? 'WASD + flechas' : 'Pausado en esta vista';
    document.title = `MAX Studio · ${name}`;
    document.getElementById('viewTitle').focus({preventScroll: true});
    window.scrollTo({top: 0, behavior: 'instant'});
  }
  document.querySelectorAll('[data-view], [data-open-view]').forEach(button => {
    button.addEventListener('click', event => {
      event.preventDefault();
      showView(button.dataset.view || button.dataset.openView);
    });
  });
})();
