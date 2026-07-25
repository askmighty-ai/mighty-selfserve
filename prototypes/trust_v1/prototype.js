/* Prototype-only interactions. No framework. Respects prefers-reduced-motion. */
(function () {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function advanceDiscovery() {
    const steps = Array.from(document.querySelectorAll("[data-scan-step]"));
    if (!steps.length) return;

    const order = ["connected", "checking", "matching", "preparing"];
    let index = 0;

    function paint(activeIndex) {
      steps.forEach((el) => {
        const key = el.getAttribute("data-scan-step");
        const pos = order.indexOf(key);
        el.classList.remove("is-done", "is-live", "is-upcoming");
        const mark = el.querySelector(".scan-mark");
        if (pos < activeIndex) {
          el.classList.add("is-done");
          if (mark) mark.textContent = "✓";
        } else if (pos === activeIndex) {
          el.classList.add("is-live");
          if (mark) mark.textContent = "•";
        } else {
          el.classList.add("is-upcoming");
          if (mark) mark.textContent = "";
        }
      });
    }

    paint(0);
    if (reduce) {
      paint(order.length - 1);
      steps.forEach((el) => {
        el.classList.remove("is-live", "is-upcoming");
        el.classList.add("is-done");
        const mark = el.querySelector(".scan-mark");
        if (mark) mark.textContent = "✓";
      });
      return;
    }

    const timer = window.setInterval(() => {
      index += 1;
      if (index >= order.length) {
        window.clearInterval(timer);
        window.setTimeout(() => {
          const link = document.querySelector("[data-auto-advance]");
          if (link) link.classList.add("is-ready");
        }, 450);
        return;
      }
      paint(index);
    }, 1100);
  }

  function revealReviewRows() {
    document.querySelectorAll(".account-row").forEach((row) => {
      row.classList.add("reveal-row");
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (document.body.dataset.proto === "discover") advanceDiscovery();
    if (document.body.dataset.proto === "review") revealReviewRows();
  });
})();
