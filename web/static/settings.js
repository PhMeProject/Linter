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
// DOM helpers
// ---------------------------------------------------------------------------

const $ = id => document.getElementById(id);

function qs(section, cls) {
  return document.querySelector(`.accordion-item[data-section="${section}"] .${cls}`);
}

function qsAll(section, cls) {
  return document.querySelectorAll(`.accordion-item[data-section="${section}"] .${cls}`);
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

// ---------------------------------------------------------------------------
// Toggle (Yes / No)
// ---------------------------------------------------------------------------

function setToggle(section, field, isYes) {
  const group  = document.querySelector(
    `.accordion-item[data-section="${section}"] [data-toggle="${field}"]`
  );
  const noBtn  = group.querySelector('[data-value="no"]');
  const yesBtn = group.querySelector('[data-value="yes"]');
  noBtn.className  = isYes ? "sp-toggle-btn"            : "sp-toggle-btn sp-toggle-btn--no";
  yesBtn.className = isYes ? "sp-toggle-btn sp-toggle-btn--yes" : "sp-toggle-btn";
}

function readToggle(section, field) {
  const group = document.querySelector(
    `.accordion-item[data-section="${section}"] [data-toggle="${field}"]`
  );
  return !!group.querySelector(".sp-toggle-btn--yes");
}

// ---------------------------------------------------------------------------
// Populate from saved data
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
    sizeSel.value    = String(size);
    sizeCustom.hidden = true;
  } else {
    sizeSel.value     = "other";
    sizeCustom.value  = size;
    sizeCustom.hidden = false;
  }

  // Font Color
  const raw   = d.font_color || DEFAULTS.font_color;
  const hex   = raw.startsWith("#") ? raw : "#" + raw;
  qs(section, "color-hex").value      = hex.toUpperCase();
  qs(section, "color-picker").value   = normalizeHex(hex);

  // Toggles
  setToggle(section, "bold",   d.bold   === true);
  setToggle(section, "italic", d.italic === true);
}

// ---------------------------------------------------------------------------
// Read current form state
// ---------------------------------------------------------------------------

function readSection(section) {
  const sizeSel    = qs(section, "fs-select");
  const sizeCustom = qs(section, "fs-custom");
  const fontSize   = sizeSel.value === "other"
    ? Math.min(72, Math.max(6, Number(sizeCustom.value) || DEFAULTS.font_size))
    : Number(sizeSel.value);

  return {
    font_family: qs(section, "ff-select").value,
    font_size:   fontSize,
    font_color:  qs(section, "color-hex").value,
    bold:        readToggle(section, "bold"),
    italic:      readToggle(section, "italic"),
  };
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
    const v = hexInput.value.trim();
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
// Toggle buttons
// ---------------------------------------------------------------------------

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
// Toast
// ---------------------------------------------------------------------------

function showToast() {
  const toast = $("toast");
  toast.hidden = false;
  // Force reflow so transition plays
  void toast.offsetWidth;
  toast.classList.add("sp-toast--visible");
  setTimeout(() => {
    toast.classList.remove("sp-toast--visible");
    setTimeout(() => { toast.hidden = true; }, 250);
  }, 2200);
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

async function saveSettings() {
  const btn = $("save-rules-btn");
  btn.disabled = true;

  const payload = {
    template_name: $("template-name").value.trim(),
    typography:    Object.fromEntries(SECTIONS.map(s => [s, readSection(s)])),
  };

  try {
    const res = await fetch("/api/settings", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    });
    if (!res.ok) throw new Error("Save failed");
    showToast();
  } catch (err) {
    alert("Could not save settings: " + err.message);
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalizeHex(color) {
  let h = color.startsWith("#") ? color : "#" + color;
  if (h.length === 4) {
    h = "#" + h[1]+h[1] + h[2]+h[2] + h[3]+h[3];
  }
  return h.toLowerCase();
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  // Wire up interactivity for each section
  SECTIONS.forEach(s => {
    wireColorSync(s);
    wireFontSizeOther(s);
    wireToggles(s);
  });

  initAccordions();

  // Load saved settings and populate
  try {
    const res = await fetch("/api/settings");
    if (res.ok) {
      const saved = await res.json();
      if ($("template-name") && saved.template_name) {
        $("template-name").value = saved.template_name;
      }
      SECTIONS.forEach(s => populateSection(s, saved?.typography?.[s]));
    }
  } catch (_) {
    // No saved settings – defaults already set in HTML
  }

  $("save-rules-btn").addEventListener("click", saveSettings);

  $("settings-gear-btn").addEventListener("click", () => {
    window.location.href = "/settings";
  });
}

init();
