/* Small DOM helpers shared by the auth pages. */

export const $ = (id) => document.getElementById(id);

/* Show/clear the inline error under a field. */
export function setFieldError(fieldId, message) {
  const input = $(fieldId);
  const slot = $(`${fieldId}-error`);
  if (slot) slot.textContent = message || "";
  if (input) {
    if (message) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }
  return !message;
}

export function clearFieldErrors(fieldIds) {
  fieldIds.forEach((id) => setFieldError(id, ""));
}

/* Apply a {field: message} map from the API, and focus the first bad field. */
export function applyFieldErrors(fieldIds, errors) {
  fieldIds.forEach((id) => setFieldError(id, errors?.[id] || ""));
  const first = fieldIds.find((id) => errors?.[id]);
  if (first) $(first)?.focus();
  return first || null;
}

/* Banner at the top of the form. kind: "error" | "success", or no text to clear. */
export function setFormMessage(text, kind) {
  const el = $("form-message");
  if (!el) return;
  el.textContent = text || "";
  el.className = "form-message" + (text ? ` is-${kind || "error"}` : "");
}

/* Fill a <select>. Items may be strings, {value,label}, or {group,options}. */
export function populateSelect(selectId, options, placeholder) {
  const select = $(selectId);
  if (!select) return;
  select.innerHTML = "";

  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = placeholder || "Select…";
  blank.disabled = true;
  blank.selected = true;
  select.appendChild(blank);

  const makeOption = (item) => {
    const opt = document.createElement("option");
    if (typeof item === "string") {
      opt.value = item;
      opt.textContent = item;
    } else {
      opt.value = item.value;
      opt.textContent = item.label;
    }
    return opt;
  };

  options.forEach((item) => {
    if (item && item.group) {
      const group = document.createElement("optgroup");
      group.label = item.group;
      item.options.forEach((child) => group.appendChild(makeOption(child)));
      select.appendChild(group);
    } else {
      select.appendChild(makeOption(item));
    }
  });
}

/* Wire every [data-target] eye button to its password input. */
export function initPasswordToggles() {
  document.querySelectorAll(".toggle-password").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = $(btn.dataset.target);
      if (!input) return;
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      btn.setAttribute("aria-pressed", String(!showing));
      btn.setAttribute("aria-label", showing ? "Show password" : "Hide password");
      input.focus();
    });
  });
}

/* Disable a button and swap its label while an async action runs. */
export function withPendingState(button, pendingLabel) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = pendingLabel;
  return () => {
    button.disabled = false;
    button.textContent = original;
  };
}
