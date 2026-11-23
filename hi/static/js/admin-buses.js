/* Buses page: generate a one-time ESP32 pairing code and show the countdown. */

(function () {
  "use strict";

  const dialog = document.getElementById("pair-dialog");
  if (!dialog) return;

  const busName = document.getElementById("pair-bus-name");
  const codeElement = document.getElementById("pair-code");
  const commandCode = document.getElementById("pair-command-code");
  const countdown = document.getElementById("pair-countdown");
  const errorBox = document.getElementById("pair-error");
  const content = document.getElementById("pair-content");
  let timer = null;

  function stopTimer() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  function startCountdown(seconds) {
    stopTimer();
    let remaining = seconds;
    const render = () => {
      const minutes = Math.floor(remaining / 60);
      const secs = String(remaining % 60).padStart(2, "0");
      countdown.textContent = `${minutes}:${secs}`;
      if (remaining <= 0) {
        countdown.textContent = "expired";
        codeElement.textContent = "EXPIRED";
        commandCode.textContent = "generate a new code";
        stopTimer();
      }
      remaining -= 1;
    };
    render();
    timer = setInterval(render, 1000);
  }

  document.querySelectorAll("[data-pair-bus]").forEach((button) => {
    button.addEventListener("click", async () => {
      stopTimer();
      busName.textContent = button.dataset.pairName || "";
      codeElement.textContent = "Generating...";
      commandCode.textContent = "";
      countdown.textContent = "5:00";
      errorBox.classList.add("hidden");
      content.classList.remove("hidden");
      dialog.showModal();

      try {
        const data = await window.BusApp.apiRequest(
          `/admin/buses/${button.dataset.pairBus}/pairing-code`,
          { method: "POST" }
        );
        codeElement.textContent = data.code;
        commandCode.textContent = data.code;
        startCountdown(data.expires_in_seconds || 300);
      } catch (error) {
        content.classList.add("hidden");
        errorBox.textContent = error.message;
        errorBox.classList.remove("hidden");
      }
    });
  });

  dialog.addEventListener("close", stopTimer);
})();
