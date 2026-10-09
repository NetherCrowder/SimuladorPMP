(() => {
  const search = document.querySelector("[data-bank-search]");
  const typeFilter = document.querySelector("[data-bank-type]");
  const statusFilter = document.querySelector("[data-bank-status]");
  const results = document.querySelector("[data-bank-results]");
  const rows = [...document.querySelectorAll("[data-bank-row]")];
  if (!search || !typeFilter || !statusFilter || !results || rows.length === 0) return;

  const normalize = (value) => value.trim().toLocaleLowerCase();
  const update = () => {
    const query = normalize(search.value);
    const type = typeFilter.value;
    const status = statusFilter.value;
    let visible = 0;

    document.querySelectorAll("[data-bank-no-results]").forEach((emptyRow) => {
      emptyRow.hidden = true;
    });
    rows.forEach((row) => {
      const matches =
        normalize(row.dataset.question || "").includes(query) &&
        (!type || row.dataset.type === type) &&
        (!status || row.dataset.status === status || row.dataset.validation === status);
      row.hidden = !matches;
      if (matches) visible += 1;
    });

    document.querySelectorAll("[data-bank-section]").forEach((bank) => {
      const bankRows = rows.filter((row) => row.dataset.bankKind === bank.dataset.bankSection);
      const visibleRows = bankRows.filter((row) => !row.hidden).length;
      const noResults = bank.querySelector("[data-bank-no-results]");
      if (noResults) noResults.hidden = visibleRows !== 0;
    });
    results.textContent = `${visible} de ${rows.length} preguntas visibles`;
  };

  [search, typeFilter, statusFilter].forEach((control) => {
    control.addEventListener("input", update);
    control.addEventListener("change", update);
  });
  update();
})();
