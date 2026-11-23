/* Student dashboard: full-screen map, live ETA polling, and Web Push setup. */

(function () {
  "use strict";

  const dataElement = document.getElementById("student-data");
  if (!dataElement) return;
  const data = JSON.parse(dataElement.textContent);

  let map = null;
  let busMarker = null;
  let firstFixCentered = false;

  function icon(kind, html, size) {
    return L.divIcon({
      className: "map-pin map-pin-" + kind,
      html: `<div class="map-pin-inner">${html}</div>`,
      iconSize: size || [32, 32],
      iconAnchor: [(size ? size[0] : 32) / 2, (size ? size[1] : 32) / 2],
    });
  }

  function initMap() {
    if (typeof L === "undefined") return;
    if (!data.assigned && !data.school) {
      // Nothing useful to show, but keep the background calm.
      map = L.map("student-map", { zoomControl: false }).setView([20.5937, 78.9629], 4);
    } else {
      map = L.map("student-map", { zoomControl: false }).setView([20.5937, 78.9629], 13);
    }

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);

    if (data.routeGeometry.length > 1) {
      const line = L.polyline(data.routeGeometry, {
        color: "#5b9dff", weight: 5, opacity: 0.8, lineJoin: "round",
      }).addTo(map);
      map.fitBounds(line.getBounds(), { padding: [25, 25] });
    }

    if (data.stop) {
      L.marker([data.stop.lat, data.stop.lon], {
        icon: icon("stop", "&#128205;"),
        title: data.stopName || "Your stop",
      }).addTo(map).bindTooltip(data.stopName || "Your stop", { direction: "top", offset: [0, -14] });
      if (data.routeGeometry.length < 2) map.setView([data.stop.lat, data.stop.lon], 15);
    }

    if (data.school) {
      L.marker([data.school.lat, data.school.lon], {
        icon: icon("school", "&#127979;"),
        interactive: false,
      }).addTo(map);
    }

    if (data.live) updateBusMarker(data.live, false);
  }

  function updateBusMarker(live, stale) {
    if (!map || !live || live.lat === null || live.lon === null) return;
    const heading = live.heading === null || live.heading === undefined ? 0 : live.heading;
    const busIcon = icon(
      "bus",
      `<span class="bus-arrow" style="transform:rotate(${heading}deg)">&#10148;</span>`,
      [44, 44]
    );
    if (!busMarker) {
      busMarker = L.marker([live.lat, live.lon], { icon: busIcon, title: "School bus" }).addTo(map);
      busMarker.bindTooltip("Your bus", { direction: "top", offset: [0, -18] });
      if (!data.routeGeometry.length && !firstFixCentered) {
        map.setView([live.lat, live.lon], 15);
        firstFixCentered = true;
      }
    } else {
      busMarker.setLatLng([live.lat, live.lon]);
      busMarker.setIcon(busIcon);
    }
    busMarker.setOpacity(stale ? 0.45 : 1);
  }

  function setText(id, text) {
    const element = document.getElementById(id);
    if (element) element.textContent = text;
  }

  function renderSnapshot(snapshot) {
    if (!snapshot.assigned) {
      setText("map-status-text", "No bus assigned");
      document.getElementById("map-status-dot").classList.add("offline");
      return;
    }

    const chip = document.getElementById("map-status-text");
    const dot = document.getElementById("map-status-dot");
    const busChip = document.getElementById("student-bus-chip");

    dot.classList.remove("offline", "warning");
    if (snapshot.stale) {
      dot.classList.add("offline");
      chip.textContent = "GPS signal lost - last known position";
    } else if (snapshot.status_code === "no_trip") {
      dot.classList.add("warning");
      chip.textContent = "Trip not active";
    } else if (snapshot.weak_gps) {
      dot.classList.add("warning");
      chip.textContent = "Weak GPS signal";
    } else {
      chip.textContent = "Live GPS";
    }

    if (snapshot.bus_name) {
      busChip.classList.remove("hidden");
      setText("student-bus-name", `${snapshot.bus_name} · ${snapshot.registration_number || ""}`);
    }

    if (snapshot.has_position) {
      updateBusMarker({ lat: snapshot.lat, lon: snapshot.lon, heading: snapshot.heading }, snapshot.stale);
    }

    let etaValue = "—";
    if (snapshot.eta_seconds !== null && snapshot.eta_seconds !== undefined) {
      etaValue = snapshot.eta_text;
    } else if (snapshot.status_code === "no_trip") {
      etaValue = "No trip";
    } else if (snapshot.status_code === "passed") {
      etaValue = "Passed";
    } else if (snapshot.status_code === "stopped") {
      etaValue = "Stopped";
    } else if (snapshot.status_code === "no_stop") {
      etaValue = "No stop";
    }

    setText("eta-value", etaValue);
    setText("status-text", snapshot.status_text || "Waiting for the bus");
    setText("status-detail", snapshot.status_detail || "");
    setText(
      "last-update-text",
      snapshot.last_update_at ? `Last GPS update: ${snapshot.last_update_text}` : "Last GPS update: —"
    );
    setText(
      "status-kicker",
      snapshot.trip_active
        ? (snapshot.trip_type === "morning" ? "Morning pickup trip" : "Afternoon drop-off trip")
        : "Live tracking"
    );
    if (snapshot.stop_name) setText("stop-name", snapshot.stop_name);
  }

  async function poll() {
    try {
      const snapshot = await window.BusApp.apiRequest("/student/api/live");
      renderSnapshot(snapshot);
    } catch (error) {
      setText("status-text", "Connection problem");
      setText("status-detail", "Could not reach the server. Retrying automatically...");
      document.getElementById("map-status-dot").classList.add("offline");
    }
  }

  // ------------------------------------------------------------------------
  // Web Push notifications
  // ------------------------------------------------------------------------

  const notificationDialog = document.getElementById("notification-dialog");
  const alertBox = document.getElementById("notification-alert");
  const distanceInput = document.getElementById("notify-distance");
  const distanceLabel = document.getElementById("distance-label");
  let serviceWorkerRegistration = null;
  let currentSubscription = null;

  function notifyAlert(message, kind) {
    alertBox.textContent = message;
    alertBox.className = "alert alert-" + (kind || "info");
    alertBox.classList.remove("hidden");
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = window.atob(base64);
    const output = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; i += 1) output[i] = rawData.charCodeAt(i);
    return output;
  }

  async function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return null;
    try {
      serviceWorkerRegistration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      return serviceWorkerRegistration;
    } catch (error) {
      notifyAlert("Service worker registration failed: " + error.message, "error");
      return null;
    }
  }

  async function refreshNotificationState() {
    if (!("Notification" in window) || !("PushManager" in window)) {
      notifyAlert("This browser does not support Web Push notifications.", "error");
      setText("permission-status", "Unsupported");
      setText("subscription-status", "Unsupported");
      return;
    }

    setText("permission-status", Notification.permission);

    const registration = serviceWorkerRegistration || await navigator.serviceWorker.getRegistration();
    if (registration) {
      const subscription = await registration.pushManager.getSubscription();
      currentSubscription = subscription;
      setText("subscription-status", subscription ? "Enabled" : "Not enabled");
    }

    try {
      const result = await window.BusApp.apiRequest("/student/api/notifications/status");
      if (result.distance_m) {
        distanceInput.value = result.distance_m;
        distanceLabel.textContent = result.distance_m + " m";
      }
    } catch (error) {
      // The dialog still works; don't block it on this request.
    }
  }

  document.getElementById("open-notifications").addEventListener("click", () => {
    alertBox.classList.add("hidden");
    notificationDialog.showModal();
    refreshNotificationState();
  });
  document.getElementById("open-notifications-2").addEventListener("click", () => {
    alertBox.classList.add("hidden");
    notificationDialog.showModal();
    refreshNotificationState();
  });

  distanceInput.addEventListener("input", () => {
    distanceLabel.textContent = distanceInput.value + " m";
  });

  document.getElementById("enable-notifications").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      if (!("Notification" in window) || !("PushManager" in window)) {
        throw new Error("This browser does not support Web Push.");
      }

      const permission = await Notification.requestPermission();
      setText("permission-status", permission);
      if (permission !== "granted") {
        throw new Error(
          "Notification permission was not granted. Allow notifications in Chrome's site settings."
        );
      }

      const registration = await registerServiceWorker();
      if (!registration) throw new Error("Service worker is not available.");

      const keyResponse = await window.BusApp.apiRequest("/student/api/notifications/public-key");
      let subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(keyResponse.public_key),
        });
      }
      currentSubscription = subscription;

      const result = await window.BusApp.apiRequest("/student/api/notifications/subscribe", {
        method: "POST",
        body: {
          subscription: subscription.toJSON(),
          distance_m: Number(distanceInput.value),
        },
      });
      notifyAlert("Notifications enabled. " + result.message, "success");
      setText("subscription-status", "Enabled");
      window.BusApp.playAlertTone();
    } catch (error) {
      notifyAlert(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById("save-distance").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await window.BusApp.apiRequest("/student/api/notification-distance", {
        method: "POST",
        body: { distance_m: Number(distanceInput.value) },
      });
      notifyAlert(result.message, "success");
    } catch (error) {
      notifyAlert(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById("disable-notifications").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const registration = serviceWorkerRegistration || await navigator.serviceWorker.getRegistration();
      const subscription = registration ? await registration.pushManager.getSubscription() : currentSubscription;
      if (subscription) {
        await window.BusApp.apiRequest("/student/api/notifications/unsubscribe", {
          method: "POST",
          body: { endpoint: subscription.endpoint },
        });
        await subscription.unsubscribe();
      }
      currentSubscription = null;
      setText("subscription-status", "Not enabled");
      notifyAlert("Notifications disabled on this device.", "success");
    } catch (error) {
      notifyAlert(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data && event.data.type === "bus-approach") {
        window.BusApp.playAlertTone();
      }
    });
  }

  initMap();
  poll();
  setInterval(poll, 5000);
})();
