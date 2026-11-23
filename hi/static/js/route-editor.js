/*
  Route editor.

  Workflow:
   1. "Add points" -> click along roads
   2. "Preview road route" -> asks the server (OSRM) for real road geometry
   3. "Save route" -> stores the route and redirects
   4. On a saved route: "Add stop" -> click near the route, name it.
*/

(function () {
  "use strict";

  const mapElement = document.getElementById("route-map");
  const dataElement = document.getElementById("route-editor-data");
  if (!mapElement || !dataElement || typeof L === "undefined") return;

  const data = JSON.parse(dataElement.textContent);
  const routeId = data.routeId;
  const school = data.school;

  let waypoints = data.waypoints.slice();
  let geometry = data.geometry.slice();
  let stops = data.stops.slice();
  let tool = "move";
  let pendingStopLatLng = null;

  const statusElement = document.getElementById("editor-status");
  const nameInput = document.getElementById("route-name");
  const waypointCount = document.getElementById("waypoint-count");
  const distanceLabel = document.getElementById("route-distance");
  const mapHint = document.getElementById("map-hint");
  const stopList = document.getElementById("stop-list");
  const stopEmpty = document.getElementById("stop-empty");
  const stopCount = document.getElementById("stop-count");
  const stopDialog = document.getElementById("stop-dialog");
  const stopForm = document.getElementById("stop-form");
  const stopNameInput = document.getElementById("stop-name");
  const stopDialogError = document.getElementById("stop-dialog-error");

  const map = L.map(mapElement, { zoomControl: true }).setView([20.5937, 78.9629], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  const routeLayer = L.layerGroup().addTo(map);
  const waypointLayer = L.layerGroup().addTo(map);
  const stopLayer = L.layerGroup().addTo(map);
  let routeLine = null;

  function icon(kind, html, size) {
    return L.divIcon({
      className: "map-pin map-pin-" + kind,
      html: `<div class="map-pin-inner">${html}</div>`,
      iconSize: size || [32, 32],
      iconAnchor: [(size ? size[0] : 32) / 2, (size ? size[1] : 32) / 2],
    });
  }

  function setStatus(message, kind) {
    statusElement.textContent = message;
    statusElement.classList.toggle("error", kind === "error");
    statusElement.classList.toggle("success", kind === "success");
  }

  function drawRoute() {
    if (routeLine) routeLayer.removeLayer(routeLine);
    routeLine = null;
    if (geometry.length > 1) {
      routeLine = L.polyline(geometry, {
        color: "#5b9dff", weight: 5, opacity: 0.85, lineJoin: "round",
      }).addTo(routeLayer);
      map.fitBounds(routeLine.getBounds(), { padding: [35, 35] });
    }
  }

  function drawWaypoints() {
    waypointLayer.clearLayers();
    waypoints.forEach((point, index) => {
      const marker = L.marker(point, {
        icon: icon("waypoint", String(index + 1), [20, 20]),
        draggable: true,
        title: `Route point ${index + 1}`,
      }).addTo(waypointLayer);
      marker.on("dragend", () => {
        const position = marker.getLatLng();
        waypoints[index] = [position.lat, position.lng];
        invalidatePreview();
      });
    });
    waypointCount.textContent = waypoints.length;
  }

  function invalidatePreview() {
    if (geometry.length) setStatus("Route points changed. Preview or save again to recalculate the road route.");
    geometry = [];
    if (routeLine) { routeLayer.removeLayer(routeLine); routeLine = null; }
    distanceLabel.textContent = "—";
  }

  function schoolMarker() {
    if (!school) return;
    L.marker([school.lat, school.lon], {
      icon: icon("school", "&#127979;"),
      title: "School",
      interactive: false,
    }).addTo(map);
  }

  function stopMarker(stop) {
    const marker = L.marker([stop.lat, stop.lon], {
      icon: icon("stop", "&#128205;"),
      draggable: true,
      title: stop.name,
    }).addTo(stopLayer);
    marker.bindTooltip(`${stop.name} (${(stop.distance_along_m / 1000).toFixed(2)} km)`, {
      direction: "top", offset: [0, -14],
    });
    marker.on("dragend", async () => {
      const position = marker.getLatLng();
      try {
        const result = await window.BusApp.apiRequest(`/admin/stops/${stop.id}/update`, {
          method: "POST",
          body: { name: stop.name, lat: position.lat, lon: position.lng },
        });
        Object.assign(stop, result.stop);
        marker.setLatLng([stop.lat, stop.lon]);
        stops.sort((a, b) => a.distance_along_m - b.distance_along_m);
        renderStopList();
        setStatus(`Moved "${stop.name}". It snapped to the nearest point on the route.`, "success");
      } catch (error) {
        marker.setLatLng([stop.lat, stop.lon]);
        setStatus(error.message, "error");
      }
    });
    marker._stopId = stop.id;
    return marker;
  }

  function drawStops() {
    stopLayer.clearLayers();
    stops.forEach((stop) => stopMarker(stop));
    stopCount.textContent = stops.length;
    stopEmpty.hidden = stops.length > 0;
  }

  function renderStopList() {
    stopList.innerHTML = "";
    stops.forEach((stop, index) => {
      const item = document.createElement("div");
      item.className = "stop-item";

      const head = document.createElement("div");
      head.className = "stop-item-head";
      const dot = document.createElement("span");
      dot.className = "stop-dot";
      const order = document.createElement("strong");
      order.textContent = "#" + (index + 1);
      const distance = document.createElement("span");
      distance.className = "muted small";
      distance.textContent = (stop.distance_along_m / 1000).toFixed(2) + " km along";
      head.append(dot, order, distance);

      const row = document.createElement("div");
      row.className = "stop-item-row";
      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 80;
      input.value = stop.name;
      input.setAttribute("aria-label", "Stop name");

      const renameButton = document.createElement("button");
      renameButton.type = "button";
      renameButton.className = "btn btn-ghost btn-sm";
      renameButton.textContent = "Rename";
      renameButton.addEventListener("click", async () => {
        const newName = input.value.trim();
        if (!newName) { setStatus("Enter a stop name.", "error"); return; }
        try {
          const result = await window.BusApp.apiRequest(`/admin/stops/${stop.id}/update`, {
            method: "POST",
            body: { name: newName },
          });
          Object.assign(stop, result.stop);
          drawStops();
          renderStopList();
          setStatus("Stop renamed.", "success");
        } catch (error) {
          setStatus(error.message, "error");
        }
      });

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "btn btn-danger btn-sm";
      deleteButton.textContent = "Delete";
      deleteButton.addEventListener("click", async () => {
        if (!window.confirm(`Delete the stop "${stop.name}"? Students assigned to it will lose their pickup stop.`)) return;
        try {
          await window.BusApp.apiRequest(`/admin/stops/${stop.id}/delete`, { method: "POST" });
          stops = stops.filter((entry) => entry.id !== stop.id);
          drawStops();
          renderStopList();
          setStatus("Stop deleted.", "success");
        } catch (error) {
          setStatus(error.message, "error");
        }
      });

      row.append(input, renameButton, deleteButton);
      item.append(head, row);
      stopList.append(item);
    });
    stopCount.textContent = stops.length;
    stopEmpty.hidden = stops.length > 0;
  }

  function setTool(nextTool) {
    if (nextTool === "undo") {
      if (!waypoints.length) return;
      waypoints.pop();
      invalidatePreview();
      drawWaypoints();
      setStatus("Removed the last route point.");
      return;
    }
    if (nextTool === "add-stop" && !routeId) {
      setStatus("Save the route before adding stops.", "error");
      return;
    }
    tool = nextTool;
    document.querySelectorAll("[data-tool]").forEach((button) => {
      if (button.dataset.tool !== "undo") {
        button.classList.toggle("active", button.dataset.tool === tool);
      }
    });
    const hints = {
      move: "Drag route point markers to adjust them. Drag a stop marker to move that stop.",
      "add-point": "Click the map along the road to add route points.",
      "add-stop": "Click the map near the route line to place a stop.",
    };
    mapHint.textContent = hints[tool];
    mapElement.style.cursor = tool === "move" ? "" : "crosshair";
  }

  document.querySelectorAll("[data-tool]").forEach((button) => {
    button.addEventListener("click", () => setTool(button.dataset.tool));
  });

  map.on("click", (event) => {
    if (tool === "add-point") {
      waypoints.push([event.latlng.lat, event.latlng.lng]);
      invalidatePreview();
      drawWaypoints();
      setStatus("Point added. Add more, or preview the road route.");
    } else if (tool === "add-stop" && routeId) {
      pendingStopLatLng = event.latlng;
      stopNameInput.value = "";
      stopDialogError.classList.add("hidden");
      stopDialog.showModal();
      stopNameInput.focus();
    }
  });

  document.getElementById("preview-route").addEventListener("click", async () => {
    if (waypoints.length < 2) {
      setStatus("Add at least two route points first.", "error");
      return;
    }
    const button = document.getElementById("preview-route");
    button.disabled = true;
    setStatus("Asking OSRM for the road route...");
    try {
      const result = await window.BusApp.apiRequest("/admin/api/routes/preview", {
        method: "POST",
        body: { name: nameInput.value.trim(), waypoints },
      });
      geometry = result.geometry;
      if (result.auto_reversed) {
        waypoints = result.waypoints;
        drawWaypoints();
      }
      drawRoute();
      distanceLabel.textContent = (result.distance_m / 1000).toFixed(2) + " km";
      setStatus(
        result.auto_reversed
          ? "Road route ready. It was reversed so that it ends near the school."
          : "Road route ready. Check the blue line, then save.",
        "success"
      );
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById("save-route").addEventListener("click", async () => {
    if (!nameInput.value.trim()) {
      setStatus("Enter a route name.", "error");
      nameInput.focus();
      return;
    }
    if (waypoints.length < 2) {
      setStatus("Add at least two route points.", "error");
      return;
    }
    const button = document.getElementById("save-route");
    button.disabled = true;
    button.textContent = "Saving...";
    setStatus("Calculating the road route and saving...");
    try {
      const url = routeId ? `/admin/routes/${routeId}/update` : "/admin/routes/create";
      const result = await window.BusApp.apiRequest(url, {
        method: "POST",
        body: { name: nameInput.value.trim(), waypoints },
      });
      window.location.href = result.redirect;
    } catch (error) {
      setStatus(error.message, "error");
      button.disabled = false;
      button.textContent = routeId ? "Save route changes" : "Save route";
    }
  });

  stopForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!pendingStopLatLng || !routeId) return;
    const name = stopNameInput.value.trim();
    if (!name) return;
    try {
      const result = await window.BusApp.apiRequest(`/admin/routes/${routeId}/stops/add`, {
        method: "POST",
        body: { name, lat: pendingStopLatLng.lat, lon: pendingStopLatLng.lng },
      });
      stops.push(result.stop);
      stops.sort((a, b) => a.distance_along_m - b.distance_along_m);
      drawStops();
      renderStopList();
      stopDialog.close();
      setStatus(`Stop "${result.stop.name}" added and snapped to the route.`, "success");
    } catch (error) {
      stopDialogError.textContent = error.message;
      stopDialogError.classList.remove("hidden");
    }
  });

  // Initial drawing.
  drawRoute();
  drawWaypoints();
  drawStops();
  renderStopList();
  schoolMarker();
  if (!geometry.length) {
    if (school) map.setView([school.lat, school.lon], 14);
    else if (waypoints.length) map.fitBounds(waypoints, { padding: [35, 35] });
  }
})();
