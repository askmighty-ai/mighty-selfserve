/* Living Calm V1 — prototype helpers (no frameworks) */
(function () {
  const params = new URLSearchParams(window.location.search);
  const state = params.get("state") || "clear";
  const valid = new Set(["clear", "attention", "opportunity"]);
  const active = valid.has(state) ? state : "clear";

  document.documentElement.dataset.state = active;

  document.querySelectorAll("[data-bind-state]").forEach((root) => {
    root.dataset.state = active;
    root.querySelectorAll("[data-when]").forEach((node) => {
      const when = node.getAttribute("data-when");
      node.hidden = when !== active;
    });
  });

  document.querySelectorAll(".quiet-field").forEach((field) => {
    field.classList.remove("is-settled", "is-attention", "is-opportunity");
    if (active === "clear") field.classList.add("is-settled");
    if (active === "attention") field.classList.add("is-attention");
    if (active === "opportunity") field.classList.add("is-opportunity");

    field.querySelectorAll(".field-point").forEach((p) => {
      p.classList.remove("is-signal", "is-opportunity");
    });
    const signal = field.querySelector('[data-point="signal"]');
    if (signal && active === "attention") signal.classList.add("is-signal");
    if (signal && active === "opportunity") signal.classList.add("is-opportunity");
  });

  document.querySelectorAll(".state-switch a").forEach((a) => {
    const href = new URL(a.href, window.location.href);
    if ((href.searchParams.get("state") || "clear") === active) {
      a.classList.add("is-active");
      a.setAttribute("aria-current", "page");
    }
  });
})();
