'use strict';
const $ = id => document.getElementById(id);
const directions = [...document.querySelectorAll('[data-direction]')];
let socket = null, connected = false, ready = false, challenge = '', sequence = 0;
let lastState = 0, active = null, activePointer = null;
const heldKeys = new Set();
const pendingKeyReleases = new Map();
const KEY_RELEASE_GRACE_MS = 35;
function clearKeyboard() {
  for (const timer of pendingKeyReleases.values()) clearTimeout(timer);
  pendingKeyReleases.clear();
  heldKeys.clear();
}
let awaitingStop = false, stopRequest = 0;
const keys = {w:'forward',ArrowUp:'forward',s:'back',ArrowDown:'back',a:'left',ArrowLeft:'left',d:'right',ArrowRight:'right',q:'forward-left',e:'forward-right',z:'back-left',c:'back-right'};
const format = value => Number(value).toFixed(2).replace('.', ',');
function message(text) { $('message').textContent = text; }
function paint() {
  directions.forEach(button => { button.disabled = !connected || !ready; button.classList.toggle('pressed', active === button.dataset.direction); });
  $('status').textContent = connected ? (ready ? 'Conectado · Listo' : 'Conectado · En espera') : 'Desconectado';
  $('statusIcon').textContent = connected ? '✓' : '○';
  $('connect').textContent = connected ? 'Desconectar' : 'Conectar';
  $('token').disabled = connected; $('address').disabled = connected;
  window.dispatchEvent(new CustomEvent('studio:base', {detail:{connected,ready,active}}));
}
function send(data) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data)); }
function stop() {
  active = null; activePointer = null; clearKeyboard();
  if (connected) { awaitingStop = true; send({type:'stop',request:++stopRequest}); }
  challenge = '';
  paint();
}
function reset() {
  active = null; activePointer = null; clearKeyboard();
  connected = ready = false; challenge = ''; lastState = 0;
  awaitingStop = false; stopRequest = 0;
  $('mode').textContent = 'SIN CONEXIÓN';
  $('lidarState').textContent = $('odomState').textContent = '— Sin datos';
  $('measured').textContent = '— m/s'; paint();
}
function drivingViewVisible() { return !$('cabina').hidden && !$('connectionDialog').open; }
function start(direction) {
  if (!drivingViewVisible() || !connected || !ready || !document.hasFocus() || document.hidden) return;
  active = direction; paint(); drive();
}
function drive() {
  if (!drivingViewVisible()) { if (active) stop(); return; }
  if (!active || !ready || !connected || !challenge || awaitingStop) return;
  if (performance.now() - lastState > 350 || socket.bufferedAmount > 0) { stop(); return; }
  const v = Number($('speed').value), w = Number($('turn').value);
  // In reverse, left/right describe the path of the rear of the robot.
  const vector = {
    forward:[v,0],back:[-v,0],left:[0,w],right:[0,-w],
    'forward-left':[v,w],'forward-right':[v,-w],
    'back-left':[-v,-w],'back-right':[-v,w]
  }[active];
  send({type:'drive',seq:++sequence,challenge,linear:vector[0],angular:vector[1]}); challenge = '';
}
function endpoint() {
  let raw = $('address').value.trim();
  if (!/^https?:\/\//i.test(raw)) raw = 'http://' + raw;
  const url = new URL(raw);
  if (!['http:','https:'].includes(url.protocol) || url.username || url.password) throw Error('Escribe una dirección HTTP o HTTPS válida.');
  url.pathname = '/'; url.search = ''; url.hash = '';
  if (location.protocol === 'https:' && url.protocol === 'http:') {
    $('localLink').href = url.href; $('localLink').hidden = false;
    throw Error('Para controlar por la red local, abre el panel de la Raspberry con el enlace de abajo.');
  }
  return url;
}
$('connectForm').addEventListener('submit', event => {
  event.preventDefault();
  if (connected) { stop(); socket?.close(); reset(); message('Control desconectado.'); return; }
  let url; try { url = endpoint(); } catch (error) { message(error.message); return; }
  if (!$('token').value.trim()) { message('Introduce el código de acceso del panel.'); return; }
  socket?.close(); reset(); $('localLink').hidden = true;
  const token = $('token').value.trim();
  const current = new WebSocket(url.href.replace(/^http/, 'ws') + 'ws'); socket = current;
  message('Conectando con MAX…'); $('connect').disabled = true;
  const timer = setTimeout(() => { if (!connected && current === socket) { current.close(); message('No hay respuesta. Revisa que estén en la misma red.'); } }, 6000);
  current.onopen = () => current.send(JSON.stringify({type:'auth',token}));
  current.onmessage = event => {
    if (current !== socket) return;
    let data; try { data = JSON.parse(event.data); } catch { return; }
    if (data.type === 'connected') {
      clearTimeout(timer); connected = true; sequence = 0; $('connect').disabled = false;
      $('mode').textContent = data.demo ? 'DEMOSTRACIÓN' : 'EN VIVO';
      sessionStorage.setItem('max.studio.address', url.href);
      sessionStorage.setItem('max.studio.token', token);
      message(data.demo ? 'Demostración: los controles no mueven ningún robot.' : 'Control conectado. Mantén pulsada una dirección.');
    } else if (data.type === 'state') {
      lastState = performance.now(); challenge = awaitingStop ? '' : data.challenge; ready = data.ready;
      $('lidarState').textContent = data.scan_ok ? '✓ Recibiendo' : '— Sin lecturas';
      $('odomState').textContent = data.odom_ok ? '✓ Recibiendo' : '— Sin lecturas';
      $('measured').textContent = format(data.linear) + ' m/s';
      if (!ready) { if (active) stop(); message(data.reason); }
      else if (!active) message($('mode').textContent === 'DEMOSTRACIÓN' ? 'Demostración: los controles no mueven ningún robot.' : 'Listo. Mantén pulsada una dirección para mover a MAX.');
    } else if (data.type === 'stopped' && data.request === stopRequest) {
      awaitingStop = false; challenge = '';
    } else if (data.type === 'error') { stop(); message(data.message); }
    window.dispatchEvent(new CustomEvent('studio:message', {detail:data}));
    paint();
  };
  current.onerror = () => { if (current === socket) message('No se pudo conectar con MAX. Revisa la dirección y la red.'); };
  current.onclose = () => { clearTimeout(timer); if (current === socket) { reset(); $('connect').disabled = false; } };
});
directions.forEach(button => {
  button.addEventListener('pointerdown', event => {
    if (event.button !== 0 || activePointer !== null || heldKeys.size > 0 || !ready) return;
    event.preventDefault(); button.setPointerCapture(event.pointerId);
    activePointer = event.pointerId; start(button.dataset.direction);
  });
  const release = event => { if (event.pointerId === activePointer) stop(); };
  button.addEventListener('pointerup', release); button.addEventListener('pointercancel', release); button.addEventListener('lostpointercapture', release);
  button.addEventListener('contextmenu', event => event.preventDefault());
});
function keyboardDirection() {
  const pressed = [...heldKeys].map(key => keys[key]);
  const forward = pressed.some(d => d === 'forward' || d.startsWith('forward-'));
  const back = pressed.some(d => d === 'back' || d.startsWith('back-'));
  const left = pressed.some(d => d === 'left' || d.endsWith('-left'));
  const right = pressed.some(d => d === 'right' || d.endsWith('-right'));
  // Opposing inputs stop and clear the gesture; fresh presses are required.
  if ((forward && back) || (left && right)) return null;
  const travel = forward ? 'forward' : back ? 'back' : '';
  const turn = left ? 'left' : right ? 'right' : '';
  return travel && turn ? travel + '-' + turn : travel || turn || null;
}
function updateKeyboard() {
  const direction = keyboardDirection();
  if (direction) start(direction);
  else stop();
}
const editable = target => ['INPUT','TEXTAREA','SELECT'].includes(target.tagName) || target.isContentEditable;
document.addEventListener('keydown', event => {
  if (event.code === 'Space' && !editable(event.target)) { event.preventDefault(); stop(); return; }
  if (!drivingViewVisible() || editable(event.target) || event.ctrlKey || event.altKey || event.metaKey) {
    if (heldKeys.size) stop();
    return;
  }
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (!Object.hasOwn(keys, key)) return;
  event.preventDefault();
  // Remote/X11 autorepeat may emit keyup immediately before keydown.
  if (pendingKeyReleases.has(key)) {
    clearTimeout(pendingKeyReleases.get(key));
    pendingKeyReleases.delete(key);
  }
  if (event.repeat || heldKeys.has(key) || activePointer !== null ||
      !connected || !ready || !document.hasFocus() || document.hidden) return;
  heldKeys.add(key);
  updateKeyboard();
});
document.addEventListener('keyup', event => {
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (!heldKeys.has(key)) return;
  event.preventDefault();
  if (pendingKeyReleases.has(key)) return;
  pendingKeyReleases.set(key, setTimeout(() => {
    pendingKeyReleases.delete(key);
    if (heldKeys.delete(key)) updateKeyboard();
  }, KEY_RELEASE_GRACE_MS));
});
document.addEventListener('focusin', event => { if (editable(event.target)) stop(); });
$('stop').addEventListener('click', stop);
window.addEventListener('blur', stop);
document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
window.addEventListener('pagehide', () => { stop(); socket?.close(); });
for (const id of ['speed','turn']) $(id).addEventListener('input', () => {
  stop(); $(id+'Value').replaceChildren(document.createTextNode(format($(id).value)+' '), Object.assign(document.createElement('small'),{textContent:id==='speed'?'m/s':'rad/s'}));
});
setInterval(() => {
  if (connected && performance.now() - lastState > 500) {
    stop(); ready = false; paint(); message('Lecturas atrasadas. Movimiento detenido.');
  } else drive();
}, 100);
// A pairing fragment is local to the browser and is never sent in HTTP requests.
window.MAXStudio = {
  send, stop, get connected(){return connected;},
  stopAll(){stop();send({type:'stop_all',request:stopRequest});},
  disconnect(){stop();socket?.close();},
  arm(data){stop();send({...data,type:'arm',request:stopRequest});}
};
const fragment = new URLSearchParams(location.hash.slice(1));
$('address').value = sessionStorage.getItem('max.studio.address') || (location.hostname.endsWith('github.io') ? 'http://192.168.1.120:8080' : location.origin);
$('token').value = fragment.get('token') || sessionStorage.getItem('max.studio.token') || '';
if (fragment.has('token')) history.replaceState(null, '', location.pathname + location.search);
paint();
