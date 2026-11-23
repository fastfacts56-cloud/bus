/* Student edit page: only show stops that belong to the selected bus's route. */

(function () {
  "use strict";

  const busSelect = document.querySelector("[data-student-bus]");
  const stopSelect = document.querySelector("[data-student-stop]");
  if (!busSelect || !stopSelect) return;

  function filterStops(clearIfInvalid) {
    const selectedBus = busSelect.selectedOptions[0];
    const routeId = selectedBus ? selectedBus.dataset.routeId : "";
    let selectedStillValid = false;

    Array.from(stopSelect.options).forEach((option) => {
      if (!option.value) return; // keep the "No stop" option
      const matches = Boolean(routeId) && option.dataset.routeId === routeId;
      option.hidden = !matches;
      option.disabled = !matches;
      if (matches && option.value === stopSelect.value) selectedStillValid = true;
    });

    if (clearIfInvalid && !selectedStillValid) {
      stopSelect.value = "";
    }
  }

  busSelect.addEventListener("change", () => filterStops(true));
  filterStops(false);
})();
