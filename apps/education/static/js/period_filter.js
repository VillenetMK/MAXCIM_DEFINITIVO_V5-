document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-period-filter]").forEach((filter) => {
    const yearSelect = filter.querySelector("[data-period-year]");
    const periodSelect = filter.querySelector("[data-period-select]");
    // Opcional: listados que además filtran por tema (p.ej. las interacciones
    // de un alumno). El modal de subir material maneja su propio <select> de
    // tema aparte (sin este atributo), así que aquí no interfiere.
    const temaSelect = filter.querySelector("[data-tema-select]");
    // Opcional, un nivel más abajo de tema (por ahora solo el historial de un
    // alumno): a diferencia de temaSelect, sus opciones ya llegan acotadas al
    // tema elegido -el propio servidor las resuelve en cada recarga (ver
    // resolve_material_filter en app.py)-, así que aquí no hace falta un
    // filterMateriales() que oculte opciones: basta con resetearlo cuando
    // cambia algo por encima (periodo o tema vuelven a dejarlo sin sentido) y
    // sumarlo a la navegación.
    const materialSelect = filter.querySelector("[data-material-select]");
    // Los filtros de listados navegan al elegir periodo (?periodo=<id> en la
    // URL). El selector del modal de subir material es un campo de
    // formulario más: solo debe acotar las opciones por año, sin navegar.
    const shouldNavigate = filter.dataset.periodNav !== "false";

    if (!yearSelect || !periodSelect) return;

    const periodOptions = Array.from(periodSelect.options).filter((option) => option.value);
    const temaOptions = temaSelect
      ? Array.from(temaSelect.options).filter((option) => option.value)
      : [];

    function filterPeriods() {
      const selectedYear = yearSelect.value;

      periodOptions.forEach((option) => {
        const isVisible = !selectedYear || option.dataset.anio === selectedYear;
        option.hidden = !isVisible;
        option.disabled = !isVisible;
      });
    }

    // Acota el <select> de tema al periodo elegido, igual que hace
    // filterUploadTemas() en material.js para el modal de subir material.
    function filterTemas(resetSelection = true) {
      if (!temaSelect) return;
      if (resetSelection) temaSelect.value = "";

      const selectedPeriodo = periodSelect.value;
      temaOptions.forEach((option) => {
        const isVisible = Boolean(selectedPeriodo) && option.dataset.periodo === selectedPeriodo;
        option.hidden = !isVisible;
        option.disabled = !isVisible;
      });
    }

    function navigate() {
      const url = new URL(window.location.href);

      // Importante: SIEMPRE se fija el parámetro (nunca se borra) una vez
      // que la docente tocó el filtro, aunque elija "Todos los periodos" /
      // "Todos los temas" (valor ""). Algunas vistas (avance del aula,
      // historial del alumno) arrancan sin filtro en la URL acotando por
      // defecto al periodo vigente para no listar de una todo el historial;
      // si aquí se borrara el parámetro al elegir "Todos", la página
      // recargada volvería a verse como "sin elegir" y regresaría sola al
      // periodo vigente en vez de respetar el "Todos" que la docente pidió.
      url.searchParams.set("periodo", periodSelect.value);
      if (temaSelect) {
        url.searchParams.set("tema", temaSelect.value);
      }
      if (materialSelect) {
        url.searchParams.set("material", materialSelect.value);
      }

      window.location.assign(url.toString());
    }

    filterPeriods();
    // Al cargar la página no se debe resetear el tema ni el material ya
    // elegidos por el servidor (el <select> de periodo ya viene con el valor
    // que le corresponde a ese tema, y el de material con las opciones de
    // ese tema).
    filterTemas(false);

    yearSelect.addEventListener("change", () => {
      periodSelect.value = "";
      filterPeriods();
      filterTemas();
      // El tema recién quedó sin elegir: cualquier material anterior ya no
      // pertenece a ningún tema visible.
      if (materialSelect) materialSelect.value = "";
    });

    if (shouldNavigate) {
      periodSelect.addEventListener("change", () => {
        filterTemas();
        if (materialSelect) materialSelect.value = "";
        navigate();
      });
      temaSelect?.addEventListener("change", () => {
        // El material seguía siendo del tema anterior: sin recargar todavía
        // no hay de dónde sacar los del tema nuevo, así que se limpia y la
        // propia recarga lo repuebla ya acotado (ver resolve_material_filter).
        if (materialSelect) materialSelect.value = "";
        navigate();
      });
      materialSelect?.addEventListener("change", navigate);
    } else {
      periodSelect.addEventListener("change", () => filterTemas());
    }
  });
});
