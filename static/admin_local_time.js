/**
 * Shared admin local-time initializer.
 * Enhances <time class="mighty-local-time"> elements with:
 *   - relative time (primary)
 *   - exact local time (secondary, muted)
 * UTC remains in datetime + title attributes.
 */
(function () {
  "use strict";

  var CLASS_NAME = "mighty-local-time";
  var READY_ATTR = "data-mighty-local-ready";

  function parseTimestamp(value) {
    if (value == null || value === "") return null;
    var text = String(value).trim();
    if (!text || text === "—") return null;

    // Epoch seconds / millis
    if (/^\d+(\.\d+)?$/.test(text)) {
      var num = Number(text);
      if (!isFinite(num)) return null;
      // Heuristic: treat large values as millis
      if (num > 1e12) return new Date(num);
      return new Date(num * 1000);
    }

    // "YYYY-MM-DD HH:MM:SS UTC" / space-separated naive → treat as UTC
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

  function plural(n, unit) {
    return n + " " + unit + (n === 1 ? "" : "s");
  }

  function startOfLocalDay(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  function formatExactLocal(d) {
    try {
      return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
        timeZoneName: "short",
      }).format(d);
    } catch (err) {
      return d.toString();
    }
  }

  function formatYesterdayLocal(d) {
    try {
      var t = new Intl.DateTimeFormat(undefined, {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      }).format(d);
      return "Yesterday at " + t;
    } catch (err) {
      return "Yesterday";
    }
  }

  function formatRelative(d, now) {
    var diffMs = d.getTime() - now.getTime();
    var absMs = Math.abs(diffMs);
    var future = diffMs > 0;
    var sec = Math.round(absMs / 1000);
    var min = Math.round(absMs / 60000);
    var hr = Math.round(absMs / 3600000);
    var day = Math.round(absMs / 86400000);

    if (sec < 10) {
      return future ? "In a moment" : "Just now";
    }
    if (sec < 60) {
      return future ? "In " + plural(sec, "second") : plural(sec, "second") + " ago";
    }
    if (min < 60) {
      return future ? "In " + plural(min, "minute") : plural(min, "minute") + " ago";
    }
    if (hr < 24) {
      return future ? "In " + plural(hr, "hour") : plural(hr, "hour") + " ago";
    }

    // Calendar-day "Yesterday" only for past timestamps
    if (!future) {
      var todayStart = startOfLocalDay(now);
      var thatStart = startOfLocalDay(d);
      var dayDiff = Math.round((todayStart - thatStart) / 86400000);
      if (dayDiff === 1) {
        return formatYesterdayLocal(d);
      }
      if (dayDiff > 1 && dayDiff < 7) {
        return plural(dayDiff, "day") + " ago";
      }
    } else if (day < 7) {
      return "In " + plural(day, "day");
    }

    return formatExactLocal(d);
  }

  function enhance(el) {
    if (!el || el.getAttribute(READY_ATTR) === "1") return;
    var raw = el.getAttribute("datetime") || el.textContent;
    var d = parseTimestamp(raw);
    if (!d) {
      // Leave original visible text; still mark so we don't loop.
      el.setAttribute(READY_ATTR, "1");
      return;
    }

    var now = new Date();
    var relative = formatRelative(d, now);
    var exact = formatExactLocal(d);

    // Preserve UTC metadata; replace visible content only.
    el.innerHTML =
      '<span class="mighty-rel">' +
      relative +
      '</span><span class="mighty-exact">' +
      exact +
      "</span>";
    el.setAttribute(READY_ATTR, "1");
  }

  function initAdminLocalTimes(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var nodes = scope.querySelectorAll
      ? scope.querySelectorAll("time." + CLASS_NAME)
      : [];
    for (var i = 0; i < nodes.length; i++) {
      enhance(nodes[i]);
    }
  }

  // Expose for dynamically inserted admin HTML (e.g. probe polling).
  window.initAdminLocalTimes = initAdminLocalTimes;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initAdminLocalTimes(document);
    });
  } else {
    initAdminLocalTimes(document);
  }
})();
