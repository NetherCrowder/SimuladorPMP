(() => {
  const form = document.getElementById("practice-form");
  const warning = document.getElementById("combination-warning");
  const availability = document.getElementById("availability-status");
  const startButton = document.getElementById("start-button");
  const routeSelect = document.getElementById("learning-route");
  if (!form || !warning || !availability || !startButton || !routeSelect) return;

  let availableCount = null;
  let canStart = false;
  let requestNumber = 0;

  const selectedLength = () => Number(form.querySelector('select[name="length"]').value);
  const selectedMode = () => selectedLength() >= 60 ? "official" : "practice";
  const selectedCategories = () => [...form.querySelectorAll('input[name="category"]:checked')];
  const selectionIsValid = () => selectedMode() === "official" ||
    Boolean(routeSelect.value && selectedCategories().length);

  const updateVisibility = () => {
    const isOfficial = selectedMode() === "official";
    form.querySelectorAll("[data-practice-options]").forEach((element) => {
      element.hidden = isOfficial;
      element.querySelectorAll("input, select").forEach((input) => {
        input.disabled = isOfficial;
      });
    });
    form.querySelectorAll("[data-axis]").forEach((group) => {
      const active = !isOfficial && group.dataset.axis === routeSelect.value;
      group.hidden = !active;
      group.disabled = !active;
      group.querySelectorAll('input[name="category"]').forEach((input) => {
        input.disabled = !active;
        if (!active) input.checked = false;
      });
    });
    availability.hidden = isOfficial;
    warning.hidden = true;
    if (isOfficial) {
      availableCount = null;
      canStart = true;
      availability.textContent = "";
      startButton.disabled = false;
    } else {
      startButton.disabled = !selectionIsValid() || !canStart ||
        availableCount < selectedLength();
    }
  };

  const update = async () => {
    updateVisibility();
    if (selectedMode() === "official" ||
        !routeSelect.value || !selectedCategories().length) {
      canStart = selectedMode() === "official";
      updateVisibility();
      if (selectedMode() === "practice") {
        availableCount = null;
        availability.textContent = "Selecciona una ruta y un tema para consultar disponibilidad.";
      }
      return;
    }

    const currentRequest = ++requestNumber;
    canStart = false;
    availableCount = null;
    availability.hidden = false;
    availability.textContent = "Consultando preguntas compatibles…";
    startButton.disabled = true;
    const params = new URLSearchParams(new FormData(form));
    params.set("mode", "practice");
    try {
      const response = await fetch(`/practice/availability?${params.toString()}`);
      const result = await response.json();
      if (currentRequest !== requestNumber) return;
      if (!response.ok) throw new Error(result.error || "No se pudo consultar la disponibilidad.");
      availableCount = result.available;
      canStart = result.ready;
      availability.textContent = result.message;
      updateVisibility();
    } catch (error) {
      if (currentRequest !== requestNumber) return;
      availability.textContent = error.message;
      startButton.disabled = true;
    }
  };

  form.addEventListener("change", (event) => {
    if (event.target === routeSelect) {
      form.querySelectorAll('input[name="category"]').forEach((input) => {
        input.checked = false;
      });
    }
    update();
  });
  form.addEventListener("submit", (event) => {
    if (selectedMode() === "practice" &&
        (!selectionIsValid() || !canStart || availableCount === null ||
         availableCount < selectedLength())) {
      event.preventDefault();
      warning.hidden = false;
      warning.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });
  updateVisibility();
})();
