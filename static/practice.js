(() => {
  const form = document.getElementById("practice-form");
  const combinationsElement = document.getElementById("valid-combinations");
  const warning = document.getElementById("combination-warning");
  const availability = document.getElementById("availability-status");
  const startButton = document.getElementById("start-button");
  if (!form || !combinationsElement || !warning || !availability || !startButton) return;

  const validCombinations = new Set(
    JSON.parse(combinationsElement.textContent).map(([method, eco]) => `${method}:${eco}`)
  );

  const selectionIsValid = () => {
    const methods = [...form.querySelectorAll('input[name="methodology"]:checked')].map((input) => input.value);
    const domains = [...form.querySelectorAll('input[name="eco"]:checked')].map((input) => input.value);
    return methods.length > 0 && domains.length > 0 &&
      methods.every((method) => domains.every((domain) => validCombinations.has(`${method}:${domain}`)));
  };

  let availableCount = availability.dataset.count === "" ? null : Number(availability.dataset.count);
  let requestNumber = 0;

  const update = async () => {
    const hasSelection = form.querySelector('input[name="methodology"]:checked') &&
      form.querySelector('input[name="eco"]:checked');
    warning.hidden = !hasSelection || selectionIsValid();
    const groupsSelected = form.querySelector('input[name="performance"]:checked') &&
      form.querySelector('input[name="methodology"]:checked') &&
      form.querySelector('input[name="eco"]:checked');
    const currentRequest = ++requestNumber;
    if (!groupsSelected || !selectionIsValid()) {
      availableCount = null;
      availability.textContent = "Selecciona una combinación válida en cada grupo para consultar disponibilidad.";
      startButton.disabled = true;
      return;
    }
    availability.textContent = "Consultando preguntas compatibles…";
    startButton.disabled = true;
    const params = new URLSearchParams(new FormData(form));
    try {
      const response = await fetch(`/practice/availability?${params.toString()}`);
      const result = await response.json();
      if (currentRequest !== requestNumber) return;
      if (!response.ok) throw new Error(result.error || "No se pudo consultar la disponibilidad.");
      availableCount = result.available;
      availability.textContent = `Preguntas compatibles disponibles: ${availableCount}.`;
      updateStartButton();
    } catch (error) {
      if (currentRequest !== requestNumber) return;
      availableCount = null;
      availability.textContent = error.message;
      startButton.disabled = true;
    }
  };

  const updateStartButton = () => {
    const length = Number(form.querySelector('select[name="length"]').value);
    startButton.disabled = !selectionIsValid() || availableCount === null || availableCount < length;
  };

  form.addEventListener("change", update);
  form.querySelector('select[name="length"]').addEventListener("change", updateStartButton);
  form.addEventListener("submit", (event) => {
    if (!selectionIsValid() || availableCount === null ||
        availableCount < Number(form.querySelector('select[name="length"]').value)) {
      event.preventDefault();
      warning.hidden = false;
      warning.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });
  update();
})();
