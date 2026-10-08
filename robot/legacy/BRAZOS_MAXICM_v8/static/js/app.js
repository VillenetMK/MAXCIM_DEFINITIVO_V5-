let listaSecuencia = [];
let servosCached = {};
let quickActionsCached = {};
let movementsCached = {};
let stepsCached = {};
let editandoPasoName = null;
let initialRenderDone = false;

// ===============================
// ESTADO GENERAL
// ===============================

async function actualizarEstado() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();

        servosCached = data.servos || {};
        quickActionsCached = data.quick_actions || {};
        movementsCached = data.movements || {};
        stepsCached = data.steps || {};

        renderSerialInfo(data);

        if (!initialRenderDone) {
            renderBrazo([0, 1, 2], "brazo1-container", servosCached);
            renderBrazo([4, 5, 6], "brazo2-container", servosCached);

            renderTablaPasos(stepsCached);
            renderAccionesRapidas(quickActionsCached);
            renderMovimientosCreados(movementsCached);
            actualizarVisualizacionCola();
            initialRenderDone = true;
        } else {
            // Actualización suave de servos sin re-renderizar el DOM completo para no perder focus/dragging
            Object.keys(servosCached).forEach(ch => {
                const s = servosCached[ch];
                const slider = document.getElementById(`slider-${ch}`);
                if (slider && document.activeElement !== slider) {
                    slider.value = s.current;
                }
                const display = document.getElementById(`val-display-${ch}`);
                if (display) {
                    display.innerText = s.current + "°";
                }
            });
        }
    } catch (err) {
        console.error("Error al actualizar estado:", err);
    }
}

async function refrescarConfiguracion() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();

        servosCached = data.servos || {};
        quickActionsCached = data.quick_actions || {};
        movementsCached = data.movements || {};
        stepsCached = data.steps || {};

        renderTablaPasos(stepsCached);
        renderAccionesRapidas(quickActionsCached);
        renderMovimientosCreados(movementsCached);
    } catch (err) {
        console.error("Error al refrescar configuración:", err);
    }
}

function renderSerialInfo(data) {
    const box = document.getElementById("serial-info");
    if (!box) return;

    let serialText = "";

    if (data.serial_connected) {
        serialText = `✅ ESP32 Conectado en <b class="highlight">${data.serial_port}</b>`;
    } else {
        serialText = `⚠️ ESP32 Desconectado (Modo Simulación)`;
    }

    if (data.sequence_running) {
        serialText += ` | <span class="status-running">⏳ Secuencia en ejecución</span>`;
    } else {
        serialText += ` | <span class="status-free">🟢 Robot Listo</span>`;
    }

    box.innerHTML = serialText;
}

// ===============================
// CONTROLES DE BRAZOS
// ===============================

function renderBrazo(canales, containerId, servos) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = "";

    canales.forEach(ch => {
        const s = servos[ch];

        if (!s) return;

        container.innerHTML += `
            <div class="servo-control">
                <div class="servo-header">
                    <span>${s.name} <span class="ch-badge">Ch ${ch}</span></span>
                    <span class="servo-angle" id="val-display-${ch}">${s.current}°</span>
                </div>

                <div class="controls">
                    <button class="btn-action-small btn-orange" onclick="mover(${ch}, 'step_down')">
                        -1°
                    </button>

                    <input
                        type="range"
                        min="${s.min}"
                        max="${s.max}"
                        value="${s.current}"
                        id="slider-${ch}"
                        onchange="mover(${ch}, 'angle', this.value)"
                        oninput="document.getElementById('val-display-${ch}').innerText = this.value + '°'"
                    >

                    <button class="btn-action-small btn-orange" onclick="mover(${ch}, 'step_up')">
                        +1°
                    </button>
                </div>
            </div>
        `;
    });
}

async function mover(channel, action, value = 0) {
    try {
        const res = await fetch("/api/move", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                channel,
                action,
                value
            })
        });

        const data = await res.json();

        if (data.status === "success") {
            const slider = document.getElementById(`slider-${channel}`);
            const display = document.getElementById(`val-display-${channel}`);

            if (slider) slider.value = data.current_angle;
            if (display) display.innerText = data.current_angle + "°";

            document.getElementById("info-global").innerHTML =
                `⚙️ Movimiento manual: Canal ${channel} ➔ <b>${data.current_angle}°</b>`;

            actualizarAnguloSugerido();
        }
    } catch (err) {
        console.error("Error al mover servo:", err);
    }
}

async function irAHomeGlobal() {
    document.getElementById("info-global").innerHTML =
        "🚀 Iniciando Homing general...";

    try {
        const res = await fetch("/api/home", {
            method: "POST"
        });

        const data = await res.json();

        if (data.status === "started") {
            document.getElementById("info-global").innerHTML =
                "✅ Homing iniciado en el robot.";
        } else if (data.status === "busy") {
            document.getElementById("info-global").innerHTML =
                "⚠️ El robot está ocupado con otra secuencia.";
        } else {
            alert(data.message || "Error al enviar Home.");
        }
    } catch (err) {
        console.error("Error en home general:", err);
    }

    actualizarEstado();
}

// ===============================
// PASOS INDIVIDUALES
// ===============================

function actualizarAnguloSugerido() {
    const ch = document.getElementById("step-channel").value;

    if (servosCached[ch]) {
        document.getElementById("step-angle").value = servosCached[ch].current;
    }
}

function renderTablaPasos(steps) {
    const tbody = document.getElementById("steps-table-body");
    if (!tbody) return;
    tbody.innerHTML = "";

    const names = Object.keys(steps);

    if (names.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="empty-text">
                    No hay pasos guardados en la biblioteca.
                </td>
            </tr>
        `;
        return;
    }

    names.forEach(name => {
        const s = steps[name];
        const motorName = servosCached[s.channel]
            ? servosCached[s.channel].name
            : `Canal ${s.channel}`;

        if (name === editandoPasoName) {
            // Fila en Modo Edición
            const motorOptions = [
                { value: 0, label: "Ch 0 - Brazo 1 Hombro" },
                { value: 1, label: "Ch 1 - Brazo 1 Codo Rot" },
                { value: 2, label: "Ch 2 - Brazo 1 Codo Vert" },
                { value: 4, label: "Ch 4 - Brazo 2 Hombro" },
                { value: 5, label: "Ch 5 - Brazo 2 Codo Rot" },
                { value: 6, label: "Ch 6 - Brazo 2 Codo Vert" }
            ];

            const optionsHtml = motorOptions.map(opt =>
                `<option value="${opt.value}" ${opt.value == s.channel ? 'selected' : ''}>${opt.label}</option>`
            ).join("");

            tbody.innerHTML += `
                <tr class="editing-row">
                    <td class="step-id-edit">${name}</td>
                    <td>
                        <select id="edit-channel" class="input-table-select">${optionsHtml}</select>
                    </td>
                    <td>
                        <input type="number" id="edit-angle" class="input-table-number" value="${s.angle}"> °
                    </td>
                    <td>
                        <input type="number" id="edit-interval" class="input-table-number" value="${s.interval}"> ms
                    </td>
                    <td>
                        <input type="number" id="edit-delay" class="input-table-number" value="${s.delay}" step="0.1"> s
                    </td>
                    <td>
                        <button class="btn-green" onclick="guardarEdicionPaso('${name}')">Guardar</button>
                        <button class="btn-dark" onclick="cancelarEdicionPaso()">Cancelar</button>
                    </td>
                </tr>
            `;
        } else {
            // Fila en Modo Vista Normal
            tbody.innerHTML += `
                <tr>
                    <td class="step-id-column">${name}</td>
                    <td class="step-motor-column">${motorName} <span class="ch-badge-table">Ch ${s.channel}</span></td>
                    <td class="step-angle-column"><b>${s.angle}°</b></td>
                    <td>
                        <input
                            type="number"
                            class="input-table-number"
                            value="${s.interval}"
                            onchange="modificarPasoCampo('${name}', 'interval', this.value)"
                        > ms
                    </td>
                    <td>
                        <input
                            type="number"
                            class="input-table-number"
                            value="${s.delay}"
                            step="0.1"
                            onchange="modificarPasoCampo('${name}', 'delay', this.value)"
                        > s
                    </td>
                    <td>
                        <button class="btn-action btn-blue" onclick="ejecutarPasoUnico('${name}')" title="Probar movimiento">
                            Probar
                        </button>
                        <button class="btn-action btn-green" onclick="agregarACola('${name}', false)" title="Añadir a la secuencia">
                            + Sec
                        </button>
                        <button class="btn-action btn-purple" onclick="agregarACola('${name}', true)" title="Añadir en paralelo al último grupo">
                            + Par
                        </button>
                        <button class="btn-action btn-orange" onclick="activarEdicionPaso('${name}')" title="Editar paso">
                            Editar
                        </button>
                        <button class="btn-action btn-red" onclick="eliminarPaso('${name}')" title="Eliminar paso">
                            🗑️
                        </button>
                    </td>
                </tr>
            `;
        }
    });
}

function activarEdicionPaso(name) {
    editandoPasoName = name;
    renderTablaPasos(stepsCached);
}

function cancelarEdicionPaso() {
    editandoPasoName = null;
    renderTablaPasos(stepsCached);
}

async function modificarPasoCampo(name, campo, valor) {
    const paso = stepsCached[name];
    if (!paso) return;

    // Actualizar en caché local inmediatamente
    if (campo === "interval") {
        paso.interval = parseInt(valor);
    } else if (campo === "delay") {
        paso.delay = parseFloat(valor);
    }

    try {
        await fetch("/api/steps", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                name: name,
                channel: paso.channel,
                angle: paso.angle,
                interval: paso.interval,
                delay: paso.delay
            })
        });

        document.getElementById("info-global").innerHTML =
            `💾 <b>${name}</b>: ${campo === "interval" ? "velocidad" : "retardo"} actualizado a <b>${valor}${campo === "interval" ? " ms" : " s"}</b>`;
    } catch (err) {
        console.error("Error al modificar campo del paso:", err);
    }
}

async function guardarEdicionPaso(name) {
    const channel = document.getElementById("edit-channel").value;
    const angle = document.getElementById("edit-angle").value;
    const interval = document.getElementById("edit-interval").value;
    const delay = document.getElementById("edit-delay").value;

    if (angle === "") return alert("Especifica un ángulo.");

    try {
        await fetch("/api/steps", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                name,
                channel: parseInt(channel),
                angle: parseInt(angle),
                interval: parseInt(interval),
                delay: parseFloat(delay)
            })
        });

        editandoPasoName = null;
        document.getElementById("info-global").innerHTML =
            `💾 Paso <b>${name}</b> editado con éxito.`;

        await refrescarConfiguracion();
    } catch (err) {
        console.error("Error al guardar edición de paso:", err);
    }
}

async function crearPaso() {
    const name = document.getElementById("step-name").value.trim();
    const channel = document.getElementById("step-channel").value;
    const angle = document.getElementById("step-angle").value;
    const interval = document.getElementById("step-interval").value;
    const delay = document.getElementById("step-delay").value;

    if (!name) return alert("Por favor escribe un nombre para identificar el paso.");
    if (angle === "") return alert("Especifica un ángulo.");

    try {
        await fetch("/api/steps", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                name,
                channel: parseInt(channel),
                angle: parseInt(angle),
                interval: parseInt(interval),
                delay: parseFloat(delay)
            })
        });

        document.getElementById("step-name").value = "";
        document.getElementById("info-global").innerHTML =
            `✅ Paso <b>${name}</b> creado con éxito.`;

        refrescarConfiguracion();
    } catch (err) {
        console.error("Error al crear paso:", err);
    }
}

async function eliminarPaso(name) {
    const confirmar = confirm(`¿Eliminar paso: ${name}?`);
    if (!confirmar) return;

    try {
        await fetch("/api/steps/delete", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ name })
        });

        document.getElementById("info-global").innerHTML =
            `🗑️ Paso <b>${name}</b> eliminado de la biblioteca.`;

        refrescarConfiguracion();
    } catch (err) {
        console.error("Error al eliminar paso:", err);
    }
}

async function ejecutarPasoUnico(name) {
    document.getElementById("info-global").innerHTML =
        `🚀 Enviando paso individual: <b>${name}</b>`;

    try {
        const res = await fetch("/api/steps/run_single", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ name })
        });

        const data = await res.json();

        if (data.status === "started") {
            document.getElementById("info-global").innerHTML =
                `✅ Paso ejecutado: <b>${name}</b>`;
        } else if (data.status === "busy") {
            document.getElementById("info-global").innerHTML =
                "⚠️ El robot está ocupado ejecutando una secuencia.";
        } else {
            alert(data.message || "Error ejecutando paso.");
        }
    } catch (err) {
        console.error("Error al ejecutar paso:", err);
    }

    actualizarEstado();
}

// ===============================
// CONSTRUCTOR DE SECUENCIA (PARALELO Y SECUENCIAL)
// ===============================

function agregarACola(name, isParallel = false) {
    if (listaSecuencia.length === 0) {
        listaSecuencia.push(name);
    } else {
        if (isParallel) {
            let lastIndex = listaSecuencia.length - 1;
            let lastItem = listaSecuencia[lastIndex];

            if (Array.isArray(lastItem)) {
                if (!lastItem.includes(name)) {
                    lastItem.push(name);
                }
            } else {
                if (lastItem !== name) {
                    listaSecuencia[lastIndex] = [lastItem, name];
                }
            }
        } else {
            listaSecuencia.push(name);
        }
    }
    actualizarVisualizacionCola();
}

function limpiarSecuencia() {
    listaSecuencia = [];
    actualizarVisualizacionCola();
    document.getElementById("info-global").innerHTML = "🧹 Constructor de rutas limpio.";
}

function obtenerDetallesPaso(name) {
    if (name === "[ IR A HOME COMIENZO ]") {
        return `<span class="detail-home">🏠 Ir a Home</span>`;
    }
    const step = stepsCached[name];
    if (step) {
        return `<span class="detail-info">Ch ${step.channel} ➔ <b>${step.angle}°</b> | v: ${step.interval}ms | d: ${step.delay}s</span>`;
    }
    const mv = movementsCached[name];
    if (mv) {
        return `<span class="detail-info-mv">📦 Movimiento (${mv.sequence.length} pasos)</span>`;
    }
    const qa = quickActionsCached[name];
    if (qa) {
        return `<span class="detail-info-qa">⚡ Accion (${qa.sequence.length} pasos)</span>`;
    }
    return `<span class="detail-unknown">Elemento externo</span>`;
}

function actualizarVisualizacionCola() {
    const q = document.getElementById("sequence-queue");
    if (!q) return;

    if (listaSecuencia.length === 0) {
        q.innerHTML = `<div class="empty-queue-text">[ Cola de ejecución vacía. Agrega pasos o movimientos ]</div>`;
        return;
    }

    q.innerHTML = "";

    listaSecuencia.forEach((item, index) => {
        const itemCard = document.createElement("div");
        itemCard.className = "queue-card";

        if (Array.isArray(item)) {
            // BLOQUE EN PARALELO
            itemCard.classList.add("parallel-block-card");

            let stepsHtml = item.map((subName, subIdx) => {
                return `
                    <div class="parallel-sub-card">
                        <div class="sub-card-header">
                            <span class="sub-step-title">${subName}</span>
                            <button class="btn-sub-delete" onclick="eliminarSubItem(${index}, ${subIdx})" title="Quitar de este bloque">×</button>
                        </div>
                        <div class="sub-card-body">
                            ${obtenerDetallesPaso(subName)}
                        </div>
                    </div>
                `;
            }).join("");

            itemCard.innerHTML = `
                <div class="card-group-header">
                    <span class="badge-parallel">⚡ PARALELO (${item.length} MOTORES)</span>
                    <div class="card-controls">
                        <button class="btn-small-control" onclick="moverCola(${index}, -1)" title="Mover arriba">🔼</button>
                        <button class="btn-small-control" onclick="moverCola(${index}, 1)" title="Mover abajo">🔽</button>
                        <button class="btn-small-control btn-purple" onclick="desagrupar(${index})" title="Separar en secuencia">🔓</button>
                        <button class="btn-small-control btn-red" onclick="eliminarDeCola(${index})" title="Quitar bloque">🗑️</button>
                    </div>
                </div>
                <div class="parallel-items-grid">
                    ${stepsHtml}
                </div>
            `;
        } else {
            // BLOQUE SECUENCIAL SIMPLE
            itemCard.classList.add("sequence-block-card");
            itemCard.innerHTML = `
                <div class="sequence-card-body">
                    <div class="card-info-side">
                        <span class="step-title">${item}</span>
                        <div class="step-sub-detail">
                            ${obtenerDetallesPaso(item)}
                        </div>
                    </div>
                    <div class="card-controls">
                        <button class="btn-small-control" onclick="moverCola(${index}, -1)" title="Mover arriba">🔼</button>
                        <button class="btn-small-control" onclick="moverCola(${index}, 1)" title="Mover abajo">🔽</button>
                        <button class="btn-small-control btn-purple" onclick="convertirEnParalelo(${index})" title="Convertir a contenedor paralelo">🔗</button>
                        <button class="btn-small-control btn-red" onclick="eliminarDeCola(${index})" title="Quitar de la cola">🗑️</button>
                    </div>
                </div>
            `;
        }

        q.appendChild(itemCard);

        // Flecha de secuencia
        if (index < listaSecuencia.length - 1) {
            const arrow = document.createElement("div");
            arrow.className = "queue-arrow-indicator";
            arrow.innerHTML = "➔";
            q.appendChild(arrow);
        }
    });
}

function eliminarDeCola(index) {
    listaSecuencia.splice(index, 1);
    actualizarVisualizacionCola();
}

function eliminarSubItem(groupIndex, subIndex) {
    if (Array.isArray(listaSecuencia[groupIndex])) {
        listaSecuencia[groupIndex].splice(subIndex, 1);

        if (listaSecuencia[groupIndex].length === 0) {
            listaSecuencia.splice(groupIndex, 1);
        } else if (listaSecuencia[groupIndex].length === 1) {
            listaSecuencia[groupIndex] = listaSecuencia[groupIndex][0];
        }
    }
    actualizarVisualizacionCola();
}

function moverCola(index, direction) {
    let newIndex = index + direction;
    if (newIndex < 0 || newIndex >= listaSecuencia.length) return;

    let temp = listaSecuencia[index];
    listaSecuencia[index] = listaSecuencia[newIndex];
    listaSecuencia[newIndex] = temp;

    actualizarVisualizacionCola();
}

function desagrupar(index) {
    if (Array.isArray(listaSecuencia[index])) {
        const items = listaSecuencia[index];
        listaSecuencia.splice(index, 1, ...items);
    }
    actualizarVisualizacionCola();
}

function convertirEnParalelo(index) {
    const item = listaSecuencia[index];
    if (typeof item === "string") {
        listaSecuencia[index] = [item];
    }
    actualizarVisualizacionCola();
}

async function ejecutarSecuencia() {
    if (listaSecuencia.length === 0) {
        return alert("Añade pasos, movimientos o acciones a la secuencia primero.");
    }

    document.getElementById("info-global").innerHTML =
        "🚀 Enviando secuencia al robot...";

    try {
        const res = await fetch("/api/sequence/run", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                sequence: listaSecuencia
            })
        });

        const data = await res.json();

        if (data.status === "started") {
            document.getElementById("info-global").innerHTML =
                "✅ Secuencia enviada. El robot la está ejecutando.";
        } else if (data.status === "busy") {
            document.getElementById("info-global").innerHTML =
                "⚠️ El robot está ocupado con otra ejecución.";
        } else {
            alert(data.message || "Error ejecutando secuencia.");
        }
    } catch (err) {
        console.error("Error al ejecutar secuencia:", err);
    }

    actualizarEstado();
}

// ===============================
// ACCIONES RÁPIDAS EDITABLES
// ===============================

function renderAccionesRapidas(quickActions) {
    const tbody = document.getElementById("quick-actions-table-body");
    if (!tbody) return;

    tbody.innerHTML = "";

    const names = Object.keys(quickActions);

    if (names.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="3" class="empty-text">
                    No hay acciones rápidas creadas.
                </td>
            </tr>
        `;
        return;
    }

    names.forEach(name => {
        const action = quickActions[name];

        // Renderizar texto de secuencia reconociendo bloques paralelos
        const formatSeq = (seq) => {
            return seq.map(item => {
                if (Array.isArray(item)) {
                    return `<span class="parallel-badge-text">[${item.join(" + ")}]</span>`;
                }
                return item;
            }).join(" ➔ ");
        };

        const sequenceText = formatSeq(action.sequence);

        tbody.innerHTML += `
            <tr>
                <td class="step-id-column">
                    ⚡ ${name}
                </td>

                <td class="sequence-text">
                    ${sequenceText}
                </td>

                <td>
                    <button class="btn-action btn-green" onclick="ejecutarAccionRapida('${name}')" title="Ejecutar acción rápida">
                        Ejecutar
                    </button>

                    <button class="btn-action btn-blue" onclick="agregarAccionRapidaACola('${name}')" title="Cargar secuencia en cola">
                        + Ruta
                    </button>

                    <button class="btn-action btn-orange" onclick="editarAccionRapida('${name}')" title="Editar secuencia">
                        Editar
                    </button>

                    <button class="btn-action btn-red" onclick="eliminarAccionRapida('${name}')" title="Eliminar">
                        🗑️
                    </button>
                </td>
            </tr>
        `;
    });
}

async function guardarAccionRapida(mode) {
    const name = document.getElementById("quick-action-name").value.trim();

    if (!name) return alert("Escribe un nombre para la acción rápida.");
    if (listaSecuencia.length === 0) return alert("Primero arma una ruta en el constructor.");

    try {
        const res = await fetch("/api/quick_actions/save", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                name,
                sequence: listaSecuencia,
                mode
            })
        });

        const data = await res.json();

        if (data.status === "success") {
            document.getElementById("info-global").innerHTML =
                `✅ Acción rápida guardada: <b>${name}</b>`;

            document.getElementById("quick-action-name").value = "";
            refrescarConfiguracion();
        } else if (data.status === "exists") {
            document.getElementById("info-global").innerHTML =
                `⚠️ La acción rápida <b>${name}</b> ya existía y se mantuvo sin cambios.`;
        } else {
            alert(data.message || "Error guardando acción rápida.");
        }
    } catch (err) {
        console.error("Error al guardar acción rápida:", err);
    }
}

async function ejecutarAccionRapida(name) {
    document.getElementById("info-global").innerHTML =
        `🚀 Iniciando acción rápida: <b>${name}</b>`;

    try {
        const res = await fetch("/api/quick_actions/run", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ name })
        });

        const data = await res.json();

        if (data.status === "started") {
            document.getElementById("info-global").innerHTML =
                `✅ Acción rápida enviada: <b>${name}</b>`;
        } else if (data.status === "busy") {
            document.getElementById("info-global").innerHTML =
                "⚠️ El robot está ejecutando otra tarea.";
        } else {
            alert(data.message || "Error ejecutando acción rápida.");
        }
    } catch (err) {
        console.error("Error al ejecutar acción rápida:", err);
    }

    actualizarEstado();
}

function agregarAccionRapidaACola(name) {
    if (!quickActionsCached[name]) return;
    const seq = JSON.parse(JSON.stringify(quickActionsCached[name].sequence));
    listaSecuencia.push(...seq);
    actualizarVisualizacionCola();
}

function editarAccionRapida(name) {
    if (!quickActionsCached[name]) return;

    document.getElementById("quick-action-name").value = name;
    listaSecuencia = JSON.parse(JSON.stringify(quickActionsCached[name].sequence));
    actualizarVisualizacionCola();

    document.getElementById("info-global").innerHTML =
        `✏️ Editando acción rápida: <b>${name}</b>. Modifica la ruta y presiona Crear / Editar.`;
}

async function eliminarAccionRapida(name) {
    const confirmar = confirm(`¿Eliminar acción rápida: ${name}?`);
    if (!confirmar) return;

    try {
        await fetch("/api/quick_actions/delete", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ name })
        });

        document.getElementById("info-global").innerHTML =
            `🗑️ Acción rápida eliminada: <b>${name}</b>`;

        refrescarConfiguracion();
    } catch (err) {
        console.error("Error al eliminar acción rápida:", err);
    }
}

// ===============================
// MOVIMIENTOS CREADOS
// ===============================

function renderMovimientosCreados(movements) {
    const tbody = document.getElementById("movements-table-body");
    if (!tbody) return;

    tbody.innerHTML = "";

    const names = Object.keys(movements);

    if (names.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="3" class="empty-text">
                    No hay movimientos creados.
                </td>
            </tr>
        `;
        return;
    }

    names.forEach(name => {
        const movement = movements[name];

        const formatSeq = (seq) => {
            return seq.map(item => {
                if (Array.isArray(item)) {
                    return `<span class="parallel-badge-text">[${item.join(" + ")}]</span>`;
                }
                return item;
            }).join(" ➔ ");
        };

        const sequenceText = formatSeq(movement.sequence);

        tbody.innerHTML += `
            <tr>
                <td class="step-id-column">
                    📦 ${name}
                </td>

                <td class="sequence-text">
                    ${sequenceText}
                </td>

                <td>
                    <button class="btn-action btn-green" onclick="ejecutarMovimientoCreado('${name}')" title="Ejecutar movimiento completo">
                        Ejecutar
                    </button>

                    <button class="btn-action btn-blue" onclick="agregarMovimientoCreadoACola('${name}')" title="Cargar en el constructor">
                        + Ruta
                    </button>

                    <button class="btn-action btn-orange" onclick="editarMovimientoCreado('${name}')" title="Editar secuencia">
                        Editar
                    </button>

                    <button class="btn-action btn-red" onclick="eliminarMovimientoCreado('${name}')" title="Eliminar">
                        🗑️
                    </button>
                </td>
            </tr>
        `;
    });
}

async function guardarMovimiento(mode) {
    const name = document.getElementById("movement-name").value.trim();

    if (!name) return alert("Escribe un nombre para el movimiento.");
    if (listaSecuencia.length === 0) return alert("Primero arma una ruta en el constructor.");

    try {
        const res = await fetch("/api/movements/save", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                name,
                sequence: listaSecuencia,
                mode
            })
        });

        const data = await res.json();

        if (data.status === "success") {
            document.getElementById("info-global").innerHTML =
                `✅ Movimiento guardado: <b>${name}</b>`;

            document.getElementById("movement-name").value = "";
            refrescarConfiguracion();
        } else if (data.status === "exists") {
            document.getElementById("info-global").innerHTML =
                `⚠️ El movimiento <b>${name}</b> ya existía y se mantuvo sin cambios.`;
        } else {
            alert(data.message || "Error guardando movimiento.");
        }
    } catch (err) {
        console.error("Error al guardar movimiento:", err);
    }
}

async function ejecutarMovimientoCreado(name) {
    document.getElementById("info-global").innerHTML =
        `🚀 Iniciando movimiento creado: <b>${name}</b>`;

    try {
        const res = await fetch("/api/movements/run", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ name })
        });

        const data = await res.json();

        if (data.status === "started") {
            document.getElementById("info-global").innerHTML =
                `✅ Movimiento enviado: <b>${name}</b>`;
        } else if (data.status === "busy") {
            document.getElementById("info-global").innerHTML =
                "⚠️ El robot está ejecutando otra tarea.";
        } else {
            alert(data.message || "Error ejecutando movimiento.");
        }
    } catch (err) {
        console.error("Error al ejecutar movimiento:", err);
    }

    actualizarEstado();
}

function agregarMovimientoCreadoACola(name) {
    if (!movementsCached[name]) return;
    const seq = JSON.parse(JSON.stringify(movementsCached[name].sequence));
    listaSecuencia.push(...seq);
    actualizarVisualizacionCola();
}

function editarMovimientoCreado(name) {
    if (!movementsCached[name]) return;

    document.getElementById("movement-name").value = name;
    listaSecuencia = JSON.parse(JSON.stringify(movementsCached[name].sequence));
    actualizarVisualizacionCola();

    document.getElementById("info-global").innerHTML =
        `✏️ Editando movimiento: <b>${name}</b>. Modifica la ruta y presiona Crear / Editar.`;
}

async function eliminarMovimientoCreado(name) {
    const confirmar = confirm(`¿Eliminar movimiento creado: ${name}?`);
    if (!confirmar) return;

    try {
        await fetch("/api/movements/delete", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ name })
        });

        document.getElementById("info-global").innerHTML =
            `🗑️ Movimiento eliminado: <b>${name}</b>`;

        refrescarConfiguracion();
    } catch (err) {
        console.error("Error al eliminar movimiento:", err);
    }
}

// ===============================
// INICIO
// ===============================

actualizarEstado();
setInterval(actualizarEstado, 3000);
