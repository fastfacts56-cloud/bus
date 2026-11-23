/* Driver dashboard: map, live status, start/end trip controls. */

(function () {
  "use strict";

  const dataElement = document.getElementById("driver-data");
  const mapElement = document.getElementById("driver-map");
  if (!dataElement || !mapElement || typeof L === "undefined") return;

  const data = JSON.parse(dataElement.textContent);
  const dialog = document.getElementById("trip-dialog");
  const endDialog = document.getElementById("end-dialog");
  const alertBox = document.getElementById("driver-alert");

  const map = L.map(mapElement, { zoomControl: true }).setView([20.5937, 78.9629], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  let busMarker = null;
  let firstView = true;

  function icon(kind, html, size) {
    return L.divIcon({
      className: "map-pin map-pin-" + kind,
      html: `<div class="map-pin-inner">${html}</div>`,
      iconSize: size || [36, 36],
      iconAnchor: [(size ? size[0] : 36) / 2, (size ? size[1] : 36) / 2],
    });
  }

  if (data.routeGeometry.length > 1) {
    const line = L.polyline(data.routeGeometry, { color: "#5b9dff", weight: 5, opacity: 0.75 }).addTo(map);
    map.fitBounds(line.getBounds(), { padding: [30, 30] });
    firstView = false;
  }

  if (data.school) {
    L.marker([data.school.lat, data.school.lon], {
      icon: icon("school", "&#127979;"),
      interactive: false,
    }).addTo(map);
    if (firstView) {
      map.setView([data.school.lat, data.school.lon], 14);
      firstView = false;
    }
  }

  function showError(message) {
    alertBox.textContent = message;
    alertBox.classList.remove("hidden");
  }

  function hideError() {
    alertBox.classList.add("hidden");
  }

  function updateBusMarker(live) {
    if (!live || live.lat === null || live.lon === null) return;
    const heading = live.heading === null || live.heading === undefined ? 0 : live.heading;
    const busIcon = icon("bus", `<span class="bus-arrow" style="transform:rotate(${heading}deg)">&#10148;</span>`, [42, 42]);
    if (!busMarker) {
      busMarker = L.marker([live.lat, live.lon], { icon: busIcon, title: "Your bus" }).addTo(map);
      map.setView([live.lat, live.lon], 15);
    } else {
      busMarker.setLatLng([live.lat, live.lon]);
      busMarker.setIcon(busIcon);
    }
  }

  function updateStatus(status) {
    if (!status.has_bus) return;
    const gps = status.gps;
    const device = status.device;
    const trip = status.trip;

    const chip = document.getElementById("driver-gps-chip");
    const dot = document.getElementById("driver-gps-dot");
    const chipLabel = document.getElementById("driver-gps-label");

    if (!device.paired) {
      chipLabel.textContent = "GPS device not paired";
      dot.classList.add("offline");
      setText("driver-device-status", "Not paired");
    } else if (!device.online) {
      chipLabel.textContent = "Device offline";
      dot.classList.add("offline");
      setText("driver-device-status", "Offline");
    } else if (gps.stale) {
      chipLabel.textContent = "GPS signal lost";
      dot.classList.add("warning");
      setText("driver-device-status", "Online - GPS outdated");
    } else {
      chipLabel.textContent = "GPS live";
      dot.classList.remove("offline", "warning");
      setText("driver-device-status", "Online");
    }

    if (gps.has_position) {
      updateBusMarker(gps);
      setText("driver-gps-time", gps.last_update_text);
      setText("driver-speed", gps.speed_kmh + " km/h");
      setText("driver-accuracy", gps.accuracy_m ? gps.accuracy_m + " m" : "—");
    } else {
      setText("driver-gps-time", "No position yet");
      setText("driver-speed", "—");
      setText("driver-accuracy", "—");
    }

    const activePanel = document.getElementById("trip-active-panel");
    const idlePanel = document.getElementById("trip-idle-panel");
    if (trip.active) {
      activePanel.classList.remove("hidden");
      idlePanel.classList.add("hidden");
      setText("trip-type-badge", trip.trip_type === "morning" ? "Morning pickup" : "Afternoon drop-off");
      setText("trip-type-title", trip.trip_type === "morning" ? "Morning Pickup running" : "Afternoon Drop-off running");
      setText("trip-started-at", "Started " + (trip.started_at_text || ""));
    } else {
      activePanel.classList.add("hidden");
      idlePanel.classList.remove("hidden");
    }
  }

  function setText(id, text) {
    const element = document.getElementById(id);
    if (element) element.textContent = text;
  }

  async function poll() {
    try {
      const status = await window.BusApp.apiRequest("/driver/api/status");
      updateStatus(status);
      hideError();
    } catch (error) {
      showError("Could not refresh status: " + error.message);
    }
  }

  document.getElementById("start-trip-btn").addEventListener("click", () => {
    dialog.showModal();
  });

  document.querySelectorAll("[data-trip-type]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await window.BusApp.apiRequest("/driver/api/trips/start", {
          method: "POST",
          body: { trip_type: button.dataset.tripType },
        });
        dialog.close();
        hideError();
        await poll();
      } catch (error) {
        dialog.close();
        showError(error.message);
      } finally {
        button.disabled = false;
      }
    });
  });

  document.getElementById("end-trip-btn").addEventListener("click", () => {
    endDialog.showModal();
  });

  document.getElementById("confirm-end-trip").addEventListener("click", async () => {
    const button = document.getElementById("confirm-end-trip");
    button.disabled = true;
    try {
      await window.BusApp.apiRequest("/driver/api/trips/end", { method: "POST" });
      endDialog.close();
      hideError();
      await poll();
    } catch (error) {
      endDialog.close();
      showError(error.message);
    } finally {
      button.disabled = false;
    }
  });

  poll();
  setInterval(poll, 5000);
})();
