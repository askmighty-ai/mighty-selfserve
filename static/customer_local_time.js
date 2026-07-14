/**
 * Shared customer local-time initializer for Truth Dashboard surfaces.
 * Enhances <time class="mighty-customer-local-time"> with exact browser-local
 * date/time including timezone abbreviation when supported.
 * UTC remains in datetime + title attributes for inspection.
 *
 * Also updates <span class="mighty-customer-elapsed"> live without rewriting
 * the underlying started_at timestamp.
 */
(function () {
  "use strict";

  var CLASS_NAME = "mighty-customer-local-time";
  var ELAPSED_CLASS = "mighty-customer-elapsed";
  var READY_ATTR = "data-mighty-customer-local-ready";
  var elapsedTimer = null;

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

  function formatElapsed(seconds) {
    if (seconds < 60) {
      return seconds + (seconds === 1 ? " second" : " seconds");
    }
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
      return minutes + (minutes === 1 ? " minute" : " minutes");
    }
    var hours = Math.floor(minutes / 60);
    return hours + (hours === 1 ? " hour" : " hours");
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

  function updateElapsed(el) {
    if (!el) return;
    var raw = el.getAttribute("data-started-at");
    var d = parseTimestamp(raw);
    if (!d) return;
    var prefix = el.getAttribute("data-elapsed-prefix") || "Checking for";
    var seconds = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
    el.textContent = prefix + " " + formatElapsed(seconds);
  }

  function initCustomerLocalTimes(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var nodes = scope.querySelectorAll
      ? scope.querySelectorAll("time." + CLASS_NAME)
      : [];
    for (var i = 0; i < nodes.length; i++) {
      enhance(nodes[i]);
    }
    var elapsed = scope.querySelectorAll
      ? scope.querySelectorAll("." + ELAPSED_CLASS)
      : [];
    for (var j = 0; j < elapsed.length; j++) {
      updateElapsed(elapsed[j]);
    }
    if (elapsed.length && !elapsedTimer) {
      elapsedTimer = setInterval(function () {
        var live = document.querySelectorAll("." + ELAPSED_CLASS);
        for (var k = 0; k < live.length; k++) {
          updateElapsed(live[k]);
        }
      }, 1000);
    }
  }

  window.initCustomerLocalTimes = initCustomerLocalTimes;
  window.formatMightyCustomerLocalTime = formatExactLocal;
  window.parseMightyCustomerTimestamp = parseTimestamp;
  window.updateMightyCustomerElapsed = updateElapsed;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initCustomerLocalTimes(document);
    });
  } else {
    initCustomerLocalTimes(document);
  }
})();
