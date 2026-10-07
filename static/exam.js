(() => {
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
