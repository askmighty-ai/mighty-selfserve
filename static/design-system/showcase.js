/**
 * Showcase-only interactions for modal focus trap / dismiss.
 * Not loaded by production customer pages.
 */
(function () {
  "use strict";

  function focusable(root) {
    return Array.from(
      root.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      )
    ).filter(function (el) {
      return !el.hasAttribute("disabled") && el.getAttribute("aria-hidden") !== "true";
    });
  }

  function openModal(id) {
    var root = document.getElementById(id + "-root");
    if (!root) return;
    root.hidden = false;
    var dialog = root.querySelector('[role="dialog"]');
    var previous = document.activeElement;
    root._mdsPreviousFocus = previous;
    var nodes = focusable(dialog || root);
    (nodes[0] || dialog || root).focus();

    function onKey(ev) {
      if (ev.key === "Escape") {
        closeModal(id);
        return;
      }
      if (ev.key !== "Tab" || !nodes.length) return;
      var first = nodes[0];
      var last = nodes[nodes.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    }
    root._mdsKeyHandler = onKey;
    document.addEventListener("keydown", onKey);
  }

  function closeModal(id) {
    var root = document.getElementById(id + "-root");
    if (!root) return;
    root.hidden = true;
    if (root._mdsKeyHandler) {
      document.removeEventListener("keydown", root._mdsKeyHandler);
      root._mdsKeyHandler = null;
    }
    if (root._mdsPreviousFocus && root._mdsPreviousFocus.focus) {
      root._mdsPreviousFocus.focus();
    }
  }

  document.addEventListener("click", function (ev) {
    var openBtn = ev.target.closest("[data-mds-open-modal]");
    if (openBtn) {
      openModal(openBtn.getAttribute("data-mds-open-modal"));
      return;
    }
    var dismiss = ev.target.closest("[data-mds-modal-dismiss]");
    if (dismiss) {
      var root = dismiss.closest(".mds-modal-root");
      if (root && root.id) {
        closeModal(root.id.replace(/-root$/, ""));
      }
    }
  });
})();
