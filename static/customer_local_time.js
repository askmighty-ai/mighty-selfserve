/**
 * Shared customer local-time initializer for Truth Dashboard surfaces.
 * Enhances <time class="mighty-customer-local-time"> with exact browser-local
 * date/time including timezone abbreviation when supported.
 * UTC remains in datetime + title attributes for inspection.
 */
(function () {
  "use strict";

  var CLASS_NAME = "mighty-customer-local-time";
  var READY_ATTR = "data-mighty-customer-local-ready";

  function parseTimestamp(value) {
    if (value == null || value === "") return null;
    var text = String(value).trim();
    if (!text || text === "—") return null;

    if (/^\d+(\.\d+)?$/.test(text)) {
      var num = Number(text);
      if (!isFinite(num)) return null;
      if (num > 1e12) return new Date(num);
      return new Date(num * 1000);
    }

    // "YYYY-MM-DD HH:MM:SS" / "YYYY-MM-DD HH:MM:SS UTC" → treat as UTC
    var spaceUtc = text.match(
      /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:\s*UTC)?$/
    );
    if (spaceUtc) {
      return new Date(spaceUtc[1] + "T" + spaceUtc[2] + "Z");
    }

    var d = new Date(text);
    if (isNaN(d.getTime())) return null;
    return d;
  }

  function formatExactLocal(d) {
    try {
      return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
        timeZoneName: "short",
      }).format(d);
    } catch (err) {
      try {
        return d.toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          year: "numeric",
          hour: "numeric",
          minute: "2-digit",
          hour12: true,
        });
      } catch (err2) {
        return d.toString();
      }
    }
  }

  function enhance(el) {
    if (!el || el.getAttribute(READY_ATTR) === "1") return;
    var raw = el.getAttribute("datetime") || el.textContent;
    var d = parseTimestamp(raw);
    if (!d) {
      el.setAttribute(READY_ATTR, "1");
      return;
    }

    var exact = formatExactLocal(d);
    el.textContent = exact;
    el.setAttribute(READY_ATTR, "1");
  }

  function initCustomerLocalTimes(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var nodes = scope.querySelectorAll
      ? scope.querySelectorAll("time." + CLASS_NAME)
      : [];
    for (var i = 0; i < nodes.length; i++) {
      enhance(nodes[i]);
    }
  }

  window.initCustomerLocalTimes = initCustomerLocalTimes;
  window.formatMightyCustomerLocalTime = formatExactLocal;
  window.parseMightyCustomerTimestamp = parseTimestamp;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initCustomerLocalTimes(document);
    });
  } else {
    initCustomerLocalTimes(document);
  }
})();
