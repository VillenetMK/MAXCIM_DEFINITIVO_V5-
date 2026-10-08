document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  function authorizedFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-CSRF-Token", csrfToken);
    return fetch(url, { ...options, headers });
  }

  const createForm = document.getElementById("createTemaForm");
  const createButton = document.getElementById("createTemaBtn");
  const createError = document.getElementById("temasFormError");
  const createNameInput = document.getElementById("newTemaNombreInput");

  const renameOverlay = document.getElementById("renameTemaOverlay");
  const renameForm = document.getElementById("renameTemaForm");
  const renameInput = document.getElementById("renameTemaNombreInput");
  const renameDescription = document.getElementById("renameTemaDescription");
  const renameError = document.getElementById("renameTemaError");
  const renameCancelButton = document.getElementById("renameTemaCancelBtn");
  const renameCloseButton = document.getElementById("renameTemaCloseBtn");
  const renameConfirmButton = document.getElementById("renameTemaConfirmBtn");

  const deleteOverlay = document.getElementById("deleteTemaOverlay");
  const deleteName = document.getElementById("deleteTemaName");
  const deleteError = document.getElementById("deleteTemaError");
  const deleteCancelButton = document.getElementById("deleteTemaCancelBtn");
  const deleteCloseButton = document.getElementById("deleteTemaCloseBtn");
  const deleteConfirmButton = document.getElementById("deleteTemaConfirmBtn");

  let pendingRenameId = null;
  let pendingDeleteId = null;
  let activeOverlay = null;
  let lastFocusedElement = null;

  function showError(element, message) {
    element.textContent = message;
    element.hidden = false;
  }

  function clearError(element) {
    element.textContent = "";
    element.hidden = true;
  }

  async function readJson(response) {
    return response.json().catch(() => ({}));
  }

  function focusableElements(overlay) {
    return Array.from(overlay.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
    )).filter((element) => !element.hidden);
  }

  function openOverlay(overlay, initialFocus) {
    lastFocusedElement = document.activeElement;
    activeOverlay = overlay;
    overlay.classList.add("is-open");
    overlay.setAttribute("aria-hidden", "false");
    window.requestAnimationFrame(() => initialFocus?.focus());
  }

  function closeOverlay(overlay) {
    overlay.classList.remove("is-open");
    overlay.setAttribute("aria-hidden", "true");
    activeOverlay = null;
    const focusTarget = lastFocusedElement;
    lastFocusedElement = null;
    focusTarget?.focus();
  }

  function closeRenameOverlay() {
    closeOverlay(renameOverlay);
    pendingRenameId = null;
    clearError(renameError);
  }

  function closeDeleteOverlay() {
    closeOverlay(deleteOverlay);
    pendingDeleteId = null;
    clearError(deleteError);
  }

  createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError(createError);

    const originalLabel = createButton.innerHTML;
    createButton.disabled = true;
    createButton.textContent = "Agregando…";

    try {
      const response = await authorizedFetch("/api/temas", {
        method: "POST",
        body: new FormData(createForm),
      });
      const data = await readJson(response);
      if (!response.ok) {
        throw new Error(data.error || "No se pudo crear el tema.");
      }

      const url = new URL(window.location.href);
      url.searchParams.set("periodo", String(data.id_periodo));
      window.location.assign(url.toString());
    } catch (error) {
      showError(createError, error.message || "No se pudo crear el tema.");
      createButton.disabled = false;
      createButton.innerHTML = originalLabel;
    }
  });

  createNameInput.addEventListener("input", () => clearError(createError));

  document.querySelectorAll(".tema-rename-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const card = button.closest("[data-tema-id]");
      const currentName = card?.querySelector("[data-tema-nombre]")?.textContent.trim() || "";
      pendingRenameId = button.dataset.temaId;
      renameInput.value = currentName;
      renameDescription.textContent = `Periodo: ${button.dataset.temaPeriodo || "Sin periodo"}`;
      clearError(renameError);
      openOverlay(renameOverlay, renameInput);
      renameInput.select();
    });
  });

  renameForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!pendingRenameId) return;
    clearError(renameError);

    const originalLabel = renameConfirmButton.textContent;
    renameConfirmButton.disabled = true;
    renameConfirmButton.textContent = "Guardando…";

    try {
      const formData = new FormData();
      formData.append("nombre", renameInput.value.trim());
      const response = await authorizedFetch(`/api/temas/${pendingRenameId}`, {
        method: "PATCH",
        body: formData,
      });
      const data = await readJson(response);
      if (!response.ok) {
        throw new Error(data.error || "No se pudo renombrar el tema.");
      }
      window.location.reload();
    } catch (error) {
      showError(renameError, error.message || "No se pudo renombrar el tema.");
      renameConfirmButton.disabled = false;
      renameConfirmButton.textContent = originalLabel;
      renameInput.focus();
    }
  });

  renameInput.addEventListener("input", () => clearError(renameError));
  renameCancelButton.addEventListener("click", closeRenameOverlay);
  renameCloseButton.addEventListener("click", closeRenameOverlay);

  document.querySelectorAll(".tema-delete-btn").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.disabled) return;
      const card = button.closest("[data-tema-id]");
      pendingDeleteId = button.dataset.temaId;
      deleteName.textContent = card?.querySelector("[data-tema-nombre]")?.textContent.trim() || "este tema";
      clearError(deleteError);
      openOverlay(deleteOverlay, deleteCancelButton);
    });
  });

  deleteConfirmButton.addEventListener("click", async () => {
    if (!pendingDeleteId) return;
    clearError(deleteError);

    const originalLabel = deleteConfirmButton.textContent;
    deleteConfirmButton.disabled = true;
    deleteConfirmButton.textContent = "Eliminando…";

    try {
      const response = await authorizedFetch(`/api/temas/${pendingDeleteId}`, {
        method: "DELETE",
      });
      const data = await readJson(response);
      if (!response.ok) {
        throw new Error(data.error || "No se pudo eliminar el tema.");
      }
      window.location.reload();
    } catch (error) {
      showError(deleteError, error.message || "No se pudo eliminar el tema.");
      deleteConfirmButton.disabled = false;
      deleteConfirmButton.textContent = originalLabel;
    }
  });

  deleteCancelButton.addEventListener("click", closeDeleteOverlay);
  deleteCloseButton.addEventListener("click", closeDeleteOverlay);

  [renameOverlay, deleteOverlay].forEach((overlay) => {
    overlay.addEventListener("mousedown", (event) => {
      if (event.target !== overlay) return;
      if (overlay === renameOverlay) closeRenameOverlay();
      if (overlay === deleteOverlay) closeDeleteOverlay();
    });
  });

  document.addEventListener("keydown", (event) => {
    if (!activeOverlay) return;

    if (event.key === "Escape") {
      event.preventDefault();
      if (activeOverlay === renameOverlay) closeRenameOverlay();
      if (activeOverlay === deleteOverlay) closeDeleteOverlay();
      return;
    }

    if (event.key !== "Tab") return;
    const focusable = focusableElements(activeOverlay);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
});
