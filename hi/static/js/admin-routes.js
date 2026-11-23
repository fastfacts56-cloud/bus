/* Routes page: choose the one school location used by every route. */

(function () {
  "use strict";

  const mapElement = document.getElementById("school-map");
  const dataElement = document.getElementById("school-map-data");
  if (!mapElement || !dataElement || typeof L === "undefined") return;

  const data = JSON.parse(dataElement.textContent);
  let school = data.school;
  let picking = false;
  let pending = null;
  let schoolMarker = null;
  let pendingMarker = null;

  const map = L.map(mapElement, { zoomControl: true }).setView([20.5937, 78.9629], 5);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  function markerIcon(kind, symbol) {
    return L.divIcon({
      className: "map-pin map-pin-" + kind,
      html: `<div class="map-pin-inner">${symbol}</div>`,
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    });
  }

  function drawSchool() {
    if (schoolMarker) map.removeLayer(schoolMarker);
    if (school) {
      schoolMarker = L.marker([school.lat, school.lon], {
        icon: markerIcon("school", "&#127979;"),
        title: "School location",
      }).addTo(map);
      map.setView([school.lat, school.lon], 14);
    }
  }

  drawSchool();

  const setButton = document.getElementById("set-school-btn");
  const saveActions = document.getElementById("school-save-actions");
  const coordinates = document.getElementById("school-coordinates");
  const saveButton = document.getElementById("school-save");
  const cancelButton = document.getElementById("school-cancel");

  setButton.addEventListener("click", () => {
    picking = true;
    mapElement.style.cursor = "crosshair";
    setButton.classList.add("hidden");
    saveActions.classList.remove("hidden");
    coordinates.textContent = "Click the map at the school entrance.";
  });

  map.on("click", (event) => {
    if (!picking) return;
    pending = event.latlng;
    if (pendingMarker) map.removeLayer(pendingMarker);
    pendingMarker = L.marker(event.latlng, {
      icon: markerIcon("school", "&#127979;"),
    }).addTo(map);
    coordinates.textContent = `Selected: ${event.latlng.lat.toFixed(5)}, ${event.latlng.lng.toFixed(5)}`;
  });

  cancelButton.addEventListener("click", () => {
    picking = false;
    mapElement.style.cursor = "";
    saveActions.classList.add("hidden");
    setButton.classList.remove("hidden");
    if (pendingMarker) {
      map.removeLayer(pendingMarker);
      pendingMarker = null;
    }
    pending = null;
    coordinates.textContent = school
      ? `Current: ${school.lat.toFixed(5)}, ${school.lon.toFixed(5)}`
      : "No school location has been set yet.";
  });

  saveButton.addEventListener("click", async () => {
    if (!pending) {
      coordinates.textContent = "Click on the map first.";
      return;
    }
    saveButton.disabled = true;
    try {
      const result = await window.BusApp.apiRequest("/admin/api/school-location", {
        method: "POST",
        body: { lat: pending.lat, lon: pending.lng },
      });
      school = { lat: result.lat, lon: result.lon };
      picking = false;
      pending = null;
      mapElement.style.cursor = "";
      saveActions.classList.add("hidden");
      setButton.classList.remove("hidden");
      setButton.textContent = "Change location";
      if (pendingMarker) { map.removeLayer(pendingMarker); pendingMarker = null; }
      drawSchool();
      coordinates.textContent = `Current: ${school.lat.toFixed(5)}, ${school.lon.toFixed(5)}`;
    } catch (error) {
      coordinates.textContent = error.message;
    } finally {
      saveButton.disabled = false;
    }
  });
})();
