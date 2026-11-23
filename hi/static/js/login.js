/* Login page: switch the form between student, driver and admin. */

(function () {
  "use strict";

  const form = document.getElementById("login-form");
  const label = document.getElementById("login-username-label");
  const username = document.getElementById("username");
  if (!form) return;

  const fields = {
    student: {
      label: "USN",
      placeholder: "Your USN, e.g. 1RV21CS045",
      autocomplete: "username",
    },
    driver: {
      label: "Driver username",
      placeholder: "Your driver username",
      autocomplete: "username",
    },
    admin: {
      label: "Admin username",
      placeholder: "Admin username",
      autocomplete: "username",
    },
  };

  function selectRole(role) {
    if (!fields[role]) return;
    form.action = "/login/" + role;
    label.textContent = fields[role].label;
    username.placeholder = fields[role].placeholder;
    username.setAttribute("autocomplete", fields[role].autocomplete);
    document.querySelectorAll(".role-tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.role === role);
    });
    username.focus();
  }

  document.querySelectorAll(".role-tab").forEach((tab) => {
    tab.addEventListener("click", () => selectRole(tab.dataset.role));
  });
})();
