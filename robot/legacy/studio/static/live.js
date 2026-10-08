'use strict';
(() => {
  const $=id=>document.getElementById(id), api=window.MAXStudio;
  let challenge='', at=0, video=false, audio=false, voice=false, context=null;
  let nextAudio=0,lastImage=0,lastAudio=0, sources=new Set();
  function send(type,data={}) {
    if(!api.connected || !challenge || performance.now()-at>500) {
      $('voiceResult').textContent='Conecta con MAX y espera una actualización.';return false;
    }
    api.send({type,...data,challenge});challenge='';return true;
  }
  function paint(){
    for(const [id,on,yes,no] of [['videoToggle',video,'Pausar cámara','Ver cámara'],['audioToggle',audio,'Silenciar','Escuchar micrófono'],['voiceToggle',voice,'Desactivar control por voz','Activar control por voz']]){
      $(id).textContent=on?yes:no;$(id).setAttribute('aria-pressed',String(on));$(id).disabled=!api.connected;
    }
    $('voiceState').textContent=voice?'Control por voz activo · mantén esta ventana visible':'Control manual';
  }
  function silence(){for(const source of sources){try{source.stop();}catch{}}sources.clear();nextAudio=0;}
  function pauseMedia(){
    video=audio=false;silence();$('liveCamera').hidden=true;$('liveCamera').removeAttribute('src');
    $('cameraLiveState').textContent='Cámara en pausa';$('audioLiveState').textContent='Audio en pausa';
    if(api.connected)send('media',{video:false,audio:false});paint();
  }
  $('videoToggle').onclick=()=>{const next=!video;if(send('media',{video:next,audio})){video=next;if(!video){$('liveCamera').hidden=true;$('liveCamera').removeAttribute('src');}$('cameraLiveState').textContent=video?'Esperando imagen reciente…':'Cámara en pausa';paint();}};
  $('audioToggle').onclick=async()=>{
    const next=!audio;
    if(next){
      try{context??=new AudioContext();await context.resume();}
      catch{$('audioLiveState').textContent='Este navegador no permite reproducir audio.';return;}
    }
    if(send('media',{video,audio:next})){audio=next;if(!audio)silence();$('audioLiveState').textContent=audio?'Esperando audio…':'Audio en pausa';paint();}
  };
  $('voiceToggle').onclick=()=>{send('voice_mode',{enabled:!voice});};
  $('pointForm').onsubmit=e=>{e.preventDefault();send('save_point',{name:$('pointName').value.trim()});};
  window.addEventListener('studio:message',e=>{
    const m=e.detail;
    if(m.type==='arm_challenge'){challenge=m.challenge;at=performance.now();}
    if(m.type==='integration_state'){
      voice=m.data.voice;
      $('navigationState').textContent=m.data.base_blocked||`${m.data.motion} · ${m.data.navigation_ready?'Nav2 disponible':'Nav2 apagado'}`;
      $('knownPoints').textContent=m.data.points?.length?'Puntos: '+m.data.points.join(', '):'Sin puntos nombrados';paint();
    }
    if(m.type==='voice_result')$('voiceResult').textContent=m.data.error||m.data.message||m.data.state;
    if(m.type==='live_media'){
      if(video&&m.data.jpeg){$('liveCamera').src='data:image/jpeg;base64,'+m.data.jpeg;$('liveCamera').hidden=false;lastImage=performance.now();$('cameraLiveState').textContent='Imagen en directo';}
      if(audio&&context?.state==='running'&&m.data.pcm){
        const raw=atob(m.data.pcm), samples=Math.floor(raw.length/2);
        if(samples>8192)return;
        if(nextAudio-context.currentTime>.25)silence();
        const buffer=context.createBuffer(1,samples,16000), values=buffer.getChannelData(0);
        for(let i=0;i<samples;i++){let n=raw.charCodeAt(2*i)|(raw.charCodeAt(2*i+1)<<8);values[i]=(n>=32768?n-65536:n)/32768;}
        const source=context.createBufferSource();source.buffer=buffer;source.connect(context.destination);
        nextAudio=Math.max(context.currentTime+.02,nextAudio);source.start(nextAudio);nextAudio+=buffer.duration;
        sources.add(source);source.onended=()=>{sources.delete(source);source.disconnect();};
        lastAudio=performance.now();$('audioLiveState').textContent='Escuchando en directo';
      }
    }
  });
  window.addEventListener('studio:base',e=>{if(!e.detail.connected){voice=false;if(video||audio)pauseMedia();challenge='';}paint();});
  window.addEventListener('blur',pauseMedia);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pauseMedia();});
  setInterval(()=>{
    if(video&&performance.now()-lastImage>1200){$('liveCamera').hidden=true;$('liveCamera').removeAttribute('src');$('cameraLiveState').textContent='Sin imagen reciente. Revisa el proceso de cámara.';}
    if(audio&&performance.now()-lastAudio>1000){silence();$('audioLiveState').textContent='Sin audio reciente. Revisa el proceso de micrófono.';}
  },400);
  paint();
})();
