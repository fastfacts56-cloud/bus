/*
  Shared browser helpers used by every page.
  No frameworks, just plain JavaScript.
*/

(function () {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  /** Fetch wrapper that adds the CSRF token and returns parsed JSON. */
  async function apiRequest(url, options = {}) {
    const headers = Object.assign(
      { "X-CSRF-Token": csrfToken, "Accept": "application/json" },
      options.headers || {}
    );
    if (options.body && typeof options.body !== "string") {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.body);
    }
    const response = await fetch(url, Object.assign({}, options, { headers }));
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = { ok: false, error: "The server returned an unexpected response." };
    }
    if (!response.ok || data.ok === false) {
      const message = data.error || data.message || `Request failed (HTTP ${response.status}).`;
      throw new Error(message);
    }
    return data;
  }

  /** Make an alert tone while the page is open (best effort, no autoplay promise). */
  function playAlertTone() {
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      const context = new AudioContextClass();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.25, context.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.9);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + 0.95);
      oscillator.onended = () => context.close();
    } catch (error) {
      // Browsers may block audio until the user interacts. That is expected.
    }
  }

  function formatDistance(meters) {
    if (meters === null || meters === undefined) return "—";
    if (meters >= 1000) return (meters / 1000).toFixed(1) + " km";
    return Math.round(meters) + " m";
  }

  function formatAge(seconds) {
    if (seconds === null || seconds === undefined) return "never";
    if (seconds < 10) return "just now";
    if (seconds < 60) return Math.round(seconds) + " sec ago";
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + " min ago";
    return Math.floor(minutes / 60) + " hr ago";
  }

  // Expose a tiny namespace for page scripts.
  window.BusApp = { apiRequest, playAlertTone, formatDistance, formatAge, csrfToken };

  document.addEventListener("click", function (event) {
    // Dismiss flash messages.
    const dismiss = event.target.closest("[data-dismiss='flash']");
    if (dismiss) {
      dismiss.closest(".flash")?.remove();
      return;
    }

    // Copy buttons.
    const copyButton = event.target.closest("[data-copy-target]");
    if (copyButton) {
      const target = document.getElementById(copyButton.dataset.copyTarget);
      if (target) {
        const text = target.textContent.trim();
        const done = () => {
          const original = copyButton.textContent;
          copyButton.textContent = "Copied";
          setTimeout(() => { copyButton.textContent = original; }, 1500);
        };
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
        } else {
          fallbackCopy(text, done);
        }
      }
      return;
    }

    // Password show / hide.
    const passwordToggle = event.target.closest("[data-toggle-password]");
    if (passwordToggle) {
      const input = passwordToggle.parentElement.querySelector("input");
      if (input) {
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        passwordToggle.textContent = show ? "Hide" : "Show";
      }
      return;
    }

    // Close dialogs.
    const closeButton = event.target.closest("[data-close-dialog]");
    if (closeButton) {
      closeButton.closest("dialog")?.close();
      return;
    }

    // Mobile sidebar.
    if (event.target.closest("[data-toggle-sidebar]")) {
      document.body.classList.toggle("sidebar-open");
      return;
    }
    if (event.target.closest("[data-sidebar-backdrop]")) {
      document.body.classList.remove("sidebar-open");
    }
  });

  // Clicking the dark area around a dialog closes it.
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      const box = dialog.getBoundingClientRect();
      const outside = event.clientX < box.left || event.clientX > box.right ||
                      event.clientY < box.top || event.clientY > box.bottom;
      if (outside) dialog.close();
    });
  });

  // Destructive actions always ask for confirmation.
  document.addEventListener("submit", function (event) {
    const form = event.target;
    const message = form.dataset.confirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  // Simple client-side table search.
  document.querySelectorAll("[data-table-filter]").forEach((input) => {
    input.addEventListener("input", () => {
      const table = document.getElementById(input.dataset.tableFilter);
      if (!table) return;
      const needle = input.value.trim().toLowerCase();
      table.querySelectorAll("tbody tr").forEach((row) => {
        row.style.display = row.textContent.toLowerCase().includes(needle) ? "" : "none";
      });
    });
  });

  // Small local clock in the admin top bar.
  const clock = document.getElementById("server-clock");
  if (clock) {
    const tick = () => {
      clock.textContent = new Date().toLocaleTimeString();
    };
    tick();
    setInterval(tick, 30000);
  }

  function fallbackCopy(text, done) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); done(); } catch (error) { /* ignore */ }
    document.body.removeChild(area);
  }
})();
