(() => {
  const answerForm = document.getElementById("answer-form");
  const caseSet = document.querySelector("[data-case-set]");
  if (answerForm && caseSet) {
    const panels = [...caseSet.querySelectorAll("[data-case-question]")];
    const answers = [...caseSet.querySelectorAll("[data-case-answer]")];
    const progress = caseSet.querySelector("#case-question-progress");
    const status = caseSet.querySelector("#case-question-status");
    const error = caseSet.querySelector("[data-case-error]");
    const previous = caseSet.querySelector("[data-case-previous]");
    const next = caseSet.querySelector("[data-case-next]");
    let activeIndex = Number(caseSet.dataset.activeQuestion) || 0;

    const renderCaseStep = () => {
      panels.forEach((panel, index) => {
        panel.hidden = index !== activeIndex;
      });
      progress.textContent = `Subpregunta ${activeIndex + 1} de ${panels.length}`;
      previous.disabled = activeIndex === 0;
      next.hidden = activeIndex === panels.length - 1;
      status.textContent = answers.every((input) => input.value !== "")
        ? "Caso completo."
        : "Completa cada subpregunta.";
      error.hidden = true;
    };

    caseSet.addEventListener("change", (event) => {
      const choice = event.target.closest("[data-case-choice]");
      if (!choice) return;
      answers[Number(choice.dataset.caseChoice)].value = choice.value;
      renderCaseStep();
    });
    previous.addEventListener("click", () => {
      if (activeIndex > 0) activeIndex -= 1;
      renderCaseStep();
    });
    next.addEventListener("click", () => {
      if (answers[activeIndex].value === "") {
        error.hidden = false;
        return;
      }
      if (activeIndex < panels.length - 1) activeIndex += 1;
      renderCaseStep();
    });
    answerForm.addEventListener("submit", (event) => {
      if (event.submitter?.value === "save") return;
      if (answers.some((input) => input.value === "")) {
        event.preventDefault();
        error.hidden = false;
        const firstUnanswered = answers.findIndex((input) => input.value === "");
        activeIndex = firstUnanswered;
        renderCaseStep();
        error.hidden = false;
        panels[activeIndex].querySelector("input[type=radio]")?.focus();
      }
    });
    renderCaseStep();
  }

  const multipleResponses = document.querySelector("[data-multiple-responses]");
  if (answerForm && multipleResponses) {
    const limit = Number(multipleResponses.dataset.selectionLimit);
    const checkboxes = [...multipleResponses.querySelectorAll('input[name="selected_options"]')];
    const status = multipleResponses.querySelector("[data-selection-status]");
    const prompt = multipleResponses.querySelector("[data-selection-prompt]");
    prompt.textContent = `Seleccione ${limit} opciones`;
    const updateStatus = () => {
      const selected = checkboxes.filter((checkbox) => checkbox.checked).length;
      status.textContent = `Seleccionadas: ${selected} de ${limit}`;
      return selected;
    };
    multipleResponses.addEventListener("change", (event) => {
      if (!event.target.matches('input[name="selected_options"]')) return;
      const selected = updateStatus();
      if (selected > limit) {
        event.target.checked = false;
        event.target.focus();
        updateStatus();
        status.textContent = `Puede seleccionar como máximo ${limit} opciones.`;
      }
    });
    answerForm.addEventListener("submit", (event) => {
      if (event.submitter?.value === "save") return;
      if (updateStatus() !== limit) {
        event.preventDefault();
        status.textContent = `Seleccione exactamente ${limit} opciones para continuar.`;
        checkboxes.find((checkbox) => !checkbox.checked)?.focus();
      }
    });
    updateStatus();
  }

  const dragDrop = document.querySelector("[data-drag-drop]");
  if (dragDrop) {
    const sources = [...dragDrop.querySelectorAll(".drag-source")];
    const targets = [...dragDrop.querySelectorAll(".drag-drop-zone")];
    const selects = [...dragDrop.querySelectorAll('select[name="drag_match"]')];
    const assignments = [...dragDrop.querySelectorAll("[data-target-assignment]")];
    const status = dragDrop.querySelector("[data-drag-status]");
    let selectedItem = null;
    let pointerDrag = null;
    let draggedItem = null;
    let skipClick = false;

    const updateAssignments = () => {
      assignments.forEach((assignment, targetIndex) => {
        const itemNames = selects.flatMap((select, itemIndex) =>
          Number(select.value) === targetIndex
            ? [sources[itemIndex].textContent.trim()]
            : []
        );
        assignment.textContent = itemNames.length === 0
          ? "Suelta o selecciona una ficha"
          : itemNames.join(", ");
      });
    };
    const assign = (itemIndex, targetIndex) => {
      if (!selects[itemIndex] || !targets[targetIndex]) return;
      selects[itemIndex].value = String(targetIndex);
      selects[itemIndex].dispatchEvent(new Event("change", { bubbles: true }));
      status.textContent = `${sources[itemIndex].textContent.trim()} asignada a ${targets[targetIndex].textContent.trim()}.`;
      selectedItem = null;
      sources.forEach((source) => source.setAttribute("aria-pressed", "false"));
      updateAssignments();
    };

    sources.forEach((source) => {
      source.addEventListener("click", () => {
        if (skipClick) {
          skipClick = false;
          return;
        }
        selectedItem = Number(source.dataset.itemIndex);
        sources.forEach((item) =>
          item.setAttribute("aria-pressed", String(item === source))
        );
        status.textContent = `${source.textContent.trim()} seleccionada. Elige una casilla receptora.`;
      });
      source.addEventListener("dragstart", (event) => {
        draggedItem = Number(source.dataset.itemIndex);
        event.dataTransfer.setData("text/plain", String(draggedItem));
        event.dataTransfer.effectAllowed = "move";
      });
      source.addEventListener("pointerdown", (event) => {
        if (event.pointerType === "mouse") return;
        pointerDrag = {
          itemIndex: Number(source.dataset.itemIndex),
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          moved: false
        };
      });
    });

    document.addEventListener("pointermove", (event) => {
      if (!pointerDrag || pointerDrag.pointerId !== event.pointerId) return;
      if (Math.hypot(event.clientX - pointerDrag.startX, event.clientY - pointerDrag.startY) > 8) {
        pointerDrag.moved = true;
      }
    });
    document.addEventListener("pointerup", (event) => {
      if (!pointerDrag || pointerDrag.pointerId !== event.pointerId) return;
      if (pointerDrag.moved) {
        const target = document.elementFromPoint(event.clientX, event.clientY)?.closest("[data-target-index]");
        if (target) {
          assign(pointerDrag.itemIndex, Number(target.dataset.targetIndex));
          skipClick = true;
        }
      }
      pointerDrag = null;
    });

    targets.forEach((target) => {
      target.addEventListener("dragover", (event) => event.preventDefault());
      target.addEventListener("drop", (event) => {
        event.preventDefault();
        const itemIndex = Number(event.dataTransfer.getData("text/plain") || draggedItem);
        assign(itemIndex, Number(target.dataset.targetIndex));
      });
      target.addEventListener("click", () => {
        if (selectedItem !== null) assign(selectedItem, Number(target.dataset.targetIndex));
      });
    });
    selects.forEach((select) => select.addEventListener("change", updateAssignments));
    answerForm.addEventListener("submit", (event) => {
      if (event.submitter?.value === "save") return;
      if (selects.some((select) => select.value === "")) {
        event.preventDefault();
        status.textContent = "Asigna cada ficha a una casilla antes de continuar.";
        selects.find((select) => select.value === "")?.focus();
      }
    });
    updateAssignments();
  }

  const zoomDialog = document.querySelector("[data-image-zoom]");
  const zoomOpen = document.querySelector("[data-image-zoom-open]");
  const zoomImage = document.querySelector("[data-zoom-image]");
  const zoomLevel = document.querySelector("[data-zoom-level]");
  if (zoomDialog && zoomOpen && zoomImage && zoomLevel) {
    const sourceImage = document.querySelector(".exam-artifact-container img");
    const viewport = zoomDialog.querySelector("[data-zoom-viewport]");
    const updateZoom = () => {
      zoomImage.style.transform = `scale(${zoomLevel.value})`;
      zoomLevel.setAttribute("aria-valuetext", `${zoomLevel.value} veces`);
    };
    zoomOpen.addEventListener("click", () => {
      zoomImage.onload = () => {
        zoomImage.style.width = `${Math.min(zoomImage.naturalWidth, 1200)}px`;
        zoomLevel.value = "1";
        updateZoom();
        viewport.scrollTo(0, 0);
      };
      zoomImage.src = sourceImage.currentSrc || sourceImage.src;
      if (zoomImage.complete && zoomImage.naturalWidth) zoomImage.onload();
      zoomDialog.showModal();
    });
    zoomDialog.querySelector("[data-image-zoom-close]").addEventListener("click", () => zoomDialog.close());
    zoomDialog.querySelector("[data-zoom-out]").addEventListener("click", () => {
      zoomLevel.value = String(Math.max(1, Number(zoomLevel.value) - 0.25));
      updateZoom();
    });
    zoomDialog.querySelector("[data-zoom-in]").addEventListener("click", () => {
      zoomLevel.value = String(Math.min(4, Number(zoomLevel.value) + 0.25));
      updateZoom();
    });
    zoomLevel.addEventListener("input", updateZoom);
    zoomDialog.addEventListener("click", (event) => {
      if (event.target === zoomDialog) zoomDialog.close();
    });
  }

  const answerFormForHotspot = answerForm;
  const hotspotImage = document.querySelector("[data-hotspot-image]");
  const hotspotCanvas = document.querySelector("[data-hotspot-canvas]");
  if (hotspotImage && hotspotCanvas && answerFormForHotspot) {
    const hotspotInput = answerFormForHotspot.querySelector('[name="hotspot_point"]');
    const hotspotStatus = answerFormForHotspot.querySelector("[data-hotspot-status]");
    const marker = hotspotCanvas.querySelector("[data-hotspot-marker]");
    let keyboardPoint = { x: 50, y: 50 };
    const restoreMarker = () => {
      if (!hotspotImage.naturalWidth || !hotspotImage.naturalHeight) return;
      hotspotCanvas.setAttribute(
        "viewBox",
        `0 0 ${hotspotImage.naturalWidth} ${hotspotImage.naturalHeight}`
      );
      if (hotspotInput.value) {
        const [x, y] = hotspotInput.value.split(",").map(Number);
        keyboardPoint = { x, y };
        showHotspotPoint(x, y);
      }
    };
    const showHotspotPoint = (x, y) => {
      marker.hidden = false;
      marker.setAttribute("cx", String((x / 100) * hotspotImage.naturalWidth));
      marker.setAttribute("cy", String((y / 100) * hotspotImage.naturalHeight));
      marker.setAttribute(
        "r",
        String(Math.max(hotspotImage.naturalWidth, hotspotImage.naturalHeight) * 0.012)
      );
      hotspotStatus.textContent = `Punto seleccionado: X ${x.toFixed(2)}%, Y ${y.toFixed(2)}%.`;
    };
    const saveHotspotPoint = (x, y) => {
      keyboardPoint = { x, y };
      hotspotInput.value = `${x.toFixed(3)},${y.toFixed(3)}`;
      showHotspotPoint(x, y);
    };
    hotspotCanvas.addEventListener("click", (event) => {
      const matrix = hotspotCanvas.getScreenCTM();
      if (!matrix) return;
      const point = hotspotCanvas.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      const imagePoint = point.matrixTransform(matrix.inverse());
      const x = (imagePoint.x / hotspotImage.naturalWidth) * 100;
      const y = (imagePoint.y / hotspotImage.naturalHeight) * 100;
      if (x < 0 || x > 100 || y < 0 || y > 100) return;
      saveHotspotPoint(x, y);
    });
    hotspotCanvas.addEventListener("keydown", (event) => {
      const step = event.shiftKey ? 5 : 1;
      const offsets = {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, -step],
        ArrowDown: [0, step]
      };
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        saveHotspotPoint(keyboardPoint.x, keyboardPoint.y);
        return;
      }
      const offset = offsets[event.key];
      if (!offset) return;
      event.preventDefault();
      saveHotspotPoint(
        Math.min(100, Math.max(0, keyboardPoint.x + offset[0])),
        Math.min(100, Math.max(0, keyboardPoint.y + offset[1]))
      );
    });
    hotspotImage.addEventListener("load", restoreMarker);
    if (hotspotImage.complete) restoreMarker();
  }

  const questionNumbers = [...document.querySelectorAll("[data-question-number]")];
  const questionTypeFilter = document.querySelector("[data-question-type-filter]");
  const questionStatusFilter = document.querySelector("[data-question-status-filter]");
  const questionFilterResults = document.querySelector("[data-question-filter-results]");
  if (questionNumbers.length && questionTypeFilter && questionStatusFilter && questionFilterResults) {
    const updateQuestionNavigation = () => {
      const type = questionTypeFilter.value;
      const status = questionStatusFilter.value;
      let visible = 0;
      questionNumbers.forEach((question) => {
        const matches =
          (!type || question.dataset.questionType === type) &&
          (!status || question.dataset.questionStatus === status);
        question.hidden = !matches;
        if (matches) visible += 1;
      });
      questionFilterResults.textContent = `${visible} de ${questionNumbers.length} preguntas`;
    };
    questionTypeFilter.addEventListener("change", updateQuestionNavigation);
    questionStatusFilter.addEventListener("change", updateQuestionNavigation);
    updateQuestionNavigation();
  }

  const timer = document.getElementById("timer");
  if (!timer) return;
  let remaining = Number.parseInt(timer.dataset.seconds, 10);
  if (!Number.isFinite(remaining)) return;

  const render = () => {
    const hours = Math.floor(remaining / 3600);
    const minutes = Math.floor((remaining % 3600) / 60);
    const seconds = remaining % 60;
    timer.textContent = [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
    if (remaining <= 60) timer.classList.add("urgent");
  };

  render();
  const interval = window.setInterval(() => {
    remaining -= 1;
    render();
    if (remaining <= 0) {
      window.clearInterval(interval);
      window.location.reload();
    }
  }, 1000);
})();
