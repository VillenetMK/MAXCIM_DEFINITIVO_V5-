"use strict";
const $ = (id) => document.getElementById(id);
let actor = null, csrf = "", socket = null, robot = {}, seq = 0;
let motion = null, motionBusy = false, motionTimer = null, heartbeatTimer = null, noticeTimer;
let connectionGeneration = 0;
let cameraBusy=false,cameraObjectUrl=null;
const controlClient = Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,"0")).join("");
const roles = {admin:"Administrador",engineer:"Ingeniero",teacher:"Docente",observer:"Observador"};
const operator = () => actor && ["admin","engineer"].includes(actor.role);
const contributor = () => actor && actor.role !== "observer";
function notice(text){$("notice").textContent=text;$("notice").hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$("notice").hidden=true,6000);}
async function api(path,options={}){
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(), 2200);
  try{
    const response=await fetch(path,{...options,credentials:"same-origin",signal:controller.signal,headers:{"Content-Type":"application/json","X-CSRF-Token":csrf,"X-Control-Client":controlClient,...options.headers}});
    const data=await response.json();
    if(!response.ok){if(response.status===401 && path!=="/api/login") showLogin();throw new Error(data.error||"Solicitud rechazada");}
    return data;
  }finally{clearTimeout(timer);}
}
const command = (name,payload={}) => api("/api/robot/command",{method:"POST",body:JSON.stringify({command:name,payload})});
function showLogin(){actor=null;csrf="";connectionGeneration++;haltMotion(false);clearInterval(heartbeatTimer);if(socket)socket.close();$("app-view").hidden=true;$("login-view").hidden=false;}
async function signedIn(){
  actor=await api("/api/me");csrf=actor.csrf;
  $("account").textContent=`${actor.username} · ${roles[actor.role]}`;
  document.querySelectorAll(".operator-only").forEach(el=>el.hidden=!operator());
  document.querySelectorAll(".contributor-only").forEach(el=>el.hidden=!contributor());
  document.querySelectorAll(".admin-only").forEach(el=>el.hidden=actor.role!=="admin");
  $("login-view").hidden=true;$("app-view").hidden=false;
  switchView("robot");renderRobot(await api("/api/robot"));connectEvents(++connectionGeneration);
  clearInterval(heartbeatTimer);heartbeatTimer=setInterval(async()=>{if(robot.owns_control&&actor){try{await command("heartbeat");}catch(error){haltMotion(false);notice(error.message);}}},700);
}
function connectEvents(generation){
  if(!actor||generation!==connectionGeneration)return;
  socket=new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/api/events?client=${controlClient}`);
  socket.onmessage=(event)=>{try{const packet=JSON.parse(event.data);if(packet.type==="state")renderRobot(packet.data);}catch{notice("Estado recibido inválido");}};
  socket.onclose=()=>{if(!actor||generation!==connectionGeneration)return;renderRobot({...robot,connected:false,owns_control:false,reason:"Conexión perdida; espera telemetría nueva"});haltMotion(false);setTimeout(()=>connectEvents(generation),1800);};
}
function renderRobot(state){
  robot=state;const simulation=(state.mode||"").includes("simulation");
  if(state.owns_control)seq=Math.max(seq,state.sequence||0);
  $("mode-badge").textContent=simulation?"SIMULACIÓN":state.connected?"ROS 2 / CONECTADO":"ROS 2 / SIN CONEXIÓN";
  $("simulation-banner").hidden=!simulation;
  $("gateway-state").textContent=state.connected?"Conectado":"Desconectado";
  $("gateway-detail").textContent=simulation?"Entorno de pruebas": "Gateway ROS 2";
  $("control-state").textContent=state.owns_control?"Tu turno":state.estop?"Enclavado":"Disponible / otro operador";
  $("control-detail").textContent=state.owns_control?"Concesión renovada mientras estás conectado":"Adquiere el turno para operar";
  $("velocity-state").textContent=`${Number(state.linear||0).toFixed(2)} m/s`;
  $("angular-state").textContent=`${Number(state.angular||0).toFixed(2)} rad/s · consigna`;
  $("arms-state").textContent=state.arm_busy?"Secuencia activa":"En espera";
  $("estop-state").textContent=state.estop?"SÍ · requiere rearme":"No";
  $("clearance-state").textContent=state.scan_recent?`${Number(state.clearance).toFixed(2)} m`:"Sin lectura reciente";
  $("reason").textContent=state.reason||"Esperando estado";
  $("voice").textContent=state.voice_enabled?"Desactivar gestos por voz":"Permitir gestos por voz";
  $("voice").disabled=!state.connected||!state.owns_control||state.estop;
  if(state.sensors?.camera==="recent"&&state.connected)refreshCamera();else{$("camera-image").hidden=true;$("camera-placeholder").hidden=false;}
  const sensorRoot=$("sensor-list");sensorRoot.replaceChildren();
  const names={lidar:"LiDAR",camera:"Cámara",audio:"Audio"},labels={simulated:"Simulado",not_connected:"Sin conexión",not_verified:"Sin verificar",recent:"Datos recientes",stale:"Datos vencidos"};
  for(const [key,name]of Object.entries(names)){const row=document.createElement("div"),label=document.createElement("span"),value=document.createElement("b");label.textContent=name;const status=(state.sensors||{})[key]|| (key==="lidar"&&state.scan_recent?"recent":"not_verified");value.textContent=labels[status]||status;row.append(label,value);sensorRoot.append(row);}
  document.querySelectorAll(".motion,[data-action]").forEach(el=>el.disabled=!state.connected||!state.owns_control||state.estop||state.arm_busy);
  $("acquire").disabled=!state.connected||state.estop||state.owns_control;
  $("release").disabled=!state.owns_control;$("reset").disabled=!state.connected||!state.estop;
  if(!state.connected||!state.owns_control||state.estop||state.arm_busy)haltMotion(false);
}
async function sendMotion(){
  if(!motion||motionBusy||!robot.owns_control)return;
  const current=motion;motionBusy=true;
  try{await command("drive",{seq:++seq,...current});}
  catch(error){haltMotion(true);notice(error.message);}
  finally{motionBusy=false;}
}
function haltMotion(send=true){const wasMoving=motion!==null;motion=null;clearInterval(motionTimer);motionTimer=null;if(send&&wasMoving&&actor)command("brake",{seq:++seq}).catch(error=>notice(error.message));}
document.querySelectorAll(".motion").forEach(button=>{
  button.addEventListener("pointerdown",event=>{if(button.disabled||event.button!==0)return;event.preventDefault();button.setPointerCapture(event.pointerId);motion={linear:Number(button.dataset.linear),angular:Number(button.dataset.angular)};sendMotion();clearInterval(motionTimer);motionTimer=setInterval(sendMotion,100);});
  for(const name of ["pointerup","pointercancel","lostpointercapture"])button.addEventListener(name,()=>haltMotion());
});
window.addEventListener("blur",()=>haltMotion());document.addEventListener("visibilitychange",()=>{if(document.hidden)haltMotion();});
window.addEventListener("keydown",event=>{if(event.key==="Escape"){haltMotion(false);if(actor)command("stop").catch(error=>notice(error.message));}});
for(const [id,kind]of [["acquire","acquire"],["release","release"],["reset","reset"],["stop","stop"],["emergency","estop"]])$(id).addEventListener("click",async()=>{haltMotion(false);try{await command(kind);if(kind==="acquire")seq=0;renderRobot(await api("/api/robot"));}catch(error){notice(error.message);}});
document.querySelectorAll("[data-action]").forEach(button=>button.addEventListener("click",async()=>{try{await command("arm",{action:button.dataset.action});}catch(error){notice(error.message);}}));
async function switchView(view){if(!actor)return;if(["activity"].includes(view)&&!operator())return;if(view==="settings"&&actor.role!=="admin")return;document.querySelectorAll(".view").forEach(el=>el.hidden=el.id!==`view-${view}`);document.querySelectorAll(".nav").forEach(el=>el.classList.toggle("active",el.dataset.view===view));$("page-name").textContent={robot:"Operación",team:"Equipo",education:"Aprendizaje",activity:"Registro",settings:"Accesos"}[view];haltMotion();try{if(view==="team")await renderTeam();if(view==="activity")await renderAudit();}catch(error){notice(error.message);}}
document.querySelectorAll("[data-view],[data-switch]").forEach(button=>button.addEventListener("click",()=>switchView(button.dataset.view||button.dataset.switch)));
async function renderTeam(){
  const [tasks,notes]=await Promise.all([api("/api/tasks"),api("/api/notes")]);const board=$("board");board.replaceChildren();
  for(const [state,label]of [["pending","Pendiente"],["working","En proceso"],["done","Terminada"]]){const column=document.createElement("div");column.className="column";const heading=document.createElement("h2");heading.textContent=`${label} · ${tasks.filter(t=>t.state===state).length}`;column.append(heading);
    for(const task of tasks.filter(t=>t.state===state)){const card=document.createElement("article"),title=document.createElement("p"),owner=document.createElement("small");card.className="task";title.textContent=task.title;owner.textContent=task.owner||"Sin responsable";card.append(title,owner);
      if(contributor()){const select=document.createElement("select");select.setAttribute("aria-label",`Estado de ${task.title}`);for(const [value,name]of [["pending","Pendiente"],["working","En proceso"],["done","Terminada"]]){const option=document.createElement("option");option.value=value;option.textContent=name;select.append(option);}select.value=state;select.addEventListener("change",async()=>{try{await api(`/api/tasks/${task.id}`,{method:"PATCH",body:JSON.stringify({state:select.value,version:task.version})});}catch(error){notice(error.message);}await renderTeam();});card.append(select);}column.append(card);
    }board.append(column);
  }
  $("notes").replaceChildren();if(!notes.length){const empty=document.createElement("p");empty.className="empty";empty.textContent="Aún no hay notas. Registra la primera decisión del equipo.";$("notes").append(empty);}
  for(const note of notes){const article=document.createElement("article"),body=document.createElement("p"),meta=document.createElement("small");article.className="note";body.textContent=note.body;meta.textContent=`${note.author} · ${new Date(note.timestamp*1000).toLocaleString("es-PE")}`;article.append(body,meta);$("notes").append(article);}
}
for(const [id,path,success]of [["task-form","/api/tasks","Tarea creada"],["note-form","/api/notes","Nota guardada"],["user-form","/api/users","Usuario creado"]])$(id).addEventListener("submit",async event=>{event.preventDefault();const form=event.target;try{await api(path,{method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(form)))});form.reset();notice(success);if(id!=="user-form")await renderTeam();}catch(error){notice(error.message);}});
async function renderAudit(){const rows=await api("/api/audit");$("audit").replaceChildren();for(const item of rows){const row=document.createElement("tr");for(const value of [new Date(item.timestamp*1000).toLocaleString("es-PE"),item.actor,item.event,item.detail]){const cell=document.createElement("td");cell.textContent=value;row.append(cell);}$("audit").append(row);}}
$("refresh-audit").addEventListener("click",()=>renderAudit().catch(error=>notice(error.message)));
$("login-form").addEventListener("submit",async event=>{event.preventDefault();$("login-error").textContent="";const button=event.target.querySelector("button");button.disabled=true;try{const result=await api("/api/login",{method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});csrf=result.csrf;event.target.reset();await signedIn();}catch(error){$("login-error").textContent=error.message;}finally{button.disabled=false;}});
$("logout").addEventListener("click",async()=>{haltMotion();try{await api("/api/logout",{method:"POST",body:"{}"});}catch(error){notice(error.message);}finally{showLogin();}});
signedIn().catch(()=>showLogin());

$("voice").addEventListener("click",async()=>{try{await command("voice",{enabled:!robot.voice_enabled});}catch(error){notice(error.message);}});
async function refreshCamera(){
 if(cameraBusy||!actor)return;cameraBusy=true;
 try{const response=await fetch("/api/camera",{credentials:"same-origin",signal:AbortSignal.timeout(1500)});if(!response.ok)throw new Error("No hay imagen reciente");const image=await response.blob();if(cameraObjectUrl)URL.revokeObjectURL(cameraObjectUrl);cameraObjectUrl=URL.createObjectURL(image);$("camera-image").src=cameraObjectUrl;$("camera-image").hidden=false;$("camera-placeholder").hidden=true;}catch{$("camera-image").hidden=true;$("camera-placeholder").hidden=false;}finally{setTimeout(()=>cameraBusy=false,400);}
}
