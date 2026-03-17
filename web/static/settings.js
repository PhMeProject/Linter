"use strict";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SECTIONS = ["h1", "body", "captions"];

const DEFAULTS = {
  font_family: "Calibri",
  font_size:   11,
  font_color:  "#000000",
  bold:        false,
  italic:      false,
};

const STD_SIZES = new Set([8,9,10,11,12,14,16,18,20,24,28,32,36,48,72]);

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let currentTemplateId = null;   // null → creating new; string → editing existing
let pendingDeleteId   = null;   // id queued for deletion confirmation

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

const $ = id => document.getElementById(id);

function qs(section, cls) {
  return document.querySelector(`.accordion-item[data-section="${section}"] .${cls}`);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// View management
// ---------------------------------------------------------------------------

function showListView() {
  $("view-list").hidden = false;
  $("view-form").hidden = true;
  currentTemplateId = null;
}

function showFormView(title) {
  $("view-list").hidden = true;
  $("view-form").hidden = false;
  $("form-title").textContent = title;
  closeAllAccordions();
}

// ---------------------------------------------------------------------------
// Template list
// ---------------------------------------------------------------------------

async function loadTemplateList() {
  const items = $("template-list-items");
  const empty = $("template-list-empty");
  try {
    const res  = await fetch("/api/templates");
    const list = res.ok ? await res.json() : [];
    items.innerHTML = "";
    if (list.length === 0) {
      empty.hidden = false;
    } else {
      empty.hidden = true;
      list.forEach(t => items.appendChild(buildTemplateRow(t)));
    }
  } catch (_) {
    empty.hidden = false;
  }
}

function buildTemplateRow(t) {
  const row = document.createElement("div");
  row.className = "sp-template-row";
  row.dataset.id   = t.id;
  row.dataset.name = t.template_name;
  row.innerHTML = `
    <span class="sp-template-name">${escapeHtml(t.template_name || "(Untitled)")}</span>
    <div class="sp-template-actions">
      <button type="button"
              class="sp-icon-btn sp-icon-btn--edit edit-btn"
              data-id="${escapeHtml(t.id)}"
              title="Edit">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
             fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round" aria-hidden="true">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
        </svg>
      </button>
      <button type="button"
              class="sp-icon-btn sp-icon-btn--danger delete-btn"
              data-id="${escapeHtml(t.id)}"
              data-name="${escapeHtml(t.template_name || "(Untitled)")}"
              title="Delete">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
             fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round" aria-hidden="true">
          <polyline points="3 6 5 6 21 6"/>
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
          <path d="M10 11v6"/><path d="M14 11v6"/>
          <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
        </svg>
      </button>
    </div>`;
  return row;
}

// ---------------------------------------------------------------------------
// Open new form
// ---------------------------------------------------------------------------

function openNewForm() {
  currentTemplateId = null;
  resetForm();
  showFormView("New Template");
}

// ---------------------------------------------------------------------------
// Open edit form
// ---------------------------------------------------------------------------

async function openEditForm(id) {
  try {
    const res = await fetch(`/api/templates/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error("Not found");
    const data = await res.json();

    currentTemplateId = id;
    resetForm();

    $("template-name").value = data.template_name || "";
    const typo = data.typography || {};
    SECTIONS.forEach(s => populateSection(s, typo[s]));

    showFormView("Edit Template");
  } catch (err) {
    alert("Could not load template: " + err.message);
  }
}

// ---------------------------------------------------------------------------
// Form: reset to defaults
// ---------------------------------------------------------------------------

function resetForm() {
  $("template-name").value = "";
  SECTIONS.forEach(s => populateSection(s, null));
}

// ---------------------------------------------------------------------------
// Form: populate section from saved data (null → all defaults)
// ---------------------------------------------------------------------------

function populateSection(section, data) {
  const d = data || {};

  // Font Family
  qs(section, "ff-select").value = d.font_family || DEFAULTS.font_family;

  // Font Size
  const sizeSel    = qs(section, "fs-select");
  const sizeCustom = qs(section, "fs-custom");
  const size       = (d.font_size != null) ? Number(d.font_size) : DEFAULTS.font_size;
  if (STD_SIZES.has(size)) {
    sizeSel.value     = String(size);
    sizeCustom.hidden = true;
  } else {
    sizeSel.value     = "other";
    sizeCustom.value  = size;
    sizeCustom.hidden = false;
  }

  // Font Color
  const raw = d.font_color || DEFAULTS.font_color;
  const hex = raw.startsWith("#") ? raw : "#" + raw;
  qs(section, "color-hex").value    = hex.toUpperCase();
  qs(section, "color-picker").value = normalizeHex(hex);

  // Toggles
  setToggle(section, "bold",   d.bold   === true);
  setToggle(section, "italic", d.italic === true);
}

// ---------------------------------------------------------------------------
// Form: read section values — null for every field still at its default
// ---------------------------------------------------------------------------

function readSection(section) {
  const sizeSel    = qs(section, "fs-select");
  const sizeCustom = qs(section, "fs-custom");
  const rawSize    = sizeSel.value === "other"
    ? Math.min(72, Math.max(6, Number(sizeCustom.value) || DEFAULTS.font_size))
    : Number(sizeSel.value);

  const fontFamily = qs(section, "ff-select").value;
  const fontColor  = qs(section, "color-hex").value.trim().toUpperCase();

  return {
    font_family: fontFamily === DEFAULTS.font_family                         ? null : fontFamily,
    font_size:   rawSize    === DEFAULTS.font_size                           ? null : rawSize,
    font_color:  (!fontColor || fontColor === DEFAULTS.font_color.toUpperCase()) ? null : fontColor,
    bold:        readToggle(section, "bold")   ? true : null,
    italic:      readToggle(section, "italic") ? true : null,
  };
}

// ---------------------------------------------------------------------------
// Accordion
// ---------------------------------------------------------------------------

function initAccordions() {
  document.querySelectorAll(".accordion-header").forEach(header => {
    header.addEventListener("click", () => {
      const item   = header.closest(".accordion-item");
      const body   = item.querySelector(".accordion-body");
      const isOpen = !body.hidden;
      body.hidden  = isOpen;
      item.classList.toggle("open", !isOpen);
    });
  });
}

function closeAllAccordions() {
  document.querySelectorAll(".accordion-item").forEach(item => {
    item.querySelector(".accordion-body").hidden = true;
    item.classList.remove("open");
  });
}

// ---------------------------------------------------------------------------
// Toggle (Yes / No)
// ---------------------------------------------------------------------------

function setToggle(section, field, isYes) {
  const group  = document.querySelector(
    `.accordion-item[data-section="${section}"] [data-toggle="${field}"]`
  );
  const noBtn  = group.querySelector('[data-value="no"]');
  const yesBtn = group.querySelector('[data-value="yes"]');
  noBtn.className  = isYes ? "sp-toggle-btn"                : "sp-toggle-btn sp-toggle-btn--no";
  yesBtn.className = isYes ? "sp-toggle-btn sp-toggle-btn--yes" : "sp-toggle-btn";
}

function readToggle(section, field) {
  const group = document.querySelector(
    `.accordion-item[data-section="${section}"] [data-toggle="${field}"]`
  );
  return !!group.querySelector(".sp-toggle-btn--yes");
}

function wireToggles(section) {
  document
    .querySelectorAll(`.accordion-item[data-section="${section}"] .sp-toggle`)
    .forEach(group => {
      const field = group.dataset.toggle;
      group.querySelectorAll(".sp-toggle-btn").forEach(btn => {
        btn.addEventListener("click", () => {
          setToggle(section, field, btn.dataset.value === "yes");
        });
      });
    });
}

// ---------------------------------------------------------------------------
// Color sync (hex ↔ picker)
// ---------------------------------------------------------------------------

function wireColorSync(section) {
  const hexInput    = qs(section, "color-hex");
  const colorPicker = qs(section, "color-picker");

  colorPicker.addEventListener("input", () => {
    hexInput.value = colorPicker.value.toUpperCase();
  });

  hexInput.addEventListener("input", () => {
    const v          = hexInput.value.trim();
    const normalized = v.startsWith("#") ? v : "#" + v;
    if (/^#[0-9a-fA-F]{6}$/.test(normalized)) {
      colorPicker.value = normalized.toLowerCase();
    }
  });
}

// ---------------------------------------------------------------------------
// Font-size "Other" reveal
// ---------------------------------------------------------------------------

function wireFontSizeOther(section) {
  const sizeSel    = qs(section, "fs-select");
  const sizeCustom = qs(section, "fs-custom");
  sizeSel.addEventListener("change", () => {
    sizeCustom.hidden = sizeSel.value !== "other";
    if (!sizeCustom.hidden) sizeCustom.focus();
  });
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

async function saveTemplate() {
  const btn = $("save-rules-btn");
  btn.disabled = true;

  const payload = {
    template_name: $("template-name").value.trim(),
    typography:    Object.fromEntries(SECTIONS.map(s => [s, readSection(s)])),
  };

  try {
    let res;
    if (currentTemplateId) {
      // Update existing
      res = await fetch(`/api/templates/${encodeURIComponent(currentTemplateId)}`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(payload),
      });
    } else {
      // Create new
      res = await fetch("/api/templates", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(payload),
      });
      if (res.ok) {
        const created = await res.json();
        currentTemplateId = created.id;   // so a second save within the same session PUTs
      }
    }
    if (!res.ok) {
      let msg = "Save failed";
      try { const e = await res.json(); if (e.error) msg = e.error; } catch (_) {}
      throw new Error(msg);
    }
    await loadTemplateList();
    showListView();
    showToast();
    updateBadge(payload.template_name);
  } catch (err) {
    alert("Could not save: " + err.message);
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

function showDeleteConfirm(id, name) {
  pendingDeleteId = id;
  $("delete-name-display").textContent = name;
  $("delete-overlay").hidden = false;
}

function hideDeleteConfirm() {
  $("delete-overlay").hidden = true;
  pendingDeleteId = null;
}

async function confirmDelete() {
  if (!pendingDeleteId) return;
  const id = pendingDeleteId;
  hideDeleteConfirm();
  try {
    const res = await fetch(`/api/templates/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Delete failed");
    await loadTemplateList();
    // Refresh badge in case the deleted template was the most recent
    await loadBadge();
  } catch (err) {
    alert("Could not delete: " + err.message);
  }
}

// ---------------------------------------------------------------------------
// Active-ruleset badge
// ---------------------------------------------------------------------------

function updateBadge(name) {
  const badge  = $("active-ruleset-badge");
  const nameEl = $("active-ruleset-name");
  if (!badge) return;
  if (name && name.trim()) {
    if (nameEl) nameEl.textContent = name.trim();
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

async function loadBadge() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) return;
    const { template_name } = await res.json();
    updateBadge(template_name);
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

function showToast() {
  const toast = $("toast");
  toast.hidden = false;
  void toast.offsetWidth;
  toast.classList.add("sp-toast--visible");
  setTimeout(() => {
    toast.classList.remove("sp-toast--visible");
    setTimeout(() => { toast.hidden = true; }, 250);
  }, 2200);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalizeHex(color) {
  let h = color.startsWith("#") ? color : "#" + color;
  if (h.length === 4) h = "#" + h[1]+h[1] + h[2]+h[2] + h[3]+h[3];
  return h.toLowerCase();
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function init() {
  // Form interactions
  SECTIONS.forEach(s => {
    wireColorSync(s);
    wireFontSizeOther(s);
    wireToggles(s);
  });
  initAccordions();

  // Static button wiring
  $("new-template-btn").addEventListener("click", openNewForm);
  $("cancel-btn").addEventListener("click", showListView);
  $("save-rules-btn").addEventListener("click", saveTemplate);
  $("delete-cancel-btn").addEventListener("click", hideDeleteConfirm);
  $("delete-confirm-btn").addEventListener("click", confirmDelete);
  $("settings-gear-btn").addEventListener("click", () => {
    window.location.href = "/settings";
  });

  // Delegated clicks on the template list (edit / delete buttons)
  $("template-list-items").addEventListener("click", e => {
    const editBtn   = e.target.closest(".edit-btn");
    const deleteBtn = e.target.closest(".delete-btn");
    if (editBtn)   openEditForm(editBtn.dataset.id);
    if (deleteBtn) showDeleteConfirm(deleteBtn.dataset.id, deleteBtn.dataset.name);
  });

  // Close overlay on backdrop click
  $("delete-overlay").addEventListener("click", e => {
    if (e.target === $("delete-overlay")) hideDeleteConfirm();
  });

  // Load data
  loadTemplateList();
  loadBadge();
}

init();
