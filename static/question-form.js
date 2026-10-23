(() => {
  const typeSelect = document.getElementById("question-type");
  const description = document.getElementById("question-type-description");
  const imageInput = document.querySelector('input[name="image"]');
  const imagePreview = document.getElementById("admin-artifact-preview");
  const adminImage = document.getElementById("admin-artifact-image");
  const hotspotRegion = document.getElementById("hotspot-admin-region");
  const adminImageWidth = document.querySelector('[name="hotspot_image_width"]');
  const adminImageHeight = document.querySelector('[name="hotspot_image_height"]');
  const caseQuestionCount = document.getElementById("case-question-count");
  const caseEditors = [...document.querySelectorAll("[data-case-editor]")];
  if (!typeSelect || !description) return;

  const descriptions = {
    case_set: "Estudio de caso extenso seguido de una serie de 3 a 5 preguntas encadenadas.",
    visual_based: "Reactivo basado en un artefacto gráfico real, como un tablero Kanban o un dashboard.",
    multiple_choice: "Selección única de la mejor alternativa entre 4 opciones (A, B, C, D).",
    multiple_responses: "Reactivo con más de una opción correcta (usualmente 2 o 3).",
    drag_drop: "Emparejamiento interactivo de conceptos o acciones en dos columnas relacionadas.",
    hotspot: "Identificación visual directa sobre un diagrama o gráfico técnico.",
    pull_down: "Párrafo con uno o varios espacios en blanco interactivos."
  };

  const update = () => {
    const selected = typeSelect.value;
    description.textContent = descriptions[selected] || "";
    document.querySelectorAll(".question-type-fields").forEach((section) => {
      const visible = section.dataset.questionType.split(" ").includes(selected);
      section.hidden = !visible;
      section.querySelectorAll("input, textarea, select").forEach((field) => {
        field.disabled = !visible;
      });
    });
    document.querySelectorAll("[data-required-for]").forEach((field) => {
      field.required = field.dataset.requiredFor.split(" ").includes(selected);
    });
    const count = Number(caseQuestionCount?.value || 3);
    caseEditors.forEach((editor) => {
      const visible = selected === "case_set" && Number(editor.dataset.caseEditor) <= count;
      editor.hidden = !visible;
      editor.querySelectorAll("input, textarea, select").forEach((field) => {
        field.disabled = !visible;
        field.required = visible;
      });
    });
    const promptLabel = document.getElementById("prompt-label");
    if (promptLabel) {
      promptLabel.firstChild.textContent = selected === "case_set" ? "Descripción del caso" :
        selected === "pull_down" ? "Párrafo (usa [[blank]] para cada espacio)" : "Pregunta";
    }
  };

  const updateHotspotRegion = () => {
    if (!hotspotRegion) return;
    const coordinates = ["x", "y", "width", "height"].map((name) =>
      Number(document.querySelector(`[name="hotspot_${name}"]`)?.value)
    );
    if (coordinates.every(Number.isFinite) && coordinates[2] > 0 && coordinates[3] > 0) {
      hotspotRegion.style.display = "block";
      hotspotRegion.style.left = `${coordinates[0]}%`;
      hotspotRegion.style.top = `${coordinates[1]}%`;
      hotspotRegion.style.width = `${coordinates[2]}%`;
      hotspotRegion.style.height = `${coordinates[3]}%`;
    } else {
      hotspotRegion.style.display = "none";
    }
  };

  const updateImageDimensions = () => {
    if (!adminImage || !adminImageWidth || !adminImageHeight ||
        !adminImage.naturalWidth || !adminImage.naturalHeight) return;
    adminImageWidth.value = String(adminImage.naturalWidth);
    adminImageHeight.value = String(adminImage.naturalHeight);
  };

  imageInput?.addEventListener("change", () => {
    const file = imageInput.files[0];
    if (!file || !adminImage || !imagePreview) return;
    adminImage.src = URL.createObjectURL(file);
    imagePreview.hidden = false;
  });
  if (adminImage) {
    adminImage.addEventListener("load", updateImageDimensions);
    updateImageDimensions();
    adminImage.addEventListener("click", (event) => {
      if (typeSelect.value !== "hotspot") return;
      const bounds = adminImage.getBoundingClientRect();
      const x = ((event.clientX - bounds.left) / bounds.width) * 100;
      const y = ((event.clientY - bounds.top) / bounds.height) * 100;
      document.querySelector('[name="hotspot_x"]').value = x.toFixed(2);
      document.querySelector('[name="hotspot_y"]').value = y.toFixed(2);
      updateHotspotRegion();
    });
  }
  document.querySelectorAll('[name^="hotspot_"]').forEach((input) => {
    input.addEventListener("input", updateHotspotRegion);
  });

  typeSelect.addEventListener("change", update);
  caseQuestionCount?.addEventListener("change", update);
  update();
  updateHotspotRegion();
})();
