/* Emerald Rozalia - Project 1 : shared browser behaviour.
 *
 * This file used to be a single 265-byte listener that console.logged the label
 * of any View/Preview/Edit/Remove/Download button and did nothing else - with a
 * dead condition that put 'Print' in its list and then excluded it. Every such
 * button on the platform therefore did nothing at all.
 *
 * Everything else was written as inline onclick attributes: 31 print buttons,
 * 3 modal openers, 4 modal closers, an auto-submitting filter, and the sewing
 * and careers dialogs. nginx serves a Content-Security-Policy of
 * `script-src 'self'` with no 'unsafe-inline', so ALL of them were inert in
 * production while working under runserver, where there is no CSP. The failure
 * was silent apart from a console error.
 *
 * Behaviour is therefore delegated from one listener on document, keyed by a
 * data-action attribute, which a CSP allows. See SITE_AUDIT_FINDINGS.md A5, B22.
 */
(function () {
  "use strict";

  /* ---------------------------------------------------------------- helpers */

  function closestAction(target) {
    var el = target.closest("[data-action]");
    return el ? { el: el, action: el.getAttribute("data-action") } : null;
  }

  function dialogById(id) {
    var el = id && document.getElementById(id);
    return el && typeof el.showModal === "function" ? el : null;
  }

  /* Fill a dialog's fields from data-* attributes on the button that opened it.
   * Replaces the openAction(...) and chooseVacancy(...) inline functions, whose
   * arguments were interpolated straight into the HTML. */
  function applyFields(dialog, source) {
    Object.keys(source.dataset).forEach(function (key) {
      if (key === "action" || key === "target") return;
      var field = dialog.querySelector('[data-field="' + key + '"]');
      if (!field) return;
      if ("value" in field) field.value = source.dataset[key];
      else field.textContent = source.dataset[key];
    });
  }

  /* ------------------------------------------------------------ interactions */

  document.addEventListener("click", function (event) {
    var hit = closestAction(event.target);
    if (!hit) return;

    switch (hit.action) {
      /* 31 buttons carried onclick="window.print()". */
      case "print":
        event.preventDefault();
        window.print();
        return;

      case "open-dialog": {
        var dialog = dialogById(hit.el.getAttribute("data-target"));
        if (!dialog) return;
        event.preventDefault();
        applyFields(dialog, hit.el);
        dialog.showModal();
        return;
      }

      /* Without this an opened dialog could only be dismissed with Escape. */
      case "close-dialog": {
        event.preventDefault();
        var open = hit.el.closest("dialog");
        if (open) open.close();
        return;
      }

      case "toggle-nav": {
        event.preventDefault();
        var nav = document.getElementById(
          hit.el.getAttribute("aria-controls") || "primary-nav",
        );
        if (!nav) return;
        var open2 = nav.classList.toggle("is-open");
        hit.el.setAttribute("aria-expanded", open2 ? "true" : "false");
        return;
      }

      /* Some "open" targets are a page section, not a <dialog>: careers.html
       * fills the application form and scrolls down to it. */
      case "fill-and-scroll": {
        var section = document.getElementById(
          hit.el.getAttribute("data-target"),
        );
        if (!section) return;
        event.preventDefault();
        applyFields(section, hit.el);
        section.scrollIntoView({ behavior: "smooth", block: "start" });
        var first = section.querySelector("input, select, textarea");
        if (first) first.focus({ preventScroll: true });
        return;
      }

      case "submit": {
        event.preventDefault();
        var form = hit.el.closest("form");
        if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
        return;
      }
      default:
        return;
    }
  });

  /* A <select> that submits its form on change. */
  document.addEventListener("change", function (event) {
    var el = event.target.closest('[data-action="submit-on-change"]');
    if (!el) return;
    var form = el.closest("form");
    if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
  });

  /* Guard against a double-submit creating two production entries or two stock
   * movements. No form had any protection. */
  document.addEventListener(
    "submit",
    function (event) {
      var form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.hasAttribute("data-allow-resubmit")) return;
      if (form.dataset.submitting === "1") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "1";
      var buttons = form.querySelectorAll(
        'button[type="submit"], input[type="submit"]',
      );
      Array.prototype.forEach.call(buttons, function (b) {
        b.disabled = true;
        if (!b.dataset.label && b.tagName === "BUTTON") {
          b.dataset.label = b.textContent;
          b.textContent = "Working…";
        }
      });
      /* If the browser restores the page from cache instead of navigating, the
       * form must not stay locked. */
      window.setTimeout(function () {
        form.dataset.submitting = "";
        Array.prototype.forEach.call(buttons, function (b) {
          b.disabled = false;
          if (b.dataset.label) b.textContent = b.dataset.label;
        });
      }, 15000);
    },
    true,
  );

  /* ------------------------------------------------------- polling refresh */

  /* Pages declare <body data-refresh-url="..." data-refresh-seconds="30">.
   * The command centre did this with an inline setInterval that reloaded the
   * whole page and had no error handling, so a failing endpoint reloaded
   * forever. This backs off instead of hammering a struggling server. */
  function startRefresh() {
    var body = document.body;
    var url = body && body.getAttribute("data-refresh-url");
    if (!url) return;
    var base = parseInt(body.getAttribute("data-refresh-seconds") || "30", 10);
    if (!(base > 0)) return;
    var failures = 0;

    function tick() {
      fetch(url, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          failures = 0;
          paint(data);
          setStale(false);
        })
        .catch(function () {
          failures += 1;
          /* Say the figures are stale rather than presenting old numbers as
           * current. */
          setStale(true);
        })
        .then(function () {
          var wait = base * Math.min(Math.pow(2, failures), 16);
          window.setTimeout(tick, wait * 1000);
        });
    }
    window.setTimeout(tick, base * 1000);
  }

  /* Write values into [data-live="key"] elements. Text only - never innerHTML,
   * so a buyer or style name containing markup cannot become markup. */
  function paint(data) {
    if (!data || typeof data !== "object") return;
    Object.keys(data).forEach(function (key) {
      var nodes = document.querySelectorAll('[data-live="' + key + '"]');
      if (!nodes.length) return;
      var value = data[key];
      if (value === null || value === undefined) value = "—";
      if (typeof value === "object") return;
      Array.prototype.forEach.call(nodes, function (n) {
        n.textContent = String(value);
      });
    });
  }

  function setStale(isStale) {
    var flag = document.querySelector("[data-refresh-status]");
    if (!flag) return;
    flag.textContent = isStale
      ? "Live figures unavailable - showing the last values received"
      : "";
    flag.hidden = !isStale;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startRefresh);
  } else {
    startRefresh();
  }
})();
