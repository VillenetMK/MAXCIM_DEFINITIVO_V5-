document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  function authorizedFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-CSRF-Token", csrfToken);
    return fetch(url, { ...options, headers });
  }

  const list = document.getElementById("materialList");
  const search = document.getElementById("materialSearch");
  const typeFilter = document.getElementById("materialTypeFilter");
  const cards = Array.from(list.querySelectorAll(".material-card"));

  cards.forEach((card) => {
    const header = card.querySelector(".material-card__header");
    header.addEventListener("click", () => {
      const wasOpen = card.classList.contains("is-open");
      cards.forEach((c) => c.classList.remove("is-open"));
      if (!wasOpen) {
        card.classList.add("is-open");
      }
    });
  });

  // Vista previa de "oraciones con imágenes": tarjetas horizontales, una por
  // oración, con flechas para deslizar (ver .material-card__slider* en
  // dashboard.css). Cada material de ese tipo tiene su propio slider.
  function wireImageSentenceSliders() {
    document.querySelectorAll("[data-slider]").forEach((slider) => {
      const track = slider.querySelector("[data-slider-track]");
      const prevBtn = slider.querySelector("[data-slider-prev]");
      const nextBtn = slider.querySelector("[data-slider-next]");
      if (!track || !prevBtn || !nextBtn) return;

      function slideBy(direction) {
        const slide = track.querySelector(".material-card__slide");
        const step = slide ? slide.getBoundingClientRect().width + 10 : track.clientWidth;
        track.scrollBy({ left: direction * step, behavior: "smooth" });
      }

      function updateNav() {
        const maxScroll = track.scrollWidth - track.clientWidth;
        prevBtn.disabled = track.scrollLeft <= 1;
        nextBtn.disabled = maxScroll <= 1 || track.scrollLeft >= maxScroll - 1;
      }

      prevBtn.addEventListener("click", () => slideBy(-1));
      nextBtn.addEventListener("click", () => slideBy(1));
      track.addEventListener("scroll", updateNav);
      // El track empieza oculto (la tarjeta de material recién se despliega
      // al tocarla) y clientWidth/scrollWidth valen 0 mientras tanto -sin
      // esto, "Siguiente" quedaría deshabilitado para siempre. ResizeObserver
      // recalcula en cuanto el contenedor pasa a tener tamaño real.
      new ResizeObserver(updateNav).observe(track);
    });
  }

  wireImageSentenceSliders();

  function applyFilters() {
    const query = search.value.trim().toLowerCase();
    const tipo = typeFilter.value;

    cards.forEach((card) => {
      const matchesType = !tipo || card.dataset.tipo === tipo;
      const matchesQuery = !query || card.dataset.title.includes(query);
      card.classList.toggle("is-hidden", !(matchesType && matchesQuery));
    });
  }

  search.addEventListener("input", applyFilters);
  typeFilter.addEventListener("change", applyFilters);

  // Delete flow: click "Eliminar" on a card -> confirm -> DELETE -> reload
  const deleteOverlay = document.getElementById("deleteOverlay");
  const deleteMaterialName = document.getElementById("deleteMaterialName");
  const deleteCancelBtn = document.getElementById("deleteCancelBtn");
  const deleteConfirmBtn = document.getElementById("deleteConfirmBtn");
  let pendingDeleteId = null;

  cards.forEach((card) => {
    const deleteBtn = card.querySelector(".material-card__delete");
    if (!deleteBtn) return;
    deleteBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      pendingDeleteId = deleteBtn.dataset.materialId;
      deleteMaterialName.textContent = deleteBtn.dataset.materialName || "este material";
      deleteOverlay.classList.add("is-open");
    });
  });

  function closeDeleteOverlay() {
    deleteOverlay.classList.remove("is-open");
    pendingDeleteId = null;
  }

  deleteCancelBtn.addEventListener("click", closeDeleteOverlay);

  deleteConfirmBtn.addEventListener("click", async () => {
    if (!pendingDeleteId) return;

    const originalLabel = deleteConfirmBtn.textContent;
    deleteConfirmBtn.disabled = true;
    deleteConfirmBtn.textContent = "Eliminando...";

    try {
      const response = await authorizedFetch(`/api/material/${pendingDeleteId}`, {
        method: "DELETE",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudo eliminar el material.");
      }
      window.location.reload();
    } catch (error) {
      alert(error.message || "No se pudo eliminar el material.");
      deleteConfirmBtn.disabled = false;
      deleteConfirmBtn.textContent = originalLabel;
      closeDeleteOverlay();
    }
  });

  // Upload flow: dropzone -> loading -> results
  const uploadOpenBtn = document.getElementById("uploadOpenBtn");
  const uploadOverlay = document.getElementById("uploadOverlay");
  const uploadCancelBtn = document.getElementById("uploadCancelBtn");
  const uploadStartBtn = document.getElementById("uploadStartBtn");
  const uploadTitleInput = document.getElementById("uploadTitleInput");
  const uploadPeriodoYearSelect = document.getElementById("uploadPeriodoYearSelect");
  const uploadPeriodoSelect = document.getElementById("uploadPeriodoSelect");
  const uploadTemaSelect = document.getElementById("uploadTemaSelect");
  const uploadFileInput = document.getElementById("uploadFileInput");
  const dropzone = document.getElementById("dropzone");
  const dropzoneText = document.getElementById("dropzoneText");
  const dropzoneHint = document.getElementById("dropzoneHint");
  const uploadManualOption = document.getElementById("uploadManualOption");
  const manualEditorOpenBtn = document.getElementById("manualEditorOpenBtn");
  const loadingOverlay = document.getElementById("loadingOverlay");
  const loadingSpinner = document.getElementById("loadingSpinner");
  const loadingText = document.getElementById("loadingText");
  const loadingCloseBtn = document.getElementById("loadingCloseBtn");
  const resultOverlay = document.getElementById("resultOverlay");
  const resultModalTitle = document.getElementById("resultModalTitle");
  const resultDoneBtn = document.getElementById("resultDoneBtn");
  const resultCancelBtn = document.getElementById("resultCancelBtn");
  const resultSubtitle = document.getElementById("resultSubtitle");
  const resultTranscribedText = document.getElementById("resultTranscribedText");
  const resultSummaryText = document.getElementById("resultSummaryText");
  const resultModal = resultOverlay.querySelector(".result-modal");
  const reviewSteps = Array.from(resultOverlay.querySelectorAll("[data-review-step]"));
  const reviewRequirements = document.getElementById("reviewRequirements");
  const reviewStepper = document.getElementById("reviewStepper");
  const reviewStepHeading = document.getElementById("reviewStepHeading");
  const resultBackBtn = document.getElementById("resultBackBtn");
  const resultScenesBlock = document.getElementById("resultScenesBlock");
  const generateScenesBtn = document.getElementById("generateScenesBtn");
  const scenesProgress = document.getElementById("scenesProgress");
  const scenesProgressText = document.getElementById("scenesProgressText");
  const scenesProgressCount = document.getElementById("scenesProgressCount");
  const scenesProgressTrack = document.getElementById("scenesProgressTrack");
  const scenesProgressFill = document.getElementById("scenesProgressFill");
  const scenesStale = document.getElementById("scenesStale");
  const scenesGrid = document.getElementById("scenesGrid");
  const scenesTone = document.getElementById("scenesTone");
  const scenesToneHint = document.getElementById("scenesToneHint");
  const sceneEditOverlay = document.getElementById("sceneEditOverlay");
  const sceneEditForm = document.getElementById("sceneEditForm");
  const sceneEditTitle = document.getElementById("sceneEditTitle");
  const sceneEditImage = document.getElementById("sceneEditImage");
  const sceneEditContext = document.getElementById("sceneEditContext");
  const sceneEditInstruction = document.getElementById("sceneEditInstruction");
  const sceneEditError = document.getElementById("sceneEditError");
  const sceneEditCancelBtn = document.getElementById("sceneEditCancelBtn");
  const sceneEditApplyBtn = document.getElementById("sceneEditApplyBtn");
  const storyOverlay = document.getElementById("storyOverlay");
  const storyOpenBtn = document.getElementById("storyOpenBtn");
  const storyCancelBtn = document.getElementById("storyCancelBtn");
  const storyForm = document.getElementById("storyForm");
  const storyGenerateBtn = document.getElementById("storyGenerateBtn");
  const storyModalTitle = document.getElementById("storyModalTitle");
  const storyModalDescription = document.getElementById("storyModalDescription");
  const storyTypeCuentoBtn = document.getElementById("storyTypeCuentoBtn");
  const storyTypeOracionBtn = document.getElementById("storyTypeOracionBtn");
  const storyTypeOracionImagenBtn = document.getElementById("storyTypeOracionImagenBtn");
  const storyTypeBitsBtn = document.getElementById("storyTypeBitsBtn");
  const storyCuentoFields = document.getElementById("storyCuentoFields");
  const storySentenceFields = document.getElementById("storySentenceFields");
  const storyImageFields = document.getElementById("storyImageFields");
  const storyBitsFields = document.getElementById("storyBitsFields");
  const bitsSyllablesInput = document.getElementById("bitsSyllables");
  const bitsSyllablesError = document.getElementById("bitsSyllablesError");
  const bitsCountInput = document.getElementById("bitsCount");
  const bitsSyllableCountInput = document.getElementById("bitsSyllableCount");
  const bitsDetailsInput = document.getElementById("bitsDetails");
  const uploadTypeCuentoBtn = document.getElementById("uploadTypeCuentoBtn");
  const uploadTypeOracionBtn = document.getElementById("uploadTypeOracionBtn");
  const uploadTypeOracionImagenBtn = document.getElementById("uploadTypeOracionImagenBtn");
  const uploadTypeBitsBtn = document.getElementById("uploadTypeBitsBtn");
  const resultTranscribedLabel = document.getElementById("resultTranscribedLabel");
  const resultTranscribedBlock = document.getElementById("resultTranscribedBlock");
  const resultSummaryBlock = document.getElementById("resultSummaryBlock");
  const resultSentencesBlock = document.getElementById("resultSentencesBlock");
  const resultSentencesLabel = document.getElementById("resultSentencesLabel");
  const resultSentencesNote = document.getElementById("resultSentencesNote");
  const resultSentencesList = document.getElementById("resultSentencesList");
  const addSentenceBtn = document.getElementById("addSentenceBtn");
  const generateMoreSentencesBtn = document.getElementById("generateMoreSentencesBtn");
  const resultQuestionsColumn = document.getElementById("resultQuestionsColumn");
  const imageDesignOverlay = document.getElementById("imageDesignOverlay");
  const imageDesignTitle = document.getElementById("imageDesignTitle");
  const imageDesignPrevBtn = document.getElementById("imageDesignPrevBtn");
  const imageDesignNextBtn = document.getElementById("imageDesignNextBtn");
  const imageDesignCounter = document.getElementById("imageDesignCounter");
  const imageDesignStage = document.getElementById("imageDesignStage");
  const imageDesignMixedLine = document.getElementById("imageDesignMixedLine");
  const imageDesignNounControls = document.getElementById("imageDesignNounControls");
  const imageDesignRemoveBtn = document.getElementById("imageDesignRemoveBtn");
  const imageDesignEmpty = document.getElementById("imageDesignEmpty");
  const imageDesignBackBtn = document.getElementById("imageDesignBackBtn");
  const imageDesignDiscardBtn = document.getElementById("imageDesignDiscardBtn");
  const imageDesignSaveBtn = document.getElementById("imageDesignSaveBtn");
  const bitsDesignOverlay = document.getElementById("bitsDesignOverlay");
  const bitsDesignTitle = document.getElementById("bitsDesignTitle");
  const bitsDesignPrevBtn = document.getElementById("bitsDesignPrevBtn");
  const bitsDesignNextBtn = document.getElementById("bitsDesignNextBtn");
  const bitsDesignCounter = document.getElementById("bitsDesignCounter");
  const bitsDesignStage = document.getElementById("bitsDesignStage");
  const bitsDesignImage = document.getElementById("bitsDesignImage");
  const bitsDesignNounControls = document.getElementById("bitsDesignNounControls");
  const bitsDesignRemoveBtn = document.getElementById("bitsDesignRemoveBtn");
  const bitsDesignEmpty = document.getElementById("bitsDesignEmpty");
  const bitsDesignBackBtn = document.getElementById("bitsDesignBackBtn");
  const bitsDesignDiscardBtn = document.getElementById("bitsDesignDiscardBtn");
  const bitsDesignSaveBtn = document.getElementById("bitsDesignSaveBtn");
  const classifyOverlay = document.getElementById("classifyOverlay");
  const classifyMaterialTitle = document.getElementById("classifyMaterialTitle");
  const classifyBackBtn = document.getElementById("classifyBackBtn");
  const classifySaveBtn = document.getElementById("classifySaveBtn");

  let selectedFile = null;
  let currentStoryType = "cuento";
  // Tema/nivel del último borrador de oraciones por IA; lo reutiliza el botón
  // "Generar 5 con IA" dentro de la revisión. Vacío si las oraciones vienen de
  // un documento subido (ahí la IA infiere el tema de las oraciones actuales).
  let lastSentenceContext = { topic: "", grade_level: "" };
  let currentUploadType = "cuento";
  let currentResultType = "cuento";
  // "Modo editor": la docente escribe las oraciones a mano, sin documento ni
  // IA. `manualEntryMode` es válido para "oracion" y "oracion_imagen";
  // `manualImageEntryMode` además activa, solo para esta última, las 2
  // imágenes por oración que sube ella misma (ver appendImageSentenceItem),
  // que se envían junto con el resto al guardar (ver saveManualImageSentenceMaterial).
  let manualEntryMode = false;
  let manualImageEntryMode = false;
  // "bits": mismo concepto que manualImageEntryMode pero para una sola
  // imagen por fila (ver appendBitsItem/saveManualBitsMaterial). Independiente
  // de manualImageEntryMode para no cruzar estado entre los dos flujos si
  // la docente cambia de tipo sin cerrar el modal.
  let manualBitsEntryMode = false;
  // Identificador propio de cada fila de "oracion_imagen", para poder
  // relacionar sus 2 imágenes con la oración correcta al guardar (ver
  // getImageSentencesData y saveManualImageSentenceMaterial) incluso si el
  // servidor descarta alguna fila duplicada al normalizar.
  let imageSentenceRowSeq = 0;
  // Mismo propósito que imageSentenceRowSeq, pero para las filas de "bits"
  // (ver getBitsData/saveManualBitsMaterial).
  let bitRowSeq = 0;
  // El contenido (cuento, oraciones, oraciones con imágenes...) ya quedó
  // aprobado y solo falta clasificarlo: guarda qué función ejecutar y a qué
  // modal volver si la docente se arrepiente (ver openClassifyModal, más
  // abajo del todo). Sin esto no hay nada que hacer si tocan "Guardar
  // material" en classifyOverlay.
  let pendingSave = null;
  let currentMaterialTitle = "";
  // Escenas ilustradas y narradas del cuento generado con IA (ver generateScenes).
  // Cada escena: { index, texto, url, state, audioUrl, audioDuration, audioState,
  // error, audioError } con state/audioState: "pending" | "loading" | "done" | "error"
  // (imagen y audio respectivamente). `scenesRunId` invalida las respuestas en vuelo cuando se cierra el modal
  // o se vuelven a generar las escenas.
  let scenes = [];
  let scenesToken = "";
  let scenesBusy = false;
  let scenesRunId = 0;
  let scenesStoryText = "";
  let scenesToneUsed = "";
  let sceneEditIndex = -1;
  let sceneEditBusy = false;
  let questionsReady = false;
  let currentTargetDurationMinutes = null;
  let imageDesignToken = "";
  let designSentences = [];
  let currentDesignIndex = 0;
  let imageDesignBusy = false;
  // Mismo esquema que imageDesignToken/designSentences/currentDesignIndex/
  // imageDesignBusy, pero para el diseño de "bits" (bitsDesignOverlay) -
  // estado separado a propósito, para que descartar un diseño no deje
  // basura en el otro flujo.
  let bitsDesignToken = "";
  let designBits = [];
  let currentBitsDesignIndex = 0;
  let bitsDesignBusy = false;

  const uploadTemaOptions = Array.from(uploadTemaSelect.options).filter((option) => option.value);

  function filterUploadTemas(resetSelection = true) {
    if (resetSelection) {
      uploadTemaSelect.value = "";
    }

    const selectedPeriodo = uploadPeriodoSelect.value;
    uploadTemaOptions.forEach((option) => {
      const isVisible = Boolean(selectedPeriodo) && option.dataset.periodo === selectedPeriodo;
      option.hidden = !isVisible;
      option.disabled = !isVisible;
    });
  }

  filterUploadTemas();
  uploadPeriodoSelect.addEventListener("change", () => {
    filterUploadTemas();
    updateClassifySaveState();
  });
  // period_filter.js restablece Periodo al cambiar Año. Este segundo
  // listener se ejecuta después y mantiene Tema sincronizado con ese reset.
  uploadPeriodoYearSelect.addEventListener("change", () => {
    filterUploadTemas();
    updateClassifySaveState();
  });
  uploadTemaSelect.addEventListener("change", () => {
    updateClassifySaveState();
  });

  const uploadTypeButtons = [uploadTypeCuentoBtn, uploadTypeOracionBtn, uploadTypeOracionImagenBtn, uploadTypeBitsBtn];

  function setUploadType(type) {
    currentUploadType = type;
    uploadTypeButtons.forEach((btn) => {
      const active = btn.dataset.type === type;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", String(active));
    });
    uploadTitleInput.placeholder = type === "cuento"
      ? "Título del cuento"
      : type === "oracion_imagen"
        ? "Título de las oraciones con imágenes"
        : type === "bits"
          ? "Título de los bits"
          : "Título del material";
    dropzoneHint.textContent = type === "oracion_imagen"
      ? "DOC, TXT o PDF con una oración por línea - máx. 50 MB"
      : type === "bits"
        ? "DOC, TXT o PDF con una palabra por línea - máx. 50 MB"
        : "DOC, TXT o PDF · máx. 50 MB";
    // El modo editor (escribir el material a mano, sin documento) no aplica
    // a "cuento".
    uploadManualOption.hidden = type === "cuento";
    manualEditorOpenBtn.textContent = type === "bits"
      ? "✏️ Modo editor: crear los bits a mano"
      : "✏️ Modo editor: escribir las oraciones a mano";
    updateUploadButtonState();
  }

  // Ajusta la pantalla de revisión de contenido extraído por IA según el
  // tipo: las oraciones solo revisan el texto, sin resumen, audios ni
  // preguntas (eso es exclusivo de los cuentos).
  function configureResultModalForType(type) {
    currentResultType = type;
    // El modo editor lo activa explícitamente openManualEditor() DESPUÉS de
    // llamar a esta función; cualquier otro camino (documento, IA) arranca
    // siempre en modo revisión normal.
    manualEntryMode = false;
    manualImageEntryMode = false;
    manualBitsEntryMode = false;
    // El nombre del material también se puede editar aquí mismo (ver el
    // listener "input" de resultModalTitle): se muestra el que ya se
    // escribió al inicio -o el de la IA/el documento- en vez de un rótulo
    // fijo como "Contenido generado".
    resultModalTitle.textContent = currentMaterialTitle;
    resultSentencesNote.hidden = false;
    const isOracion = type === "oracion";
    const isImageSentence = type === "oracion_imagen";
    const isBits = type === "bits";
    const isSentenceType = isOracion || isImageSentence || isBits;
    resultTranscribedLabel.textContent = "Texto completo";
    resultTranscribedBlock.hidden = isSentenceType;
    resultSummaryBlock.hidden = isSentenceType;
    resultSentencesBlock.hidden = !isSentenceType;
    // Las escenas solo se ofrecen en cuentos (generados con IA o cargados desde
    // un documento): esos caminos las muestran después de llamar a esta función.
    resultScenesBlock.hidden = true;
    // Con un solo bloque (oraciones, oraciones con imágenes, bits) no hay
    // acordeón: se oculta el encabezado y el bloque queda siempre abierto. Los
    // cuentos, en cambio, se revisan como un asistente de tres pantallas
    // (ver openReviewStep).
    resultModal.classList.toggle("result-modal--simple", isSentenceType);
    resultModal.classList.toggle("result-modal--wizard", !isSentenceType);
    reviewStepper.hidden = isSentenceType;
    reviewStepHeading.hidden = isSentenceType;
    resultBackBtn.hidden = true;
    resultQuestionsColumn.hidden = isSentenceType;
    generateMoreSentencesBtn.hidden = isImageSentence || isBits;
    resultSentencesLabel.textContent = isBits
      ? "Palabras para revisar"
      : isImageSentence
        ? "Oraciones con imágenes para revisar"
        : "Oraciones para revisar";
    resultSentencesNote.textContent = isBits
      ? "Corrige cada palabra; usa ✕ para quitar la que no quieras guardar."
      : isImageSentence
        ? "Corrige cada oración y sus dos sustantivos; usa ✕ para quitar la que no quieras guardar."
        : "Corrige cada oración; usa ✕ para quitar la que no quieras guardar.";
    if (addSentenceBtn) {
      addSentenceBtn.textContent = isBits ? "+ Agregar palabra" : "+ Agregar oración";
    }
    resultDoneBtn.textContent = (isImageSentence || isBits)
      ? "Siguiente: diseñar imágenes"
      : "Continuar";
    openReviewStep("text");
  }

  function updateUploadButtonState() {
    uploadStartBtn.disabled = !selectedFile;
  }

  function resetUploadForm() {
    resetImageDesignState();
    resetBitsDesignState();
    selectedFile = null;
    uploadTitleInput.value = "";
    filterUploadTemas();
    uploadFileInput.value = "";
    dropzoneText.textContent = "Arrastra el archivo aquí o haz clic para seleccionar";
    setUploadType("cuento");
  }

  function openUpload() {
    resetUploadForm();
    uploadOverlay.classList.add("is-open");
  }

  function closeUpload() {
    uploadOverlay.classList.remove("is-open");
  }

  // "Modo editor": salta el documento y la IA -la docente escribe las
  // oraciones ella misma. Reutiliza la misma pantalla de revisión que ya
  // usan la extracción y la generación con IA (mismo botón "+ Agregar
  // oración", mismo guardado para "oracion"); para "oracion_imagen" activa
  // además los cuadros de subir imagen de appendImageSentenceItem y guarda
  // directo con ellas, sin pasar por el diseño con IA (ver
  // saveManualImageSentenceMaterial).
  function openManualEditor(type) {
    // Si no escribió un nombre en el modal de arriba, arranca con uno
    // genérico -el título ahora también se edita aquí mismo (ver
    // resultModalTitle), así que no hace falta que quede vacío.
    currentMaterialTitle = uploadTitleInput.value.trim() || "Nuevo material";
    currentTargetDurationMinutes = null;
    configureResultModalForType(type);
    resetResultState();
    manualEntryMode = true;
    resultSentencesNote.hidden = true;

    if (type === "oracion_imagen") {
      manualImageEntryMode = true;
      renderImageSentenceRows([]);
      appendImageSentenceItem({}, { focus: true });
      resultSubtitle.textContent = "Modo editor - escribe cada oración, sus dos palabras y sube tú misma las imágenes que las reemplazarán";
      resultDoneBtn.textContent = "Continuar";
    } else if (type === "bits") {
      manualBitsEntryMode = true;
      renderBitsRows([]);
      appendBitsItem({}, { focus: true });
      resultSubtitle.textContent = "Modo editor - escribe cada palabra y sube tú misma la imagen que la representará";
      resultDoneBtn.textContent = "Continuar";
    } else {
      lastSentenceContext = { topic: currentMaterialTitle, grade_level: "" };
      renderSentences([]);
      appendSentenceItem("", { focus: true });
      resultSubtitle.textContent = "Modo editor · escribe cada oración";
    }

    updateDoneButtonState();
    closeUpload();
    resultOverlay.classList.add("is-open");
  }

  function setSelectedFile(file) {
    if (!file) return;
    selectedFile = file;
    dropzoneText.textContent = file.name;
    updateUploadButtonState();
  }

  uploadOpenBtn.addEventListener("click", openUpload);
  uploadCancelBtn.addEventListener("click", closeUpload);
  uploadTypeButtons.forEach((btn) => {
    btn.addEventListener("click", () => setUploadType(btn.dataset.type));
  });

  manualEditorOpenBtn.addEventListener("click", () => {
    openManualEditor(currentUploadType);
  });

  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropzone.classList.add("is-dragover");
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("is-dragover");
  });

  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragover");
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    setSelectedFile(file);
  });

  uploadFileInput.addEventListener("change", () => {
    setSelectedFile(uploadFileInput.files && uploadFileInput.files[0]);
  });

  function showLoading(message = "Extrayendo texto y generando resumen con IA...") {
    loadingSpinner.hidden = false;
    loadingText.textContent = message;
    loadingCloseBtn.hidden = true;
    loadingOverlay.classList.add("is-open");
  }

  function showLoadingError(message) {
    loadingSpinner.hidden = true;
    loadingText.textContent = message;
    loadingCloseBtn.hidden = false;
  }

  async function saveSentenceMaterial(sentences) {
    const formData = new FormData();
    formData.append("tipo_material", "oracion");
    formData.append("title", currentMaterialTitle);
    formData.append("sentences_json", JSON.stringify(sentences));
    formData.append("id_periodo", uploadPeriodoSelect.value);
    formData.append("id_tema", uploadTemaSelect.value);

    const response = await authorizedFetch("/api/material/save", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "No se pudieron guardar las oraciones.");
    }
  }

  async function saveDesignedImageSentenceMaterial() {
    const formData = new FormData();
    formData.append("tipo_material", "oracion_imagen");
    formData.append("title", currentMaterialTitle);
    formData.append("staging_token", imageDesignToken);
    formData.append("sentences_json", JSON.stringify(designSentences.map((sentence) => ({
      texto: sentence.texto,
      sustantivos: sentence.sustantivos.map((noun) => noun.palabra),
      staging_index: sentence.staging_index,
    }))));
    formData.append("id_periodo", uploadPeriodoSelect.value);
    formData.append("id_tema", uploadTemaSelect.value);

    const response = await authorizedFetch("/api/material/save", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "No se pudieron guardar las oraciones con imágenes.");
    }
    return data;
  }

  // Modo editor de "oracion_imagen": a diferencia de saveDesignedImageSentenceMaterial
  // (que aprueba un diseño ya generado en el servidor, identificado por
  // staging_token), aquí las imágenes nunca pasaron por el servidor -viven
  // solo como File en cada fila (ver buildNounImagePicker)- así que se
  // envían junto con el resto en el mismo POST. `rowIndex` viaja como
  // "staging_index" -mismo nombre que ya lee /api/material/save para
  // volver a relacionar cada oración con sus imágenes aunque el servidor
  // descarte alguna fila duplicada al normalizar.
  async function saveManualImageSentenceMaterial(items) {
    const formData = new FormData();
    formData.append("tipo_material", "oracion_imagen");
    formData.append("title", currentMaterialTitle);
    formData.append("sentences_json", JSON.stringify(items.map((item) => ({
      texto: item.texto,
      sustantivos: item.sustantivos,
      staging_index: item.rowIndex,
    }))));
    formData.append("id_periodo", uploadPeriodoSelect.value);
    formData.append("id_tema", uploadTemaSelect.value);
    items.forEach((item) => {
      item.files.forEach((file, nounIndex) => {
        formData.append(`imagen_${item.rowIndex}_${nounIndex}`, file, file.name);
      });
    });

    const response = await authorizedFetch("/api/material/save", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "No se pudieron guardar las oraciones con imágenes.");
    }
    return data;
  }

  async function saveStoryMaterial(transcribedText, summaryText, questionsData) {
    const formData = new FormData();
    formData.append("tipo_material", "cuento");
    formData.append("scenes_token", scenesToken);
    formData.append("title", currentMaterialTitle);
    formData.append("transcribed_text", transcribedText);
    formData.append("summary_text", summaryText);
    formData.append("questions_json", JSON.stringify(questionsData));
    formData.append("id_periodo", uploadPeriodoSelect.value);
    formData.append("id_tema", uploadTemaSelect.value);
    if (currentTargetDurationMinutes !== null) {
      formData.append("target_duration_minutes", String(currentTargetDurationMinutes));
    }

    const response = await authorizedFetch("/api/material/save", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "No se pudo guardar el material.");
    }
    return data;
  }

  async function prepareImageSentenceDesign(items) {
    resetImageDesignState();
    resultOverlay.classList.remove("is-open");
    showLoading("Generando las imágenes con IA, puede tardar un momento");

    try {
      const response = await authorizedFetch("/api/material/image-sentences/prepare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: currentMaterialTitle, items }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudieron generar las imágenes.");
      }

      imageDesignToken = typeof data.token === "string" ? data.token : "";
      designSentences = (Array.isArray(data.items) ? data.items : []).map((item) => ({
        ...item,
        staging_index: item.index,
        sustantivos: Array.isArray(item.sustantivos)
          ? item.sustantivos.map((noun) => ({ ...noun }))
          : [],
      }));
      if (!imageDesignToken || !designSentences.length) {
        throw new Error("No se recibió un diseño válido para las oraciones.");
      }

      currentDesignIndex = 0;
      renderDesignSentence();
      imageDesignTitle.textContent = currentMaterialTitle;
      imageDesignOverlay.classList.add("is-open");
    } catch (error) {
      resetImageDesignState();
      resultOverlay.classList.add("is-open");
      throw error;
    } finally {
      loadingOverlay.classList.remove("is-open");
    }
  }

  async function saveDesignedBitsMaterial() {
    const formData = new FormData();
    formData.append("tipo_material", "bits");
    formData.append("title", currentMaterialTitle);
    formData.append("staging_token", bitsDesignToken);
    formData.append("bits_json", JSON.stringify(designBits.map((bit) => ({
      palabra: bit.palabra,
      pregunta: bit.pregunta || "",
      staging_index: bit.staging_index,
    }))));
    formData.append("id_periodo", uploadPeriodoSelect.value);
    formData.append("id_tema", uploadTemaSelect.value);

    const response = await authorizedFetch("/api/material/save", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "No se pudieron guardar los bits.");
    }
    return data;
  }

  // Modo editor de "bits": mismo principio que saveManualImageSentenceMaterial
  // pero con una sola imagen por fila (sin sufijo de sustantivo en el nombre
  // del campo).
  async function saveManualBitsMaterial(items) {
    const formData = new FormData();
    formData.append("tipo_material", "bits");
    formData.append("title", currentMaterialTitle);
    formData.append("bits_json", JSON.stringify(items.map((item) => ({
      palabra: item.palabra,
      pregunta: item.pregunta || "",
      staging_index: item.rowIndex,
    }))));
    formData.append("id_periodo", uploadPeriodoSelect.value);
    formData.append("id_tema", uploadTemaSelect.value);
    items.forEach((item) => {
      if (item.file) {
        formData.append(`imagen_${item.rowIndex}`, item.file, item.file.name);
      }
    });

    const response = await authorizedFetch("/api/material/save", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "No se pudieron guardar los bits.");
    }
    return data;
  }

  async function prepareBitsDesign(items) {
    resetBitsDesignState();
    resultOverlay.classList.remove("is-open");
    showLoading("Generando las imágenes con IA, puede tardar un momento");

    try {
      const response = await authorizedFetch("/api/bits/prepare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: currentMaterialTitle, items }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudieron generar las imágenes.");
      }

      bitsDesignToken = typeof data.token === "string" ? data.token : "";
      designBits = (Array.isArray(data.items) ? data.items : []).map((item) => ({ ...item, staging_index: item.index }));
      if (!bitsDesignToken || !designBits.length) {
        throw new Error("No se recibió un diseño válido para los bits.");
      }

      currentBitsDesignIndex = 0;
      renderBitsDesignItem();
      bitsDesignTitle.textContent = currentMaterialTitle;
      bitsDesignOverlay.classList.add("is-open");
    } catch (error) {
      resetBitsDesignState();
      resultOverlay.classList.add("is-open");
      throw error;
    } finally {
      loadingOverlay.classList.remove("is-open");
    }
  }

  uploadStartBtn.addEventListener("click", async () => {
    if (!selectedFile) return;

    currentMaterialTitle = uploadTitleInput.value.trim() || selectedFile.name;
    const uploadType = currentUploadType;

    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("title", currentMaterialTitle);
    formData.append("tipo_material", uploadType);

    closeUpload();
    showLoading(uploadType === "oracion_imagen"
      ? "Identificando las oraciones con imagenes con IA..."
      : uploadType === "oracion"
        ? "Identificando las oraciones con IA..."
        : uploadType === "bits"
          ? "Identificando las palabras con IA..."
          : undefined);

    try {
      const response = await authorizedFetch("/api/material/process", {
        method: "POST",
        body: formData,
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || "No se pudo procesar el documento.");
      }

      currentTargetDurationMinutes = null;
      configureResultModalForType(uploadType);
      resetResultState();

      if (uploadType === "oracion_imagen") {
        renderImageSentenceRows(data.items || []);
        resultSubtitle.textContent = "A partir del documento original - revisa las oraciones y sus dos sustantivos antes de aprobar";
      } else if (uploadType === "bits") {
        renderBitsRows(data.items || []);
        resultSubtitle.textContent = "A partir del documento original - revisa las palabras antes de aprobar";
      } else if (uploadType === "oracion") {
        lastSentenceContext = { topic: currentMaterialTitle, grade_level: "" };
        renderSentences(data.sentences || []);
        resultSubtitle.textContent = "A partir del documento original · revisa las oraciones identificadas antes de aprobar";
      } else {
        resultTranscribedText.textContent = data.transcribed_text;
        resultSummaryText.textContent = data.summary_text;
        resultSubtitle.textContent = "A partir del documento original · revisa el contenido antes de aprobar";
        resultScenesBlock.hidden = false;
        updateDoneButtonState();
      }

      loadingOverlay.classList.remove("is-open");
      resultOverlay.classList.add("is-open");
    } catch (error) {
      showLoadingError(error.message || "No se pudo procesar el documento.");
    }
  });

  loadingCloseBtn.addEventListener("click", () => {
    loadingOverlay.classList.remove("is-open");
  });

  resultCancelBtn.addEventListener("click", () => {
    resultOverlay.classList.remove("is-open");
    resetResultState();
  });

  classifyBackBtn.addEventListener("click", () => {
    classifyOverlay.classList.remove("is-open");
    pendingSave?.backOverlay.classList.add("is-open");
  });

  classifySaveBtn.addEventListener("click", async () => {
    if (!pendingSave || classifySaveBtn.disabled) return;
    const originalLabel = classifySaveBtn.textContent;
    classifySaveBtn.disabled = true;
    classifyBackBtn.disabled = true;
    classifySaveBtn.textContent = "Guardando...";
    try {
      await pendingSave.run();
      classifyOverlay.classList.remove("is-open");
      window.location.reload();
    } catch (error) {
      alert(error.message || "No se pudo guardar el material.");
      classifySaveBtn.disabled = false;
      classifyBackBtn.disabled = false;
      classifySaveBtn.textContent = originalLabel;
    }
  });

  // El nombre del material se puede corregir aquí mismo, no solo en el
  // input inicial del modal de subida: currentMaterialTitle es lo que
  // finalmente viaja en el "title" del guardado (ver saveSentenceMaterial,
  // saveDesignedImageSentenceMaterial y el guardado del cuento más abajo),
  // así que basta con mantenerlo sincronizado con lo que quede escrito en
  // el título.
  resultModalTitle.addEventListener("input", () => {
    currentMaterialTitle = resultModalTitle.textContent.replace(/\s+/g, " ").trim();
  });

  // Mismo nombre editable en el paso de diseño de imágenes: saveDesignedImageSentenceMaterial
  // también lee currentMaterialTitle al guardar.
  imageDesignTitle.addEventListener("input", () => {
    currentMaterialTitle = imageDesignTitle.textContent.replace(/\s+/g, " ").trim();
  });

  // "Crear con IA" genera un cuento o un set de oraciones. Ambos borradores
  // caen en la misma pantalla de revisión/edición antes de crear el material.
  const storyTypeButtons = [storyTypeCuentoBtn, storyTypeOracionBtn, storyTypeOracionImagenBtn, storyTypeBitsBtn];
  const storyFieldGroups = {
    cuento: storyCuentoFields,
    oracion: storySentenceFields,
    oracion_imagen: storyImageFields,
    bits: storyBitsFields,
  };
  const storyCopy = {
    cuento: [
      "Crear un cuento con IA",
      "Define aquí los datos del cuento y la IA prepara un borrador editable.",
    ],
    oracion: [
      "Crear oraciones con IA",
      "Indica el tema y el nivel. La IA prepara un borrador de oraciones para revisar y editar antes de crear el material.",
    ],
    oracion_imagen: [
      "Crear oraciones con imágenes con IA",
      "Indica el tema y el nivel. La IA prepara un borrador de oraciones con una imagen para cada una, para revisar antes de crear el material.",
    ],
    bits: [
      "Crear bits con IA",
      "Escribe las sílabas y cuántas palabras quieres. La IA prepara un borrador para inicial de 5 años, para revisar antes de crear el material.",
    ],
  };

  function setStoryType(type) {
    currentStoryType = type;
    storyTypeButtons.forEach((btn) => {
      const active = btn.dataset.type === type;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", String(active));
    });
    Object.entries(storyFieldGroups).forEach(([key, group]) => {
      const active = key === type;
      group.hidden = !active;
      // Deshabilitar el grupo oculto lo excluye de la validación nativa del form.
      group.querySelectorAll("input, textarea").forEach((el) => { el.disabled = !active; });
    });
    const [title, description] = storyCopy[type];
    storyModalTitle.textContent = title;
    storyModalDescription.textContent = description;
  }

  storyTypeButtons.forEach((btn) => {
    btn.addEventListener("click", () => setStoryType(btn.dataset.type));
  });

  // Sílabas de "Bits": la docente escribe una lista libre separada por comas
  // o espacios (ej. "ma, me, mi"); las palabras generadas empezarán con
  // alguna de ellas.
  function selectedBitsSyllables() {
    return bitsSyllablesInput.value
      .split(/[,\s]+/)
      .map((piece) => piece.trim())
      .filter(Boolean);
  }

  function resetBitsSyllables() {
    bitsSyllablesInput.value = "";
    bitsSyllablesError.hidden = true;
  }

  bitsSyllablesInput.addEventListener("input", () => {
    if (selectedBitsSyllables().length) bitsSyllablesError.hidden = true;
  });

  storyOpenBtn.addEventListener("click", () => {
    resetImageDesignState();
    resetBitsDesignState();
    storyForm.reset();
    resetBitsSyllables();
    setStoryType("cuento");
    storyOverlay.classList.add("is-open");
  });

  storyCancelBtn.addEventListener("click", () => {
    storyOverlay.classList.remove("is-open");
  });

  // "Oraciones con imágenes": cada oración conserva los dos sustantivos
  // concretos que más adelante se reemplazarán por imágenes.
  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function markNouns(texto, nouns) {
    const source = String(texto || "");
    const escapedNouns = Array.from(new Set(
      (nouns || [])
        .map((noun) => String(noun || "").trim())
        .filter(Boolean)
    ))
      .sort((a, b) => b.length - a.length)
      .map((noun) => noun.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!escapedNouns.length) return escapeHtml(source);

    const matcher = new RegExp(escapedNouns.join("|"), "gi");
    let html = "";
    let cursor = 0;
    let match = matcher.exec(source);
    while (match) {
      html += escapeHtml(source.slice(cursor, match.index));
      html += `<mark>${escapeHtml(match[0])}</mark>`;
      cursor = match.index + match[0].length;
      match = matcher.exec(source);
    }
    html += escapeHtml(source.slice(cursor));
    return html;
  }

  storyForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (currentStoryType === "oracion_imagen") {
      const imagePayload = {
        topic: document.getElementById("sentenceImageTopic").value.trim(),
        grade_level: document.getElementById("sentenceImageGrade").value.trim(),
        count: Number.parseInt(document.getElementById("sentenceImageCount").value, 10),
        extra_details: document.getElementById("sentenceImageDetails").value.trim(),
      };

      storyGenerateBtn.disabled = true;
      storyGenerateBtn.textContent = "Creando…";
      storyOverlay.classList.remove("is-open");
      showLoading();
      loadingText.textContent = "Generando oraciones con dos sustantivos…";

      try {
        const response = await authorizedFetch("/api/sentences/generate-images", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(imagePayload),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "No se pudieron generar las oraciones.");
        }
        currentMaterialTitle = data.title;
        currentTargetDurationMinutes = null;
        configureResultModalForType("oracion_imagen");
        resetResultState();
        renderImageSentenceRows(data.items || []);
        resultSubtitle.textContent = "Borrador generado por IA - revisa las oraciones y sus dos sustantivos antes de crear el material";
        loadingOverlay.classList.remove("is-open");
        resultOverlay.classList.add("is-open");
      } catch (error) {
        showLoadingError(error.message || "No se pudieron generar las oraciones.");
      } finally {
        storyGenerateBtn.disabled = false;
        storyGenerateBtn.textContent = "Generar borrador";
      }
      return;
    }

    if (currentStoryType === "oracion") {
      const sentencePayload = {
        topic: document.getElementById("sentenceTopic").value.trim(),
        grade_level: document.getElementById("sentenceGrade").value.trim(),
        count: Number.parseInt(document.getElementById("sentenceCount").value, 10),
        extra_details: document.getElementById("sentenceDetails").value.trim(),
      };

      storyGenerateBtn.disabled = true;
      storyGenerateBtn.textContent = "Creando…";
      storyOverlay.classList.remove("is-open");
      showLoading();
      loadingText.textContent = "Generando un borrador de oraciones con IA…";

      try {
        const response = await authorizedFetch("/api/sentences/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sentencePayload),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "No se pudieron generar las oraciones.");
        }

        currentMaterialTitle = data.title;
        currentTargetDurationMinutes = null;
        lastSentenceContext = {
          topic: sentencePayload.topic,
          grade_level: sentencePayload.grade_level,
        };
        configureResultModalForType("oracion");
        resetResultState();
        renderSentences(data.sentences || []);
        resultSubtitle.textContent = "Borrador generado por IA · revisa y edita las oraciones antes de crear el material";
        loadingOverlay.classList.remove("is-open");
        resultOverlay.classList.add("is-open");
      } catch (error) {
        showLoadingError(error.message || "No se pudieron generar las oraciones.");
      } finally {
        storyGenerateBtn.disabled = false;
        storyGenerateBtn.textContent = "Generar borrador";
      }
      return;
    }

    if (currentStoryType === "bits") {
      const silabas = selectedBitsSyllables();
      if (!silabas.length) {
        bitsSyllablesError.hidden = false;
        bitsSyllablesInput.focus();
        return;
      }
      const bitsPayload = {
        silabas,
        count: Number.parseInt(bitsCountInput.value, 10),
        syllable_count: bitsSyllableCountInput.value.trim(),
        extra_details: bitsDetailsInput.value.trim(),
      };

      storyGenerateBtn.disabled = true;
      storyGenerateBtn.textContent = "Creando…";
      storyOverlay.classList.remove("is-open");
      showLoading();
      loadingText.textContent = "Generando palabras con IA…";

      try {
        const response = await authorizedFetch("/api/bits/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(bitsPayload),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "No se pudieron generar las palabras.");
        }
        currentMaterialTitle = data.title;
        currentTargetDurationMinutes = null;
        configureResultModalForType("bits");
        resetResultState();
        renderBitsRows(data.items || []);
        resultSubtitle.textContent = "Borrador generado por IA - revisa las palabras antes de crear el material";
        loadingOverlay.classList.remove("is-open");
        resultOverlay.classList.add("is-open");
      } catch (error) {
        showLoadingError(error.message || "No se pudieron generar las palabras.");
      } finally {
        storyGenerateBtn.disabled = false;
        storyGenerateBtn.textContent = "Generar borrador";
      }
      return;
    }

    const payload = {
      character: document.getElementById("storyCharacter").value.trim(),
      setting: document.getElementById("storySetting").value.trim(),
      grade_level: document.getElementById("storyGrade").value.trim(),
      objective: document.getElementById("storyObjective").value.trim(),
      extra_details: document.getElementById("storyDetails").value.trim(),
      duration_minutes: Number.parseInt(document.getElementById("storyDuration").value, 10),
    };

    storyGenerateBtn.disabled = true;
    storyGenerateBtn.textContent = "Creando…";
    storyOverlay.classList.remove("is-open");
    showLoading();
    loadingText.textContent = "Creando un cuento con IA…";

    try {
      const response = await authorizedFetch("/api/story/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "No se pudo crear el cuento.");
      }

      currentMaterialTitle = data.title;
      currentTargetDurationMinutes = data.target_duration_minutes;
      resultTranscribedText.textContent = data.story;
      resultSummaryText.textContent = data.summary;
      configureResultModalForType("cuento");
      resultSubtitle.textContent = `Creado para ${data.target_duration_minutes} min · ${data.word_count} palabras · la miss puede editarlo antes de aprobar`;
      resetResultState();
      resultScenesBlock.hidden = false;
      loadingOverlay.classList.remove("is-open");
      resultOverlay.classList.add("is-open");
    } catch (error) {
      showLoadingError(error.message || "No se pudo crear el cuento.");
    } finally {
      storyGenerateBtn.disabled = false;
      storyGenerateBtn.textContent = "Generar borrador";
    }
  });

  // El contenido en sí ya queda aprobado aquí -el guardado real (con
  // periodo y tema) pasa a classifyOverlay (ver openClassifyModal), un paso
  // aparte que se abre en vez de guardar directo.
  resultDoneBtn.addEventListener("click", async () => {
    // En un cuento, "Siguiente" pasa a la pantalla que sigue; solo la última
    // ("Continuar") aprueba el contenido y abre la clasificación.
    if (isReviewWizard() && reviewStepKey !== "questions") {
      goToReviewStep(1);
      return;
    }
    if (currentResultType === "oracion_imagen") {
      const items = getImageSentencesData();
      if (!items.length) {
        alert("Agrega al menos una oración con dos sustantivos.");
        return;
      }
      if (manualImageEntryMode) {
        // Modo editor: nada de diseño con IA -las imágenes ya las subió la
        // docente en la misma revisión (ver getImageSentencesData).
        openClassifyModal(resultOverlay, () => saveManualImageSentenceMaterial(items));
        return;
      }
      if (items.length > 20) {
        alert("Puedes diseñar un máximo de 20 oraciones a la vez.");
        return;
      }
      const originalLabel = resultDoneBtn.textContent;
      resultDoneBtn.disabled = true;
      resultDoneBtn.textContent = "Generando imágenes...";
      try {
        await prepareImageSentenceDesign(items);
      } catch (error) {
        alert(error.message || "No se pudieron generar las imágenes.");
      } finally {
        resultDoneBtn.textContent = originalLabel;
        updateDoneButtonState();
      }
      return;
    }

    if (currentResultType === "bits") {
      const items = getBitsData();
      if (!items.length) {
        alert("Agrega al menos una palabra.");
        return;
      }
      if (manualBitsEntryMode) {
        // Modo editor: nada de diseño con IA -las imágenes ya las subió la
        // docente en la misma revisión (ver getBitsData).
        openClassifyModal(resultOverlay, () => saveManualBitsMaterial(items));
        return;
      }
      if (items.length > 20) {
        alert("Puedes diseñar un máximo de 20 bits a la vez.");
        return;
      }
      const originalLabel = resultDoneBtn.textContent;
      resultDoneBtn.disabled = true;
      resultDoneBtn.textContent = "Generando imágenes...";
      try {
        await prepareBitsDesign(items);
      } catch (error) {
        alert(error.message || "No se pudieron generar las imágenes.");
      } finally {
        resultDoneBtn.textContent = originalLabel;
        updateDoneButtonState();
      }
      return;
    }

    if (currentResultType === "oracion") {
      const sentences = getSentencesData();
      if (!sentences.length) {
        alert("No hay oraciones para guardar.");
        return;
      }
      openClassifyModal(resultOverlay, () => saveSentenceMaterial(sentences));
      return;
    }

    const transcribedText = resultTranscribedText.textContent.trim();
    const summaryText = resultSummaryText.textContent.trim();
    const questionsData = getQuestionsData();

    if (!transcribedText || !summaryText) {
      alert("Falta el texto completo o el resumen.");
      return;
    }
    if (!questionsData.length) {
      alert("Genera las preguntas antes de guardar.");
      return;
    }
    if (questionsData.some((question) => !question.respuesta_esperada)) {
      alert("Revisa y completa la respuesta esperada de cada pregunta.");
      return;
    }
    if (!scenesReadyToSave()) {
      alert("Genera las escenas (imágenes y audios) antes de guardar.");
      return;
    }
    openClassifyModal(resultOverlay, () => saveStoryMaterial(transcribedText, summaryText, questionsData));
  });

  // Duración de un audio de escena en texto legible (p. ej. «1 min 05 s»).
  function formatAudioDuration(seconds) {
    if (!Number.isFinite(seconds)) return "";
    const roundedSeconds = Math.max(0, Math.round(seconds));
    const minutes = Math.floor(roundedSeconds / 60);
    const remainder = roundedSeconds % 60;
    return minutes ? `${minutes} min ${String(remainder).padStart(2, "0")} s` : `${remainder} s`;
  }

  // --- Escenas ilustradas del cuento generado con IA -------------------------
  // Flujo: /api/story/scenes/plan (la IA decide cuántas escenas) y después una
  // petición /api/story/scenes/image y otra /api/story/scenes/audio por escena,
  // para mostrar el avance de cada ilustración y su narración.
  const SCENES_BUTTON_LABEL = generateScenesBtn.textContent;

  function setScenesProgress({ text, done = 0, total = 0, indeterminate = false, finished = false }) {
    const percent = total ? Math.round((done / total) * 100) : 0;
    scenesProgress.hidden = false;
    scenesProgress.classList.toggle("scenes-progress--indeterminate", indeterminate);
    scenesProgress.classList.toggle("scenes-progress--done", finished);
    scenesProgressText.textContent = text;
    scenesProgressCount.textContent = total ? `${done} de ${total}` : "";
    scenesProgressFill.style.width = indeterminate ? "" : `${percent}%`;
    if (indeterminate) {
      scenesProgressTrack.removeAttribute("aria-valuenow");
    } else {
      scenesProgressTrack.setAttribute("aria-valuenow", String(percent));
    }
  }

  function isSceneComplete(scene) {
    return scene.state === "done" && scene.audioState === "done";
  }

  function scenesReadyToSave() {
    return scenes.length > 0 && scenes.every(isSceneComplete) && !scenesBusy && scenesStale.hidden;
  }

  function setScenesBusy(busy) {
    scenesBusy = busy;
    updateDoneButtonState();
    generateScenesBtn.disabled = busy;
    scenesTone.disabled = busy;
    scenesGrid.querySelectorAll(".scene-card__action, .scene-card__edit").forEach((btn) => {
      btn.disabled = busy || Boolean(btn.closest(".scene-card--pending, .scene-card--loading"));
    });
  }

  function buildSceneCard(scene) {
    const card = document.createElement("li");
    card.className = `scene-card scene-card--${scene.state}`;
    card.tabIndex = -1;

    const media = document.createElement("div");
    media.className = "scene-card__media";
    if (scene.state === "done" && scene.url) {
      const img = document.createElement("img");
      img.src = scene.url;
      img.alt = `Ilustración de la escena ${scene.index + 1}: ${scene.texto}`;
      media.appendChild(img);

      // Capa sobre la imagen: "Editar imagen" al pasar el cursor o enfocarla.
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "scene-card__edit";
      edit.setAttribute("aria-label", `Editar imagen de la escena ${scene.index + 1}`);
      edit.disabled = scenesBusy;
      const editLabel = document.createElement("span");
      editLabel.className = "scene-card__edit-label";
      editLabel.setAttribute("aria-hidden", "true");
      editLabel.textContent = "✏️ Editar imagen";
      edit.appendChild(editLabel);
      edit.addEventListener("click", () => openSceneEdit(scene.index));
      media.appendChild(edit);
    } else {
      const status = document.createElement("div");
      status.className = "scene-card__status";
      status.textContent = {
        pending: "En espera",
        loading: "Dibujando…",
        error: scene.error || "No se pudo dibujar",
      }[scene.state] || "";
      media.appendChild(status);
    }
    const badge = document.createElement("span");
    badge.className = "scene-card__badge";
    badge.textContent = String(scene.index + 1);
    media.appendChild(badge);
    card.appendChild(media);

    const body = document.createElement("div");
    body.className = "scene-card__body";
    const text = document.createElement("p");
    text.className = "scene-card__text";
    text.textContent = scene.texto;
    body.appendChild(text);
    if (scene.audioState === "done") {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "none";
      audio.className = "scene-card__audio";
      audio.src = scene.audioUrl;
      audio.setAttribute("aria-label", `Audio de la escena ${scene.index + 1}`);
      body.appendChild(audio);
      if (Number.isFinite(scene.audioDuration)) {
        const duration = document.createElement("span");
        duration.className = "scene-card__audio-meta";
        duration.textContent = `Duración: ${formatAudioDuration(scene.audioDuration)}`;
        body.appendChild(duration);
      }
      if (scene.audioNotice) {
        const notice = document.createElement("p");
        notice.className = "scene-card__audio-meta scene-card__audio-meta--error";
        notice.textContent = scene.audioNotice;
        body.appendChild(notice);
      }
      // La voz puede sonar distinta en cada intento: la docente escucha el audio
      // y, si no le gusta, lo vuelve a generar sin tocar la imagen.
      const regen = document.createElement("button");
      regen.type = "button";
      regen.className = "btn btn--ghost scene-card__action scene-card__regen";
      regen.textContent = "🔄 Volver a generar audio";
      regen.setAttribute("aria-label", `Volver a generar el audio de la escena ${scene.index + 1}`);
      regen.disabled = scenesBusy;
      regen.addEventListener("click", () => regenerateScene(scene.index, { forceAudio: true }));
      body.appendChild(regen);
    } else if (scene.audioState === "loading" || scene.audioState === "error") {
      const status = document.createElement("p");
      status.className = "scene-card__audio-meta";
      status.classList.toggle("scene-card__audio-meta--error", scene.audioState === "error");
      status.textContent = scene.audioState === "loading"
        ? "Narrando…"
        : scene.audioError || "No se pudo narrar esta escena.";
      body.appendChild(status);
    }
    // Las escenas ya dibujadas se editan desde la capa de la imagen; el botón
    // solo aparece cuando una falló y hay que volver a intentarla.
    if (scene.state === "error" || scene.audioState === "error") {
      const action = document.createElement("button");
      action.type = "button";
      action.className = "btn btn--ghost scene-card__action";
      action.textContent = "Reintentar";
      action.setAttribute("aria-label", `Reintentar escena ${scene.index + 1}`);
      action.disabled = scenesBusy;
      action.addEventListener("click", () => regenerateScene(scene.index));
      body.appendChild(action);
    }
    card.appendChild(body);
    return card;
  }

  function updateSceneCard(index) {
    const current = scenesGrid.children[index];
    const next = buildSceneCard(scenes[index]);
    if (current) {
      // Se conserva el <li> (y con él el foco del teclado) y solo se cambia su contenido.
      current.className = next.className;
      current.replaceChildren(...next.childNodes);
    } else {
      scenesGrid.appendChild(next);
    }
  }

  function finishScenesProgress() {
    const done = scenes.filter(isSceneComplete).length;
    const incomplete = scenes.length - done;
    setScenesProgress({
      text: incomplete
        ? `Listo, pero ${incomplete === 1 ? "1 escena quedó incompleta" : `${incomplete} escenas quedaron incompletas`}. Usa «Reintentar».`
        : "Escenas listas. Cada una tiene su imagen y su audio; edita una imagen pasando el cursor (o tocándola) o vuelve a generar su audio.",
      done,
      total: scenes.length,
      finished: scenes.length > 0 && incomplete === 0 && scenesStale.hidden,
    });
  }

  // Devuelve false si la respuesta llegó tarde (se cerró el modal o se volvió
  // a generar) y por eso se descartó.
  async function drawScene(index, runId) {
    const scene = scenes[index];
    scene.state = "loading";
    scene.error = "";
    updateSceneCard(index);
    try {
      const response = await authorizedFetch("/api/story/scenes/image", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: scenesToken, index }),
      });
      const data = await response.json().catch(() => ({}));
      if (runId !== scenesRunId) return false;
      if (!response.ok) {
        throw new Error(data.error || "No se pudo dibujar esta escena.");
      }
      scene.url = data.imagen_url;
      scene.state = "done";
    } catch (error) {
      if (runId !== scenesRunId) return false;
      scene.state = "error";
      scene.error = error.message || "No se pudo dibujar esta escena.";
    }
    updateSceneCard(index);
    paintReviewState();
    return true;
  }

  async function generateSceneAudio(index, runId) {
    const scene = scenes[index];
    // Al volver a narrar una escena que ya tenía audio, si falla se conserva el
    // anterior: el servidor solo reemplaza el archivo cuando la nueva narración sale bien.
    const previous = scene.audioState === "done"
      ? { url: scene.audioUrl, duration: scene.audioDuration }
      : null;
    scene.audioState = "loading";
    scene.audioError = "";
    scene.audioNotice = "";
    updateSceneCard(index);
    paintReviewState();
    try {
      const response = await authorizedFetch("/api/story/scenes/audio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: scenesToken, index }),
      });
      const data = await response.json().catch(() => ({}));
      if (runId !== scenesRunId) return false;
      if (!response.ok) {
        throw new Error(data.error || "No se pudo narrar esta escena.");
      }
      scene.audioUrl = data.audio_url;
      scene.audioDuration = Number.isFinite(data.duration_seconds) ? data.duration_seconds : null;
      scene.audioState = "done";
    } catch (error) {
      if (runId !== scenesRunId) return false;
      const message = error.message || "No se pudo narrar esta escena.";
      if (previous) {
        scene.audioUrl = previous.url;
        scene.audioDuration = previous.duration;
        scene.audioState = "done";
        scene.audioNotice = `${message} Se conserva el audio anterior.`;
      } else {
        scene.audioState = "error";
        scene.audioError = message;
      }
    }
    updateSceneCard(index);
    paintReviewState();
    return true;
  }

  async function generateScenes() {
    if (scenesBusy) return;
    const storyText = resultTranscribedText.textContent.trim();
    if (!storyText) {
      alert("Escribe o genera el cuento antes de crear las escenas.");
      return;
    }

    const tone = scenesTone.value;
    resetScenes();
    const runId = scenesRunId;
    scenesStoryText = storyText;
    scenesToneUsed = tone;
    setScenesBusy(true);
    setScenesProgress({ text: "Analizando el cuento y decidiendo las escenas…", indeterminate: true });

    try {
      const response = await authorizedFetch("/api/story/scenes/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ story: storyText, tone }),
      });
      const data = await response.json().catch(() => ({}));
      if (runId !== scenesRunId) return;
      if (!response.ok) {
        throw new Error(data.error || "No se pudieron planear las escenas.");
      }
      scenesToken = data.token;
      scenes = (data.scenes || []).map((scene) => ({
        index: scene.index,
        texto: scene.texto,
        url: "",
        state: "pending",
        error: "",
        audioState: "pending",
        audioUrl: "",
        audioDuration: null,
        audioError: "",
      }));
      scenesGrid.replaceChildren(...scenes.map(buildSceneCard));
      // Con las tarjetas ya en el DOM el panel tiene su altura completa: se
      // acerca a la vista para que la docente vea aparecer las escenas.
      if (resultScenesBlock.open) {
        resultScenesBlock.scrollIntoView({
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
          block: "start",
        });
      }

      const toneLabel = data.tone_label ? ` · tono ${data.tone_label.toLowerCase()}` : "";
      for (let i = 0; i < scenes.length; i += 1) {
        setScenesProgress({
          text: `Escena ${i + 1} de ${scenes.length}: dibujando${toneLabel}…`,
          done: scenes.filter(isSceneComplete).length,
          total: scenes.length,
        });
        if (!(await drawScene(i, runId))) return;
        if (scenes[i].state === "done") {
          setScenesProgress({
            text: `Escena ${i + 1} de ${scenes.length}: narrando…`,
            done: scenes.filter(isSceneComplete).length,
            total: scenes.length,
          });
          if (!(await generateSceneAudio(i, runId))) return;
        }
      }
      finishScenesProgress();
      generateScenesBtn.textContent = "✨ Generar de nuevo";
    } catch (error) {
      if (runId !== scenesRunId) return;
      setScenesProgress({ text: error.message || "No se pudieron crear las escenas." });
    } finally {
      if (runId === scenesRunId) setScenesBusy(false);
    }
  }

  // Rehace lo que falta de una escena (imagen y/o audio). Con `forceAudio` vuelve
  // a narrarla aunque ya tenga audio.
  async function regenerateScene(index, { forceAudio = false } = {}) {
    if (scenesBusy) return;
    const runId = scenesRunId;
    // Al deshabilitarse el botón el foco se perdería: se guarda en la tarjeta
    // y se devuelve al botón al terminar, salvo que la docente ya haya navegado.
    const card = scenesGrid.children[index];
    if (card?.contains(document.activeElement)) {
      card.focus({ preventScroll: true });
    }
    setScenesBusy(true);
    try {
      if (scenes[index].state !== "done") {
        setScenesProgress({
          text: `Escena ${index + 1} de ${scenes.length}: dibujando…`,
          done: scenes.filter(isSceneComplete).length,
          total: scenes.length,
        });
        if (!(await drawScene(index, runId))) return;
      }
      if (scenes[index].state === "done" && (forceAudio || scenes[index].audioState !== "done")) {
        setScenesProgress({
          text: `Escena ${index + 1} de ${scenes.length}: narrando${forceAudio ? " de nuevo" : ""}…`,
          done: scenes.filter(isSceneComplete).length,
          total: scenes.length,
        });
        if (!(await generateSceneAudio(index, runId))) return;
      }
      finishScenesProgress();
    } finally {
      if (runId === scenesRunId) {
        setScenesBusy(false);
        if (document.activeElement === card && resultOverlay.classList.contains("is-open")) {
          const target = (forceAudio && card.querySelector(".scene-card__regen"))
            || card.querySelector(".scene-card__action, .scene-card__edit");
          target?.focus({ preventScroll: true });
        }
      }
    }
  }

  // Modal "Editar imagen": se abre encima del de revisión (que no cambia) y al
  // cerrarse se vuelve a él. Mientras la IA edita no se puede cerrar, para que
  // la imagen del servidor y la que se ve nunca queden desfasadas.
  const SCENE_EDIT_APPLY_LABEL = sceneEditApplyBtn.textContent;

  function openSceneEdit(index) {
    const scene = scenes[index];
    if (scenesBusy || !scene || scene.state !== "done") return;
    sceneEditIndex = index;
    sceneEditTitle.textContent = `Editar imagen · Escena ${index + 1}`;
    sceneEditImage.src = scene.url;
    sceneEditImage.alt = `Ilustración actual de la escena ${index + 1}`;
    sceneEditContext.textContent = scene.texto;
    sceneEditInstruction.value = "";
    sceneEditError.hidden = true;
    sceneEditOverlay.classList.add("is-open");
    sceneEditInstruction.focus();
  }

  function closeSceneEdit() {
    if (sceneEditBusy) return;
    const index = sceneEditIndex;
    sceneEditIndex = -1;
    sceneEditOverlay.classList.remove("is-open");
    // Devuelve el foco a la imagen que se estaba editando.
    scenesGrid.children[index]?.querySelector(".scene-card__edit")?.focus({ preventScroll: true });
  }

  async function applySceneEdit() {
    const index = sceneEditIndex;
    const instruction = sceneEditInstruction.value.trim();
    if (index < 0 || !instruction || sceneEditBusy) return;

    const runId = scenesRunId;
    sceneEditBusy = true;
    sceneEditError.hidden = true;
    sceneEditInstruction.disabled = true;
    sceneEditCancelBtn.disabled = true;
    sceneEditApplyBtn.disabled = true;
    sceneEditApplyBtn.textContent = "Editando la imagen…";

    let saved = false;
    try {
      const response = await authorizedFetch("/api/story/scenes/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: scenesToken, index, instruction }),
      });
      const data = await response.json().catch(() => ({}));
      if (runId !== scenesRunId) return;
      if (!response.ok) {
        throw new Error(data.error || "No se pudo editar la imagen.");
      }
      scenes[index].url = data.imagen_url;
      updateSceneCard(index);
      saved = true;
    } catch (error) {
      if (runId !== scenesRunId) return;
      sceneEditError.textContent = error.message || "No se pudo editar la imagen.";
      sceneEditError.hidden = false;
    } finally {
      sceneEditBusy = false;
      sceneEditInstruction.disabled = false;
      sceneEditCancelBtn.disabled = false;
      sceneEditApplyBtn.disabled = false;
      sceneEditApplyBtn.textContent = SCENE_EDIT_APPLY_LABEL;
    }
    if (saved) {
      closeSceneEdit();
    } else {
      sceneEditInstruction.focus();
    }
  }

  sceneEditForm.addEventListener("submit", (event) => {
    event.preventDefault();
    applySceneEdit();
  });
  sceneEditCancelBtn.addEventListener("click", closeSceneEdit);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sceneEditOverlay.classList.contains("is-open")) {
      closeSceneEdit();
    }
  });

  function resetScenes() {
    sceneEditBusy = false;
    sceneEditIndex = -1;
    sceneEditOverlay.classList.remove("is-open");
    scenesRunId += 1;
    scenes = [];
    scenesToken = "";
    scenesBusy = false;
    scenesStoryText = "";
    scenesToneUsed = "";
    scenesGrid.replaceChildren();
    scenesProgress.hidden = true;
    scenesStale.hidden = true;
    generateScenesBtn.disabled = false;
    scenesTone.disabled = false;
    generateScenesBtn.textContent = SCENES_BUTTON_LABEL;
    refreshToneHint();
  }

  // Muestra qué significa el tono elegido; si cambió después de generar las
  // escenas, avisa que hay que volver a generarlas para aplicarlo.
  function refreshToneHint() {
    const changed = scenes.length > 0 && scenesToneUsed && scenesTone.value !== scenesToneUsed;
    scenesToneHint.textContent = changed
      ? "Cambiaste el tono: pulsa «Generar de nuevo» para aplicarlo."
      : (scenesTone.selectedOptions[0]?.dataset.hint || "");
  }

  scenesTone.addEventListener("change", refreshToneHint);
  refreshToneHint();

  generateScenesBtn.addEventListener("click", generateScenes);
  resultBackBtn.addEventListener("click", () => goToReviewStep(-1));

  resultTranscribedText.addEventListener("input", () => {
    scenesStale.hidden = !scenesStoryText
      || resultTranscribedText.textContent.trim() === scenesStoryText;
    updateDoneButtonState();
  });
  resultSummaryText.addEventListener("input", updateDoneButtonState);
  // El periodo y el tema ya no se eligen aquí -eso pasó a classifyOverlay,
  // un paso posterior (ver openClassifyModal)- así que este botón solo
  // depende de que el contenido en sí esté listo.
  function updateDoneButtonState() {
    let contentReady;
    if (currentResultType === "oracion_imagen") {
      // getImageSentencesData() también marca en rojo las filas incompletas
      // (efecto secundario deseado); no se usa su cantidad para decidir si
      // ya se puede guardar porque deduplica por texto -dos oraciones
      // completas con el mismo texto no deberían bloquear el guardado.
      getImageSentencesData();
      contentReady = allImageSentenceRowsComplete();
    } else if (currentResultType === "bits") {
      // Mismo motivo que oracion_imagen: marca en rojo las filas incompletas
      // como efecto secundario, y no usa la cantidad devuelta (dedupe por
      // palabra) para decidir si ya se puede guardar.
      getBitsData();
      contentReady = allBitsRowsComplete();
    } else if (currentResultType === "oracion") {
      contentReady = sentenceRowsAreValid();
    } else {
      // Cuento: cada pantalla exige lo suyo para pasar a la siguiente; la última
      // ("Continuar") exige que todo esté listo.
      contentReady = isReviewWizard() && reviewStepKey !== "questions"
        ? reviewStepReady(reviewStepKey)
        : REVIEW_WIZARD_STEPS.every((step) => reviewStepReady(step.key));
    }
    // No se puede continuar a media generación de las escenas.
    resultDoneBtn.disabled = !contentReady || scenesBusy;
    paintReviewState();
  }

  // Cuentos: la revisión es un asistente de tres pantallas (texto, escenas y
  // preguntas) dentro del mismo modal. Solo se ve la pantalla actual;
  // "Siguiente" avanza cuando esa pantalla está lista y "Atrás" vuelve. Los
  // demás materiales tienen una sola pantalla y no usan el asistente.
  const REVIEW_WIZARD_STEPS = [
    { key: "text", title: "Revisar texto", focusTarget: () => resultTranscribedText },
    { key: "scenes", title: "Ilustrar y narrar escenas", focusTarget: () => generateScenesBtn },
    { key: "questions", title: "Generar preguntas", focusTarget: () => generateQuestionsBtn },
  ];
  let reviewStepKey = "text";

  function isReviewWizard() {
    return resultModal.classList.contains("result-modal--wizard");
  }

  // Qué necesita cada pantalla para poder pasar a la siguiente.
  function reviewStepReady(key) {
    if (key === "text") {
      return Boolean(resultTranscribedText.textContent.trim() && resultSummaryText.textContent.trim());
    }
    if (key === "scenes") return scenesReadyToSave();
    return questionsReady;
  }

  // Muestra la sección indicada del modal de revisión. En un cuento las tres
  // secciones quedan abiertas y solo se ve la actual; en los demás materiales
  // queda abierta solo esa. Con `focus` lleva el foco al control principal de
  // la pantalla (al moverse con "Siguiente"/"Atrás").
  function openReviewStep(key, { focus = false } = {}) {
    reviewStepKey = key;
    const wizard = isReviewWizard();
    reviewSteps.forEach((step) => {
      const current = step.dataset.reviewStep === key;
      step.open = wizard || current;
      step.classList.toggle("review-step--inactive", wizard && !current);
    });
    if (!wizard) return;

    const position = REVIEW_WIZARD_STEPS.findIndex((step) => step.key === key);
    resultBackBtn.hidden = position === 0;
    resultDoneBtn.textContent = position === REVIEW_WIZARD_STEPS.length - 1 ? "Continuar" : "Siguiente";
    reviewStepHeading.textContent = `Paso ${position + 1} de ${REVIEW_WIZARD_STEPS.length} · ${REVIEW_WIZARD_STEPS[position].title}`;
    resultModal.querySelector(".result-modal__columns").scrollTop = 0;
    updateDoneButtonState();
    if (focus) REVIEW_WIZARD_STEPS[position].focusTarget().focus({ preventScroll: true });
  }

  function goToReviewStep(offset) {
    const position = REVIEW_WIZARD_STEPS.findIndex((step) => step.key === reviewStepKey);
    const target = REVIEW_WIZARD_STEPS[position + offset];
    if (target) openReviewStep(target.key, { focus: true });
  }

  // Estado de cada sección y qué falta para poder continuar. "Listo" significa
  // generado, no revisado: el texto siempre figura como editable.
  function paintReviewState() {
    const setBadge = (key, text, ready = false) => {
      const badge = resultOverlay.querySelector(`[data-review-status="${key}"]`);
      badge.textContent = text;
      badge.dataset.ready = String(ready);
    };

    setBadge("questions", questionsReady ? "Listo" : "Pendiente", questionsReady);

    const scenesStaleNow = !scenesStale.hidden;
    const allComplete = scenesReadyToSave();
    let scenesLabel = "Pendiente";
    if (scenesBusy) scenesLabel = scenes.some((scene) => scene.audioState === "loading")
      ? "Narrando…" : "Dibujando…";
    else if (scenesStaleNow) scenesLabel = "Texto cambiado";
    else if (scenes.some((scene) => scene.state === "error" || scene.audioState === "error")) scenesLabel = "Con errores";
    else if (allComplete) scenesLabel = "Listo";
    setBadge("scenes", scenesLabel, allComplete);

    if (isReviewWizard()) {
      reviewStepper.querySelectorAll("[data-stepper]").forEach((item) => {
        const key = item.dataset.stepper;
        item.dataset.state = key === reviewStepKey
          ? "current"
          : reviewStepReady(key) ? "done" : "todo";
        if (key === reviewStepKey) item.setAttribute("aria-current", "step");
        else item.removeAttribute("aria-current");
      });
    }

    let message;
    if (currentResultType === "cuento") {
      const step = isReviewWizard() ? reviewStepKey : "questions";
      if (step === "text") {
        message = reviewStepReady("text")
          ? "Cuando termines de revisar el texto, pulsa «Siguiente»."
          : "Escribe el texto completo y el resumen para continuar.";
      } else if (step === "scenes") {
        if (scenesBusy) message = "Espera a que terminen de ilustrarse y narrarse las escenas.";
        else if (scenes.length > 0 && scenesStaleNow) message = "Vuelve a generar las escenas: el texto cambió.";
        else if (!allComplete) message = "Genera las escenas (imágenes y audios) para continuar.";
        else message = "Escenas listas. Pulsa «Siguiente» para generar las preguntas.";
      } else {
        const missing = [];
        if (!reviewStepReady("text")) missing.push("escribir el texto y el resumen (paso 1)");
        if (!allComplete) missing.push("generar las escenas (paso 2)");
        if (!questionsReady) missing.push("generar las preguntas");
        const missingText = missing.length > 1
          ? `${missing.slice(0, -1).join(", ")} y ${missing[missing.length - 1]}`
          : missing[0];
        message = missing.length
          ? `Para continuar falta ${missingText}.`
          : "Todo listo para continuar.";
        if (scenes.length > 0 && scenesStaleNow) {
          message += " El texto cambió: vuelve al paso 2 y genera las escenas de nuevo.";
        }
      }
    } else {
      message = resultDoneBtn.disabled
        ? "Completa las filas marcadas para continuar."
        : "Todo listo para continuar.";
    }
    reviewRequirements.textContent = message;
  }

  function resetResultState() {
    resetImageDesignState();
    resetBitsDesignState();
    questionsReady = false;
    questionsResult.innerHTML = "";
    resultSentencesList.innerHTML = "";
    scenesTone.value = "auto";
    resetScenes();
    openReviewStep("text");

    // El tema se elige de nuevo para cada material; parte sin selección y con
    // las opciones acotadas al periodo vigente.
    filterUploadTemas(true);
    classifyOverlay.classList.remove("is-open");
    pendingSave = null;

    updateDoneButtonState();
  }

  // Último paso antes de guardar de verdad: contenido ya aprobado (cuento,
  // oraciones, oraciones con imágenes -generadas o del modo editor-), solo
  // falta el periodo y el tema. `run` hace el guardado en sí (uno de los
  // saveXxxMaterial de más arriba); `backOverlay` es a dónde volver si la
  // docente toca "Volver" en vez de "Guardar material" (ver
  // classifyBackBtn/classifySaveBtn, más abajo).
  function openClassifyModal(backOverlay, run) {
    pendingSave = { backOverlay, run };
    classifyMaterialTitle.textContent = currentMaterialTitle;
    classifySaveBtn.textContent = "Guardar material";
    classifyBackBtn.disabled = false;
    updateClassifySaveState();
    backOverlay.classList.remove("is-open");
    classifyOverlay.classList.add("is-open");
  }

  function updateClassifySaveState() {
    classifySaveBtn.disabled = !uploadTemaSelect.value;
  }

  // Sentence review: one editable line per sentence. The teacher can fix
  // wording, clear a line to drop it, remove it, add blank lines, or ask the
  // IA for more — mirroring how the story questions are reviewed before saving.
  function appendSentenceItem(text = "", { focus = false } = {}) {
    const item = document.createElement("li");
    item.className = "sentences-review__item";

    const editable = document.createElement("div");
    editable.className = "sentences-review__editable";
    editable.contentEditable = "true";
    editable.spellcheck = true;
    editable.textContent = typeof text === "string" ? text : "";
    // Recién creada, la fila no se marca inválida todavía aunque esté vacía
    // -eso se vería mal apenas se abre el editor-; "touched" pasa a true en
    // cuanto la docente escribe algo o sale del campo, y desde ahí sí se
    // valida que no quede en blanco (ver sentenceRowsAreValid).
    const markTouched = () => {
      item.dataset.touched = "true";
      updateDoneButtonState();
    };
    editable.addEventListener("input", markTouched);
    editable.addEventListener("blur", markTouched);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "sentences-review__remove";
    remove.title = "Quitar esta oración";
    remove.textContent = "✕";
    remove.addEventListener("click", () => {
      item.remove();
      updateDoneButtonState();
    });

    item.append(editable, remove);
    resultSentencesList.appendChild(item);
    if (focus) editable.focus();
  }

  function renderSentences(sentences) {
    resultSentencesList.innerHTML = "";
    (sentences || []).forEach((sentence) => appendSentenceItem(sentence));
    updateDoneButtonState();
  }

  function getSentencesData() {
    return Array.from(resultSentencesList.querySelectorAll(".sentences-review__item:not(.sentences-review__item--image) .sentences-review__editable"))
      .map((el) => el.textContent.replace(/\s+/g, " ").trim())
      .filter(Boolean);
  }

  // Una oración no puede quedar en blanco (sea que vengan de un documento,
  // de la IA o escritas a mano) - para quitar una línea se usa el botón ✕,
  // no dejarla vacía. Marca cada fila ya "tocada" que esté vacía y devuelve
  // false si falta alguna por corregir, o si no queda ninguna oración.
  function sentenceRowsAreValid() {
    const rows = Array.from(
      resultSentencesList.querySelectorAll(".sentences-review__item:not(.sentences-review__item--image)")
    );
    if (!rows.length) return false;
    let valid = true;
    rows.forEach((row) => {
      const blank = !row.querySelector(".sentences-review__editable").textContent.replace(/\s+/g, " ").trim();
      row.classList.toggle("sentences-review__item--invalid", blank && row.dataset.touched === "true");
      if (blank) valid = false;
    });
    return valid;
  }

  // Cuadro para subir, a mano, la imagen que reemplazará a un sustantivo
  // (modo editor). El archivo elegido se previsualiza aquí mismo
  // (URL.createObjectURL) y recién se envía al guardar el material -junto
  // con el resto de la oración- por eso `onChange` avisa a la fila para que
  // se revalide (ver appendImageSentenceItem).
  function buildNounImagePicker(index, onChange) {
    const wrap = document.createElement("div");
    wrap.className = "sentences-review__image-picker";

    const preview = document.createElement("img");
    preview.className = "sentences-review__image-preview";
    preview.alt = "";
    preview.hidden = true;

    const dropLabel = document.createElement("label");
    dropLabel.className = "sentences-review__image-dropzone";

    const icon = document.createElement("span");
    icon.className = "sentences-review__image-dropzone-icon";
    icon.textContent = "🖼️";

    const text = document.createElement("span");
    text.className = "sentences-review__image-dropzone-text";
    text.textContent = "Subir imagen";

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = "image/*";
    fileInput.hidden = true;
    fileInput.dataset.imageSentenceFile = String(index);

    fileInput.addEventListener("change", () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      preview.src = URL.createObjectURL(file);
      preview.hidden = false;
      text.textContent = file.name;
      dropLabel.classList.add("has-image");
      dropLabel.setAttribute("aria-invalid", "false");
      onChange();
    });

    dropLabel.append(icon, text, fileInput);
    wrap.append(preview, dropLabel);
    return wrap;
  }

  function appendImageSentenceItem(item = {}, { focus = false } = {}) {
    const row = document.createElement("li");
    row.className = "sentences-review__item sentences-review__item--image";
    // Único dentro de esta revisión: ver el comentario de imageSentenceRowSeq.
    row.dataset.rowIndex = String(imageSentenceRowSeq++);

    const content = document.createElement("div");
    content.className = "sentences-review__image-content";

    const editable = document.createElement("div");
    editable.className = "sentences-review__editable";
    editable.contentEditable = "true";
    editable.spellcheck = true;
    editable.dataset.imageSentenceText = "";
    editable.textContent = typeof item.texto === "string" ? item.texto : "";

    const preview = document.createElement("div");
    preview.className = "sentences-review__preview";
    preview.setAttribute("aria-label", "Vista previa con sustantivos resaltados");

    const nouns = Array.isArray(item.sustantivos) ? item.sustantivos : [];
    const nounFields = document.createElement("div");
    nounFields.className = "sentences-review__nouns";
    const nounInputs = [0, 1].map((index) => {
      const field = document.createElement("div");
      field.className = "sentences-review__noun-field";

      const label = document.createElement("label");
      label.className = "sentences-review__noun-label";

      const labelText = document.createElement("span");
      labelText.textContent = `Sustantivo ${index + 1}`;

      const input = document.createElement("input");
      input.type = "text";
      input.className = "sentences-review__noun-input";
      input.value = typeof nouns[index] === "string" ? nouns[index] : "";
      input.maxLength = 300;
      input.autocomplete = "off";
      input.dataset.imageSentenceNoun = String(index);

      label.append(labelText, input);
      field.appendChild(label);
      // Solo en modo editor: la palabra ya trae su propio cuadro para subir
      // la imagen que la reemplazará -sin esto, la imagen se genera después
      // con IA en el paso de diseño (image-sentences/prepare).
      if (manualImageEntryMode) {
        field.appendChild(buildNounImagePicker(index, handleFieldChange));
      }
      nounFields.appendChild(field);
      return input;
    });

    function refreshPreview() {
      const text = editable.textContent.replace(/\s+/g, " ").trim();
      const currentNouns = nounInputs.map((input) => input.value.replace(/\s+/g, " ").trim());
      preview.innerHTML = text
        ? markNouns(text, currentNouns)
        : '<span class="sentences-review__preview-empty">Vista previa de la oración</span>';
    }

    // Recién creada, la fila no se marca inválida todavía aunque esté
    // incompleta -eso se vería mal apenas se abre la revisión-; "touched"
    // pasa a true en cuanto la docente edita algo o sale de un campo, y
    // desde ahí sí se exige texto + los 2 sustantivos (ver getImageSentencesData).
    function handleFieldChange() {
      row.dataset.touched = "true";
      refreshPreview();
      updateDoneButtonState();
    }

    editable.addEventListener("input", handleFieldChange);
    editable.addEventListener("blur", handleFieldChange);
    nounInputs.forEach((input) => {
      input.addEventListener("input", handleFieldChange);
      input.addEventListener("blur", handleFieldChange);
    });

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "sentences-review__remove";
    remove.title = "Quitar esta oración";
    remove.setAttribute("aria-label", "Quitar esta oración");
    remove.textContent = "✕";
    remove.addEventListener("click", () => {
      row.remove();
      updateDoneButtonState();
    });

    content.append(editable, preview, nounFields);
    row.append(content, remove);
    resultSentencesList.appendChild(row);
    refreshPreview();
    if (focus) editable.focus();
  }

  function renderImageSentenceRows(items) {
    resultSentencesList.innerHTML = "";
    (items || []).forEach((item) => appendImageSentenceItem(item));
    updateDoneButtonState();
  }

  // Fila a fila, los 2 File elegidos en los cuadros de subir imagen (modo
  // editor); [] si no está en ese modo, o si a alguno todavía le falta el
  // archivo (undefined en su lugar).
  function getImageSentenceFiles(row) {
    if (!manualImageEntryMode) return [];
    return Array.from(row.querySelectorAll("[data-image-sentence-file]"))
      .map((input) => input.files && input.files[0]);
  }

  // Como sentenceRowsAreValid(), pero para "oracion_imagen": exige texto +
  // 2 sustantivos en cada fila, sin deduplicar (a diferencia de
  // getImageSentencesData, que sí deduplica porque arma el payload a enviar).
  // En modo editor también exige las 2 imágenes que sube la docente -sin
  // eso no hay nada que guardar todavía.
  function allImageSentenceRowsComplete() {
    const rows = Array.from(resultSentencesList.querySelectorAll(".sentences-review__item--image"));
    if (!rows.length) return false;
    return rows.every((row) => {
      const text = row.querySelector("[data-image-sentence-text]")
        ?.textContent.replace(/\s+/g, " ").trim() || "";
      const nouns = Array.from(row.querySelectorAll("[data-image-sentence-noun]"))
        .map((input) => input.value.replace(/\s+/g, " ").trim());
      if (!(Boolean(text) && nouns.length === 2 && nouns.every(Boolean))) return false;
      if (!manualImageEntryMode) return true;
      const files = getImageSentenceFiles(row);
      return files.length === 2 && files.every(Boolean);
    });
  }

  // Una oración con imágenes tampoco puede quedar en blanco ni incompleta:
  // hace falta el texto Y sus 2 sustantivos (y, en modo editor, sus 2
  // imágenes), sea que la fila venga de un documento, de la IA o se haya
  // escrito a mano. Igual que en las oraciones planas, solo se marca
  // inválida una vez "tocada" (ver appendImageSentenceItem). En modo editor
  // cada item también lleva `rowIndex` y `files`, que es lo que
  // saveManualImageSentenceMaterial necesita para guardar.
  function getImageSentencesData() {
    const seen = new Set();
    const items = [];
    Array.from(resultSentencesList.querySelectorAll(".sentences-review__item--image")).forEach((row) => {
      const text = row.querySelector("[data-image-sentence-text]")
        ?.textContent.replace(/\s+/g, " ").trim() || "";
      const nounInputs = Array.from(row.querySelectorAll("[data-image-sentence-noun]"));
      const nouns = nounInputs.map((input) => input.value.replace(/\s+/g, " ").trim());
      const files = getImageSentenceFiles(row);
      const imagesComplete = !manualImageEntryMode || (files.length === 2 && files.every(Boolean));
      const complete = Boolean(text) && nouns.length === 2 && nouns.every(Boolean) && imagesComplete;
      const touched = row.dataset.touched === "true";

      row.classList.toggle("sentences-review__item--invalid", touched && !complete);
      nounInputs.forEach((input) => {
        input.setAttribute("aria-invalid", String(touched && !complete && !input.value.trim()));
      });
      if (manualImageEntryMode) {
        row.querySelectorAll(".sentences-review__image-dropzone").forEach((dropzone, index) => {
          dropzone.setAttribute("aria-invalid", String(touched && !files[index]));
        });
      }

      const key = text.toLocaleLowerCase("es");
      if (complete && !seen.has(key) && items.length < 120) {
        seen.add(key);
        items.push({
          texto: text,
          sustantivos: nouns,
          ...(manualImageEntryMode ? { rowIndex: row.dataset.rowIndex, files } : {}),
        });
      }
    });
    return items;
  }

  // "Bits": una sola palabra + una imagen por fila (a diferencia de
  // "oracion_imagen", que tiene texto + 2 sustantivos). Reusa el mismo
  // esqueleto de fila (.sentences-review__item--image, .sentences-review__
  // editable, buildNounImagePicker) pero sin preview de sustantivos
  // resaltados ni grid de 2 columnas -no hace falta, es una sola palabra.
  function appendBitsItem(item = {}, { focus = false } = {}) {
    const row = document.createElement("li");
    row.className = "sentences-review__item sentences-review__item--image sentences-review__item--bit";
    // Único dentro de esta revisión: mismo propósito que imageSentenceRowSeq.
    row.dataset.rowIndex = String(bitRowSeq++);

    const content = document.createElement("div");
    content.className = "sentences-review__image-content";

    const editable = document.createElement("div");
    editable.className = "sentences-review__editable";
    editable.contentEditable = "true";
    editable.spellcheck = true;
    editable.dataset.bitWord = "";
    editable.textContent = typeof item.palabra === "string" ? item.palabra : "";

    // Recién creada, la fila no se marca inválida todavía aunque esté
    // incompleta (mismo criterio que appendImageSentenceItem).
    function handleFieldChange() {
      row.dataset.touched = "true";
      updateDoneButtonState();
    }

    editable.addEventListener("input", handleFieldChange);
    editable.addEventListener("blur", handleFieldChange);

    content.appendChild(editable);

    // Solo en modo editor: la palabra ya trae su propio cuadro para subir la
    // imagen que la representará -sin esto, la imagen se genera después con
    // IA en el paso de diseño (/api/bits/prepare).
    if (manualBitsEntryMode) {
      content.appendChild(buildNounImagePicker(0, handleFieldChange));

      // Sin IA de por medio nadie sugiere la pregunta: la docente puede
      // escribirla (opcional; sin ella el robot usa su frase por defecto).
      const question = document.createElement("input");
      question.type = "text";
      question.className = "image-design__noun-input sentences-review__bit-question";
      question.maxLength = 200;
      question.autocomplete = "off";
      question.placeholder = "Pregunta del robot (opcional). Ej.: El sol está…";
      question.setAttribute("aria-label", "Pregunta del robot para esta palabra");
      question.dataset.bitQuestion = "";
      question.value = typeof item.pregunta === "string" ? item.pregunta : "";
      content.appendChild(question);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "sentences-review__remove";
    remove.title = "Quitar esta palabra";
    remove.setAttribute("aria-label", "Quitar esta palabra");
    remove.textContent = "✕";
    remove.addEventListener("click", () => {
      row.remove();
      updateDoneButtonState();
    });

    row.append(content, remove);
    resultSentencesList.appendChild(row);
    if (focus) editable.focus();
  }

  function renderBitsRows(items) {
    resultSentencesList.innerHTML = "";
    (items || []).forEach((item) => appendBitsItem(item));
    updateDoneButtonState();
  }

  // El File elegido en el cuadro de subir imagen (modo editor); null si no
  // está en ese modo, o si todavía no eligió archivo.
  function getBitsFile(row) {
    if (!manualBitsEntryMode) return null;
    const input = row.querySelector("[data-image-sentence-file]");
    return (input && input.files && input.files[0]) || null;
  }

  // Como allImageSentenceRowsComplete(), pero para "bits": exige la palabra
  // (y, en modo editor, su imagen) en cada fila, sin deduplicar.
  function allBitsRowsComplete() {
    const rows = Array.from(resultSentencesList.querySelectorAll(".sentences-review__item--bit"));
    if (!rows.length) return false;
    return rows.every((row) => {
      const word = row.querySelector("[data-bit-word]")?.textContent.replace(/\s+/g, " ").trim() || "";
      if (!word) return false;
      if (!manualBitsEntryMode) return true;
      return Boolean(getBitsFile(row));
    });
  }

  // Como getImageSentencesData(), pero para "bits": arma el payload a enviar
  // (deduplicado por palabra). En modo editor cada item también lleva
  // `rowIndex` y `file`, que es lo que saveManualBitsMaterial necesita.
  function getBitsData() {
    const seen = new Set();
    const items = [];
    Array.from(resultSentencesList.querySelectorAll(".sentences-review__item--bit")).forEach((row) => {
      const word = row.querySelector("[data-bit-word]")?.textContent.replace(/\s+/g, " ").trim() || "";
      const file = getBitsFile(row);
      const imageComplete = !manualBitsEntryMode || Boolean(file);
      const complete = Boolean(word) && imageComplete;
      const touched = row.dataset.touched === "true";

      row.classList.toggle("sentences-review__item--invalid", touched && !complete);
      if (manualBitsEntryMode) {
        row.querySelector(".sentences-review__image-dropzone")
          ?.setAttribute("aria-invalid", String(touched && !file));
      }

      const key = word.toLocaleLowerCase("es");
      if (complete && !seen.has(key) && items.length < 120) {
        seen.add(key);
        items.push({
          palabra: word,
          ...(manualBitsEntryMode
            ? {
              rowIndex: row.dataset.rowIndex,
              file,
              pregunta: row.querySelector("[data-bit-question]")?.value.replace(/\s+/g, " ").trim() || "",
            }
            : {}),
        });
      }
    });
    return items;
  }

  function resetImageDesignState() {
    imageDesignToken = "";
    designSentences = [];
    currentDesignIndex = 0;
    imageDesignBusy = false;
    imageDesignOverlay.classList.remove("is-open");
    imageDesignMixedLine.replaceChildren();
    imageDesignNounControls.replaceChildren();
    imageDesignStage.hidden = true;
    imageDesignEmpty.hidden = true;
    imageDesignCounter.textContent = "Oración 0 de 0";
    imageDesignPrevBtn.disabled = true;
    imageDesignNextBtn.disabled = true;
    imageDesignRemoveBtn.disabled = true;
    imageDesignSaveBtn.disabled = true;
    imageDesignSaveBtn.textContent = "Continuar";
  }

  function updateDesignNav() {
    const count = designSentences.length;
    imageDesignCounter.textContent = count
      ? `Oración ${currentDesignIndex + 1} de ${count}`
      : "Oración 0 de 0";
    imageDesignPrevBtn.disabled = imageDesignBusy || !count || currentDesignIndex === 0;
    imageDesignNextBtn.disabled = imageDesignBusy || !count || currentDesignIndex >= count - 1;
    imageDesignRemoveBtn.disabled = imageDesignBusy || !count;
    imageDesignSaveBtn.disabled = imageDesignBusy || !count;
    imageDesignNounControls.querySelectorAll("input, button").forEach((control) => {
      control.disabled = imageDesignBusy;
    });
  }

  function appendDesignText(value) {
    if (!value) return;
    const text = document.createElement("span");
    text.className = "image-design__text";
    text.textContent = value;
    imageDesignMixedLine.appendChild(text);
  }

  function renderDesignMixedLine(sentence) {
    imageDesignMixedLine.replaceChildren();
    const template = String(sentence.plantilla || sentence.texto || "");
    const placeholderPattern = /\{\{([01])\}\}/g;
    let cursor = 0;
    let match = placeholderPattern.exec(template);

    while (match) {
      appendDesignText(template.slice(cursor, match.index));
      const nounIndex = Number.parseInt(match[1], 10);
      const noun = sentence.sustantivos[nounIndex];
      if (noun) {
        const image = document.createElement("img");
        image.className = "image-design__img";
        image.src = noun.imagen_url;
        image.alt = noun.palabra;
        image.dataset.nounIndex = String(nounIndex);
        imageDesignMixedLine.appendChild(image);
      }
      cursor = match.index + match[0].length;
      match = placeholderPattern.exec(template);
    }
    appendDesignText(template.slice(cursor));
  }

  async function regenerateDesignNoun(sentence, nounIndex, input, button) {
    const noun = sentence.sustantivos[nounIndex];
    const proposedWord = input.value.replace(/\s+/g, " ").trim();
    if (!proposedWord) {
      alert("Escribe el sustantivo antes de regenerar la imagen.");
      return;
    }

    const payload = {
      token: imageDesignToken,
      sentence_index: sentence.staging_index,
      noun_index: nounIndex,
    };
    if (proposedWord !== noun.palabra) {
      payload.palabra = proposedWord;
    }

    const originalLabel = button.textContent;
    imageDesignBusy = true;
    button.textContent = "Generando…";
    updateDesignNav();
    try {
      const response = await authorizedFetch("/api/material/image-sentences/regenerate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudo regenerar la imagen.");
      }

      sentence.plantilla = data.plantilla;
      sentence.sustantivos[nounIndex] = {
        ...noun,
        palabra: data.palabra,
        imagen_url: data.imagen_url,
        fuente: data.fuente === "manual" ? "manual" : "ia",
      };
      if (designSentences[currentDesignIndex] === sentence) {
        renderDesignSentence();
      }
    } catch (error) {
      alert(error.message || "No se pudo regenerar la imagen.");
    } finally {
      imageDesignBusy = false;
      button.textContent = originalLabel;
      updateDesignNav();
    }
  }

  // Campo "Cambiar la imagen con indicaciones" del paso de diseño (oraciones
  // con imágenes y bits): la docente describe un ajuste y la IA lo aplica sobre
  // la imagen ya generada en vez de crear otra desde cero. `onApply` recibe el
  // campo de texto y el botón para que cada flujo maneje su propio estado.
  function buildImageEditField(onApply) {
    const wrapper = document.createElement("div");
    wrapper.className = "image-design__edit";

    const label = document.createElement("label");
    label.className = "image-design__noun-label";
    const caption = document.createElement("span");
    caption.textContent = "Cambiar la imagen con indicaciones";
    const instruction = document.createElement("input");
    instruction.type = "text";
    instruction.className = "image-design__noun-input image-design__edit-input";
    instruction.maxLength = 500;
    instruction.autocomplete = "off";
    instruction.placeholder = "Ej.: que sea más grande y de color azul";
    label.append(caption, instruction);

    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "btn btn--primary image-design__edit-apply";
    apply.textContent = "Aplicar cambios";

    const submit = () => onApply(instruction, apply);
    apply.addEventListener("click", submit);
    instruction.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        submit();
      }
    });

    wrapper.append(label, apply);
    return wrapper;
  }

  async function editDesignNounImage(sentence, nounIndex, instructionInput, button) {
    const instruction = instructionInput.value.replace(/\s+/g, " ").trim();
    if (!instruction) {
      alert("Escribe qué quieres cambiar de la imagen.");
      return;
    }
    if (imageDesignBusy) return;

    const originalLabel = button.textContent;
    imageDesignBusy = true;
    button.textContent = "Editando…";
    updateDesignNav();
    try {
      const response = await authorizedFetch("/api/material/image-sentences/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: imageDesignToken,
          sentence_index: sentence.staging_index,
          noun_index: nounIndex,
          instruction,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudo editar la imagen.");
      }

      sentence.sustantivos[nounIndex] = {
        ...sentence.sustantivos[nounIndex],
        imagen_url: data.imagen_url,
      };
      instructionInput.value = "";
      // Solo se repinta la línea con las imágenes: así no se pierde una
      // palabra que la docente haya escrito y todavía no haya aplicado.
      if (designSentences[currentDesignIndex] === sentence) {
        renderDesignMixedLine(sentence);
      }
    } catch (error) {
      alert(error.message || "No se pudo editar la imagen.");
    } finally {
      imageDesignBusy = false;
      button.textContent = originalLabel;
      updateDesignNav();
    }
  }

  async function uploadDesignNounImage(sentence, nounIndex, input, fileInput, button) {
    const file = fileInput.files?.[0];
    if (!file || imageDesignBusy) {
      fileInput.value = "";
      return;
    }

    const noun = sentence.sustantivos[nounIndex];
    const proposedWord = input.value.replace(/\s+/g, " ").trim();
    if (!proposedWord) {
      fileInput.value = "";
      alert("Escribe el sustantivo antes de subir la imagen.");
      return;
    }

    const formData = new FormData();
    formData.append("token", imageDesignToken);
    formData.append("sentence_index", sentence.staging_index);
    formData.append("noun_index", nounIndex);
    if (proposedWord !== noun.palabra) {
      formData.append("palabra", proposedWord);
    }
    formData.append("imagen", file);

    const originalLabel = button.textContent;
    imageDesignBusy = true;
    button.textContent = "Subiendo…";
    updateDesignNav();
    try {
      const response = await authorizedFetch("/api/material/image-sentences/upload-image", {
        method: "POST",
        body: formData,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudo subir la imagen.");
      }

      sentence.plantilla = data.plantilla;
      sentence.sustantivos[nounIndex] = {
        ...noun,
        palabra: data.palabra,
        imagen_url: data.imagen_url,
        fuente: data.fuente === "ia" ? "ia" : "manual",
      };
      if (designSentences[currentDesignIndex] === sentence) {
        renderDesignSentence();
      }
    } catch (error) {
      alert(error.message || "No se pudo subir la imagen.");
    } finally {
      imageDesignBusy = false;
      fileInput.value = "";
      button.textContent = originalLabel;
      updateDesignNav();
    }
  }

  function renderDesignSentence() {
    const count = designSentences.length;
    if (!count) {
      currentDesignIndex = 0;
      imageDesignStage.hidden = true;
      imageDesignEmpty.hidden = false;
      imageDesignMixedLine.replaceChildren();
      imageDesignNounControls.replaceChildren();
      updateDesignNav();
      return;
    }

    currentDesignIndex = Math.min(Math.max(currentDesignIndex, 0), count - 1);
    const sentence = designSentences[currentDesignIndex];
    imageDesignStage.hidden = false;
    imageDesignEmpty.hidden = true;
    renderDesignMixedLine(sentence);
    imageDesignNounControls.replaceChildren();

    sentence.sustantivos.forEach((noun, nounIndex) => {
      const group = document.createElement("div");
      group.className = "image-design__noun-control";

      const label = document.createElement("label");
      label.className = "image-design__noun-label";
      const heading = document.createElement("span");
      heading.className = "image-design__noun-heading";
      const caption = document.createElement("span");
      caption.textContent = `Sustantivo ${nounIndex + 1}`;
      const source = document.createElement("span");
      const isManual = noun.fuente === "manual";
      source.className = `image-design__source image-design__source--${isManual ? "manual" : "ia"}`;
      source.textContent = isManual ? "Subida por ti" : "Generada por IA";
      heading.append(caption, source);
      const input = document.createElement("input");
      input.type = "text";
      input.className = "image-design__noun-input";
      input.maxLength = 60;
      input.autocomplete = "off";
      input.value = noun.palabra;
      label.append(heading, input);

      const actions = document.createElement("div");
      actions.className = "image-design__noun-actions";

      const regenerate = document.createElement("button");
      regenerate.type = "button";
      regenerate.className = "btn btn--accent image-design__regenerate";
      regenerate.textContent = "Regenerar";
      regenerate.addEventListener("click", () => {
        regenerateDesignNoun(sentence, nounIndex, input, regenerate);
      });

      const fileInput = document.createElement("input");
      fileInput.type = "file";
      fileInput.className = "image-design__file-input";
      fileInput.accept = "image/*";
      fileInput.hidden = true;

      const upload = document.createElement("button");
      upload.type = "button";
      upload.className = "btn btn--ghost image-design__upload";
      upload.textContent = "Subir imagen";
      upload.addEventListener("click", () => {
        fileInput.click();
      });
      fileInput.addEventListener("change", () => {
        uploadDesignNounImage(sentence, nounIndex, input, fileInput, upload);
      });

      actions.append(regenerate, fileInput, upload);
      const edit = buildImageEditField((instructionInput, applyButton) => {
        editDesignNounImage(sentence, nounIndex, instructionInput, applyButton);
      });
      group.append(label, actions, edit);
      imageDesignNounControls.appendChild(group);
    });
    updateDesignNav();
  }

  imageDesignPrevBtn.addEventListener("click", () => {
    if (currentDesignIndex <= 0) return;
    currentDesignIndex -= 1;
    renderDesignSentence();
  });

  imageDesignNextBtn.addEventListener("click", () => {
    if (currentDesignIndex >= designSentences.length - 1) return;
    currentDesignIndex += 1;
    renderDesignSentence();
  });

  imageDesignRemoveBtn.addEventListener("click", () => {
    if (!designSentences.length) return;
    designSentences.splice(currentDesignIndex, 1);
    if (currentDesignIndex >= designSentences.length) {
      currentDesignIndex = Math.max(0, designSentences.length - 1);
    }
    renderDesignSentence();
  });

  imageDesignBackBtn.addEventListener("click", () => {
    resetImageDesignState();
    resultOverlay.classList.add("is-open");
    updateDoneButtonState();
  });

  // "Descartar" desde cualquiera de los dos overlays de diseño (oraciones
  // con imágenes o bits) vuelve todo el flujo al inicio, sin importar en
  // cuál de los dos estaba la docente.
  function discardEverything() {
    imageDesignOverlay.classList.remove("is-open");
    bitsDesignOverlay.classList.remove("is-open");
    resultOverlay.classList.remove("is-open");
    classifyOverlay.classList.remove("is-open");
    loadingOverlay.classList.remove("is-open");
    uploadOverlay.classList.remove("is-open");
    storyOverlay.classList.remove("is-open");
    resetResultState();
    resetUploadForm();
    storyForm.reset();
    resetBitsConsonants();
    setStoryType("cuento");
    currentResultType = "cuento";
    currentMaterialTitle = "";
    currentTargetDurationMinutes = null;
    lastSentenceContext = { topic: "", grade_level: "" };
  }

  imageDesignDiscardBtn.addEventListener("click", discardEverything);

  imageDesignSaveBtn.addEventListener("click", () => {
    if (!designSentences.length) {
      alert("No hay oraciones con imágenes para guardar.");
      return;
    }
    openClassifyModal(imageDesignOverlay, () => saveDesignedImageSentenceMaterial());
  });

  // --- Diseño de "bits": mismo patrón que el diseño de oraciones con
  // imágenes de arriba (resetImageDesignState/updateDesignNav/
  // renderDesignSentence/regenerateDesignNoun/uploadDesignNounImage), pero
  // una sola imagen por bit -sin plantilla, sin mixed-line, un solo control.

  function resetBitsDesignState() {
    bitsDesignToken = "";
    designBits = [];
    currentBitsDesignIndex = 0;
    bitsDesignBusy = false;
    bitsDesignOverlay.classList.remove("is-open");
    bitsDesignImage.removeAttribute("src");
    bitsDesignNounControls.replaceChildren();
    bitsDesignStage.hidden = true;
    bitsDesignEmpty.hidden = true;
    bitsDesignCounter.textContent = "Bit 0 de 0";
    bitsDesignPrevBtn.disabled = true;
    bitsDesignNextBtn.disabled = true;
    bitsDesignRemoveBtn.disabled = true;
    bitsDesignSaveBtn.disabled = true;
    bitsDesignSaveBtn.textContent = "Continuar";
  }

  function updateBitsDesignNav() {
    const count = designBits.length;
    bitsDesignCounter.textContent = count
      ? `Bit ${currentBitsDesignIndex + 1} de ${count}`
      : "Bit 0 de 0";
    bitsDesignPrevBtn.disabled = bitsDesignBusy || !count || currentBitsDesignIndex === 0;
    bitsDesignNextBtn.disabled = bitsDesignBusy || !count || currentBitsDesignIndex >= count - 1;
    bitsDesignRemoveBtn.disabled = bitsDesignBusy || !count;
    bitsDesignSaveBtn.disabled = bitsDesignBusy || !count;
    bitsDesignNounControls.querySelectorAll("input, button").forEach((control) => {
      control.disabled = bitsDesignBusy;
    });
  }

  async function regenerateBitsDesignImage(bit, input, button) {
    const proposedWord = input.value.replace(/\s+/g, " ").trim();
    if (!proposedWord) {
      alert("Escribe la palabra antes de regenerar la imagen.");
      return;
    }

    const payload = { token: bitsDesignToken, item_index: bit.staging_index };
    if (proposedWord !== bit.palabra) {
      payload.palabra = proposedWord;
    }

    const originalLabel = button.textContent;
    bitsDesignBusy = true;
    button.textContent = "Generando…";
    updateBitsDesignNav();
    try {
      const response = await authorizedFetch("/api/bits/regenerate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudo regenerar la imagen.");
      }

      bit.palabra = data.palabra;
      bit.imagen_url = data.imagen_url;
      bit.fuente = data.fuente === "manual" ? "manual" : "ia";
      if (designBits[currentBitsDesignIndex] === bit) {
        renderBitsDesignItem();
      }
    } catch (error) {
      alert(error.message || "No se pudo regenerar la imagen.");
    } finally {
      bitsDesignBusy = false;
      button.textContent = originalLabel;
      updateBitsDesignNav();
    }
  }

  // La pregunta que el robot dice y muestra por cada bit. La palabra es la
  // respuesta del niño, así que la pregunta es el comienzo de una frase que esa
  // palabra completa ("El sol está…" -> "feliz"). La IA la propone al generar el
  // diseño; aquí la docente la edita o pide otra sugerencia para la imagen
  // actual (útil tras regenerar, editar o subir la imagen).
  async function suggestBitsQuestion(bit, wordInput, questionInput, button) {
    const proposedWord = wordInput.value.replace(/\s+/g, " ").trim();
    if (!proposedWord) {
      alert("Escribe la palabra antes de sugerir la pregunta.");
      return;
    }
    if (bitsDesignBusy) return;

    const originalLabel = button.textContent;
    bitsDesignBusy = true;
    button.textContent = "Sugiriendo…";
    updateBitsDesignNav();
    try {
      const response = await authorizedFetch("/api/bits/suggest-question", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: bitsDesignToken,
          item_index: bit.staging_index,
          palabra: proposedWord,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudo sugerir la pregunta.");
      }

      bit.pregunta = typeof data.pregunta === "string" ? data.pregunta : "";
      questionInput.value = bit.pregunta;
    } catch (error) {
      alert(error.message || "No se pudo sugerir la pregunta.");
    } finally {
      bitsDesignBusy = false;
      button.textContent = originalLabel;
      updateBitsDesignNav();
    }
  }

  function buildBitsQuestionField(bit, wordInput) {
    const wrapper = document.createElement("div");
    wrapper.className = "image-design__edit";

    const label = document.createElement("label");
    label.className = "image-design__noun-label";
    const caption = document.createElement("span");
    caption.textContent = "Pregunta del robot";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "image-design__noun-input";
    input.maxLength = 200;
    input.autocomplete = "off";
    input.placeholder = "Ej.: El sol está…";
    input.value = bit.pregunta || "";
    input.addEventListener("input", () => {
      bit.pregunta = input.value;
    });
    const hint = document.createElement("small");
    hint.className = "image-design__hint";
    hint.textContent = "La palabra es lo que responde el niño: la pregunta debe quedar completa al decirla.";
    label.append(caption, input, hint);

    const suggest = document.createElement("button");
    suggest.type = "button";
    suggest.className = "btn btn--ghost image-design__edit-apply";
    suggest.textContent = "Sugerir otra";
    suggest.addEventListener("click", () => {
      suggestBitsQuestion(bit, wordInput, input, suggest);
    });

    wrapper.append(label, suggest);
    return wrapper;
  }

  async function editBitsDesignImage(bit, instructionInput, button) {
    const instruction = instructionInput.value.replace(/\s+/g, " ").trim();
    if (!instruction) {
      alert("Escribe qué quieres cambiar de la imagen.");
      return;
    }
    if (bitsDesignBusy) return;

    const originalLabel = button.textContent;
    bitsDesignBusy = true;
    button.textContent = "Editando…";
    updateBitsDesignNav();
    try {
      const response = await authorizedFetch("/api/bits/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: bitsDesignToken,
          item_index: bit.staging_index,
          instruction,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudo editar la imagen.");
      }

      bit.imagen_url = data.imagen_url;
      instructionInput.value = "";
      // Solo se cambia la imagen: no se repintan los controles para no perder
      // una palabra escrita que todavía no se haya aplicado.
      if (designBits[currentBitsDesignIndex] === bit) {
        bitsDesignImage.src = bit.imagen_url;
      }
    } catch (error) {
      alert(error.message || "No se pudo editar la imagen.");
    } finally {
      bitsDesignBusy = false;
      button.textContent = originalLabel;
      updateBitsDesignNav();
    }
  }

  async function uploadBitsDesignImage(bit, input, fileInput, button) {
    const file = fileInput.files?.[0];
    if (!file || bitsDesignBusy) {
      fileInput.value = "";
      return;
    }

    const proposedWord = input.value.replace(/\s+/g, " ").trim();
    if (!proposedWord) {
      fileInput.value = "";
      alert("Escribe la palabra antes de subir la imagen.");
      return;
    }

    const formData = new FormData();
    formData.append("token", bitsDesignToken);
    formData.append("item_index", bit.staging_index);
    if (proposedWord !== bit.palabra) {
      formData.append("palabra", proposedWord);
    }
    formData.append("imagen", file);

    const originalLabel = button.textContent;
    bitsDesignBusy = true;
    button.textContent = "Subiendo…";
    updateBitsDesignNav();
    try {
      const response = await authorizedFetch("/api/bits/upload-image", {
        method: "POST",
        body: formData,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "No se pudo subir la imagen.");
      }

      bit.palabra = data.palabra;
      bit.imagen_url = data.imagen_url;
      bit.fuente = data.fuente === "ia" ? "ia" : "manual";
      if (designBits[currentBitsDesignIndex] === bit) {
        renderBitsDesignItem();
      }
    } catch (error) {
      alert(error.message || "No se pudo subir la imagen.");
    } finally {
      bitsDesignBusy = false;
      fileInput.value = "";
      button.textContent = originalLabel;
      updateBitsDesignNav();
    }
  }

  function renderBitsDesignItem() {
    const count = designBits.length;
    if (!count) {
      currentBitsDesignIndex = 0;
      bitsDesignStage.hidden = true;
      bitsDesignEmpty.hidden = false;
      bitsDesignImage.removeAttribute("src");
      bitsDesignNounControls.replaceChildren();
      updateBitsDesignNav();
      return;
    }

    currentBitsDesignIndex = Math.min(Math.max(currentBitsDesignIndex, 0), count - 1);
    const bit = designBits[currentBitsDesignIndex];
    bitsDesignStage.hidden = false;
    bitsDesignEmpty.hidden = true;
    bitsDesignImage.src = bit.imagen_url;
    bitsDesignImage.alt = bit.palabra;
    bitsDesignNounControls.replaceChildren();

    const group = document.createElement("div");
    group.className = "image-design__noun-control";

    const label = document.createElement("label");
    label.className = "image-design__noun-label";
    const heading = document.createElement("span");
    heading.className = "image-design__noun-heading";
    const caption = document.createElement("span");
    caption.textContent = "Palabra";
    const source = document.createElement("span");
    const isManual = bit.fuente === "manual";
    source.className = `image-design__source image-design__source--${isManual ? "manual" : "ia"}`;
    source.textContent = isManual ? "Subida por ti" : "Generada por IA";
    heading.append(caption, source);
    const input = document.createElement("input");
    input.type = "text";
    input.className = "image-design__noun-input";
    input.maxLength = 60;
    input.autocomplete = "off";
    input.value = bit.palabra;
    label.append(heading, input);

    const actions = document.createElement("div");
    actions.className = "image-design__noun-actions";

    const regenerate = document.createElement("button");
    regenerate.type = "button";
    regenerate.className = "btn btn--accent image-design__regenerate";
    regenerate.textContent = "Regenerar";
    regenerate.addEventListener("click", () => {
      regenerateBitsDesignImage(bit, input, regenerate);
    });

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.className = "image-design__file-input";
    fileInput.accept = "image/*";
    fileInput.hidden = true;

    const upload = document.createElement("button");
    upload.type = "button";
    upload.className = "btn btn--ghost image-design__upload";
    upload.textContent = "Subir imagen";
    upload.addEventListener("click", () => {
      fileInput.click();
    });
    fileInput.addEventListener("change", () => {
      uploadBitsDesignImage(bit, input, fileInput, upload);
    });

    actions.append(regenerate, fileInput, upload);
    const edit = buildImageEditField((instructionInput, applyButton) => {
      editBitsDesignImage(bit, instructionInput, applyButton);
    });
    group.append(label, actions, edit, buildBitsQuestionField(bit, input));
    bitsDesignNounControls.appendChild(group);
    updateBitsDesignNav();
  }

  bitsDesignPrevBtn.addEventListener("click", () => {
    if (currentBitsDesignIndex <= 0) return;
    currentBitsDesignIndex -= 1;
    renderBitsDesignItem();
  });

  bitsDesignNextBtn.addEventListener("click", () => {
    if (currentBitsDesignIndex >= designBits.length - 1) return;
    currentBitsDesignIndex += 1;
    renderBitsDesignItem();
  });

  bitsDesignRemoveBtn.addEventListener("click", () => {
    if (!designBits.length) return;
    designBits.splice(currentBitsDesignIndex, 1);
    if (currentBitsDesignIndex >= designBits.length) {
      currentBitsDesignIndex = Math.max(0, designBits.length - 1);
    }
    renderBitsDesignItem();
  });

  bitsDesignBackBtn.addEventListener("click", () => {
    resetBitsDesignState();
    resultOverlay.classList.add("is-open");
    updateDoneButtonState();
  });

  bitsDesignDiscardBtn.addEventListener("click", discardEverything);

  bitsDesignSaveBtn.addEventListener("click", () => {
    if (!designBits.length) {
      alert("No hay bits para guardar.");
      return;
    }
    openClassifyModal(bitsDesignOverlay, () => saveDesignedBitsMaterial());
  });

  bitsDesignTitle.addEventListener("input", () => {
    currentMaterialTitle = bitsDesignTitle.textContent.replace(/\s+/g, " ").trim();
  });

  addSentenceBtn?.addEventListener("click", () => {
    if (currentResultType === "oracion_imagen") {
      appendImageSentenceItem({}, { focus: true });
    } else if (currentResultType === "bits") {
      appendBitsItem({}, { focus: true });
    } else {
      appendSentenceItem("", { focus: true });
    }
    updateDoneButtonState();
  });

  generateMoreSentencesBtn?.addEventListener("click", async () => {
    const existing = getSentencesData();
    const originalLabel = generateMoreSentencesBtn.textContent;
    generateMoreSentencesBtn.disabled = true;
    generateMoreSentencesBtn.textContent = "Generando…";
    try {
      const response = await authorizedFetch("/api/sentences/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          count: 5,
          existing,
          topic: lastSentenceContext.topic || currentMaterialTitle || "",
          grade_level: lastSentenceContext.grade_level || "",
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "No se pudieron generar más oraciones.");
      }
      (data.sentences || []).forEach((sentence) => appendSentenceItem(sentence));
      updateDoneButtonState();
    } catch (error) {
      alert(error.message || "No se pudieron generar más oraciones.");
    } finally {
      generateMoreSentencesBtn.disabled = false;
      generateMoreSentencesBtn.textContent = originalLabel;
    }
  });

  // Question generation: request N questions per type and list them, editable, by category
  const generateQuestionsBtn = document.getElementById("generateQuestionsBtn");
  const questionsResult = document.getElementById("questionsResult");
  const questionCountInputs = Array.from(document.querySelectorAll(".question-row__input"));

  const questionTypeMeta = {};
  questionCountInputs.forEach((input) => {
    const label = input.closest(".question-row").querySelector(".question-row__label");
    questionTypeMeta[input.dataset.questionType] = {
      label: label.textContent,
      color: label.style.color,
    };
  });

  // Serializes the teacher-reviewed question and answer pairs exactly as shown.
  const QUESTION_TYPE_TO_TIPO = {
    literales: "literal",
    inferenciales: "inferencial",
    criticas: "critica",
  };

  function getQuestionsData() {
    const categories = Array.from(questionsResult.querySelectorAll(".questions-result__category"));
    const data = [];

    categories.forEach((category) => {
      const tipo = QUESTION_TYPE_TO_TIPO[category.dataset.type] || category.dataset.type;
      Array.from(category.querySelectorAll(".questions-result__item")).forEach((item) => {
        const question = item.querySelector(".questions-result__question").textContent.trim();
        const expectedAnswer = item.querySelector(".questions-result__answer").textContent.trim();
        if (!question) return;
        data.push({
          tipo,
          pregunta: question,
          respuesta_esperada: expectedAnswer,
          editada_por_docente:
            question !== item.dataset.originalQuestion
            || expectedAnswer !== item.dataset.originalAnswer,
        });
      });
    });

    return data;
  }

  function renderQuestions(questionsByType) {
    questionsResult.innerHTML = "";

    Object.entries(questionsByType).forEach(([type, questions]) => {
      if (!questions.length) return;
      const meta = questionTypeMeta[type] || { label: type, color: "#132a5e" };

      const category = document.createElement("div");
      category.className = "questions-result__category";
      category.dataset.type = type;

      const titleId = `questionsCategory-${type}`;
      const title = document.createElement("div");
      title.className = "questions-result__category-title";
      title.id = titleId;
      title.style.color = meta.color;
      title.textContent = meta.label;
      category.appendChild(title);

      // Semantic list so the questions can be traversed and read aloud (e.g.
      // by a screen reader or TTS) in order, grouped under their category.
      const list = document.createElement("ul");
      list.className = "questions-result__list";
      list.setAttribute("aria-labelledby", titleId);

      questions.forEach((rawQuestion) => {
        const questionText = typeof rawQuestion === "string"
          ? rawQuestion
          : (rawQuestion.pregunta || "");
        const expectedAnswer = typeof rawQuestion === "string"
          ? ""
          : (rawQuestion.respuesta_esperada || "");
        const item = document.createElement("li");
        item.className = "questions-result__item";
        item.dataset.originalQuestion = questionText.trim();
        item.dataset.originalAnswer = expectedAnswer.trim();

        const questionLabel = document.createElement("span");
        questionLabel.className = "questions-result__field-label";
        questionLabel.textContent = "Pregunta";
        const question = document.createElement("div");
        question.className = "questions-result__editable questions-result__question";
        question.contentEditable = "true";
        question.spellcheck = true;
        question.textContent = questionText;

        const answerLabel = document.createElement("span");
        answerLabel.className = "questions-result__field-label";
        answerLabel.textContent = "Respuesta esperada o criterio";
        const answer = document.createElement("div");
        answer.className = "questions-result__editable questions-result__answer";
        answer.contentEditable = "true";
        answer.spellcheck = true;
        answer.dataset.placeholder = "Completa este criterio";
        answer.textContent = expectedAnswer;

        item.append(questionLabel, question, answerLabel, answer);
        list.appendChild(item);
      });

      category.appendChild(list);
      questionsResult.appendChild(category);
    });
  }

  generateQuestionsBtn.addEventListener("click", async () => {
    const counts = {};
    questionCountInputs.forEach((input) => {
      counts[input.dataset.questionType] = Math.max(0, parseInt(input.value, 10) || 0);
    });

    if (!Object.values(counts).some((count) => count > 0)) {
      alert("Indica al menos una pregunta para generar.");
      return;
    }

    const text = resultTranscribedText.textContent.trim();
    if (!text) {
      alert("No hay texto para generar preguntas.");
      return;
    }

    const originalLabel = generateQuestionsBtn.textContent;
    generateQuestionsBtn.disabled = true;
    generateQuestionsBtn.textContent = "Generando...";

    try {
      const response = await authorizedFetch("/api/material/questions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, counts }),
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || "No se pudieron generar las preguntas.");
      }

      renderQuestions(data.questions);
      questionsReady = Object.values(data.questions).some((questions) => questions.length > 0);
      updateDoneButtonState();
    } catch (error) {
      alert(error.message || "No se pudieron generar las preguntas.");
    } finally {
      generateQuestionsBtn.disabled = false;
      generateQuestionsBtn.textContent = originalLabel;
    }
  });
});
