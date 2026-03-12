"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let sessionId = null;
let reportData = null;          // full /lint JSON response
const accepted = new Map();     // rule_id → boolean (accepted/rejected per para+rule)

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

const $  = id => document.getElementById(id);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls)  e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};

// ---------------------------------------------------------------------------
// Upload form
// ---------------------------------------------------------------------------

const fileInput     = $("file-input");
const fileDropLabel = $("file-drop-label");
const submitBtn     = $("submit-btn");
const uploadError   = $("upload-error");
const fileDrop      = $("file-drop");

fileInput.addEventListener("change", () => {
  const name = fileInput.files[0]?.name ?? null;
  if (name) {
    fileDropLabel.textContent = name;
    fileDropLabel.classList.add("has-file");
    submitBtn.disabled = false;
  } else {
    fileDropLabel.textContent = "Click to choose a .docx file";
    fileDropLabel.classList.remove("has-file");
    submitBtn.disabled = true;
  }
});

// Drag-and-drop styling
fileDrop.addEventListener("dragover",  e => { e.preventDefault(); fileDrop.classList.add("drag-over"); });
fileDrop.addEventListener("dragleave", () => fileDrop.classList.remove("drag-over"));
fileDrop.addEventListener("drop",      e => {
  e.preventDefault();
  fileDrop.classList.remove("drag-over");
  if (e.dataTransfer.files.length) {
    // Programmatically assign dropped file to the input
    const dt = new DataTransfer();
    dt.items.add(e.dataTransfer.files[0]);
    fileInput.files = dt.files;
    fileInput.dispatchEvent(new Event("change"));
  }
});

$("upload-form").addEventListener("submit", async e => {
  e.preventDefault();
  showError(null);
  showLoading(true);

  const form = new FormData(e.target);
  let data;
  try {
    const res = await fetch("/lint", { method: "POST", body: form });
    data = await res.json();
    if (!res.ok) throw new Error(data.error ?? "Server error");
  } catch (err) {
    showLoading(false);
    showError(err.message);
    return;
  }

  showLoading(false);
  renderReview(data);
});

// ---------------------------------------------------------------------------
// Review rendering
// ---------------------------------------------------------------------------

function renderReview(data) {
  reportData = data;
  sessionId  = data.session_id;
  accepted.clear();

  // Pre-populate accepted map: all changes accepted by default
  for (const para of data.paragraphs) {
    for (const change of para.changes) {
      accepted.set(acceptedKey(para.index, change.rule_id), true);
    }
  }

  renderSummary(data);
  renderParagraphs(data.paragraphs);

  $("upload-section").hidden = true;
  $("review-section").hidden = false;
}

function acceptedKey(paraIndex, ruleId) {
  return `${paraIndex}::${ruleId}`;
}

// ── Summary banner ─────────────────────────────────────────────────────────

function renderSummary(data) {
  const s = data.summary;
  const banner = $("summary-banner");
  banner.className = "summary-banner" +
    (s.violations > 0 ? " has-violations" : s.changes === 0 ? " clean" : "");

  const parts = [
    `<span class="summary-stat">
       <span class="pill pill-change">${s.changes}</span>
       <span class="label">text ${s.changes === 1 ? "change" : "changes"}</span>
     </span>`,
    `<span class="summary-stat">
       <span class="pill pill-violation">${s.violations}</span>
       <span class="label">style ${s.violations === 1 ? "violation" : "violations"}</span>
     </span>`,
  ];
  if (s.changes === 0 && s.violations === 0) {
    parts.push(`<span class="pill pill-clean">&#10003; Document is clean</span>`);
  }
  parts.push(`<span class="label" style="margin-left:auto;color:var(--gray-400)">${data.template_name}</span>`);
  banner.innerHTML = parts.join("");
}

// ── Paragraph rows ──────────────────────────────────────────────────────────

function renderParagraphs(paragraphs) {
  const container = $("paragraphs-container");
  container.innerHTML = "";

  for (const para of paragraphs) {
    container.appendChild(buildParaRow(para));
  }

  // "Show unchanged" toggle
  applyUnchangedVisibility();
}

function buildParaRow(para) {
  const row = el("div", "para-row");
  if (!para.has_changes && !para.has_violations) {
    row.classList.add("unchanged");
  } else if (para.has_changes) {
    row.classList.add("changed");
  } else {
    row.classList.add("violation");
  }
  row.dataset.paraIndex = para.index;

  // Left cell: original with deleted phrases highlighted
  const leftCell = el("div", "para-cell original");
  leftCell.dataset.paraIndex = para.index;
  leftCell.dataset.side = "left";
  leftCell.innerHTML = buildOriginalHtml(para);

  // Right cell: corrected with inserted phrases highlighted
  const rightCell = el("div", "para-cell corrected");
  rightCell.dataset.paraIndex = para.index;
  rightCell.dataset.side = "right";
  rightCell.innerHTML = buildCorrectedHtml(para);

  row.appendChild(leftCell);
  row.appendChild(rightCell);

  // Change controls (accept / reject checkboxes)
  if (para.changes.length > 0) {
    row.appendChild(buildChangeControls(para));
  }

  // Violation list
  if (para.violations.length > 0) {
    row.appendChild(buildViolationList(para.violations));
  }

  return row;
}

// Build the HTML for the original cell, highlighting accepted `find` phrases.
function buildOriginalHtml(para) {
  if (para.original.trim() === "") return "<em style='color:var(--gray-400)'>—</em>";
  let html = escapeHtml(para.original);
  for (const change of para.changes) {
    if (isAccepted(para.index, change.rule_id) && change.find) {
      html = highlightPattern(html, change.find, change.case_sensitive, change.whole_word, "del");
    }
  }
  return html;
}

// Build the HTML for the corrected cell.  Re-applies accepted substitutions
// to the original text client-side so the live preview updates on toggle.
function buildCorrectedHtml(para) {
  if (para.original.trim() === "") return "<em style='color:var(--gray-400)'>—</em>";

  // Re-compute corrected text from original with only accepted rules
  let text = para.original;
  for (const change of para.changes) {
    if (isAccepted(para.index, change.rule_id) && change.find) {
      text = applySubstitution(text, change.find, change.replace, change.case_sensitive, change.whole_word);
    }
  }

  let html = escapeHtml(text);
  for (const change of para.changes) {
    if (isAccepted(para.index, change.rule_id) && change.replace) {
      html = highlightPattern(html, change.replace, change.case_sensitive, change.whole_word, "ins");
    }
  }
  return html;
}

function buildChangeControls(para) {
  const wrap = el("div", "para-changes");
  for (const change of para.changes) {
    const key = acceptedKey(para.index, change.rule_id);
    const row = el("div", "change-row");

    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.checked = accepted.get(key) !== false;
    chk.dataset.paraIndex = para.index;
    chk.dataset.ruleId    = change.rule_id;
    chk.addEventListener("change", () => {
      accepted.set(key, chk.checked);
      refreshParaRow(para.index);
    });

    const desc = el("label", "change-desc");
    desc.htmlFor = `chk-${key}`;
    desc.innerHTML = `<strong>${change.description}</strong>`;
    if (change.find && change.replace) {
      desc.innerHTML += ` &nbsp;<span style="color:var(--gray-400)">·</span>&nbsp;` +
        `<mark class="del">${escapeHtml(change.find)}</mark> ` +
        `&#8594; <mark class="ins">${escapeHtml(change.replace)}</mark>`;
    }

    row.appendChild(chk);
    row.appendChild(desc);
    wrap.appendChild(row);
  }
  return wrap;
}

function buildViolationList(violations) {
  const wrap = el("div", "violation-list");
  for (const v of violations) {
    const item = el("div", "violation-item");
    item.innerHTML =
      `<span class="icon">&#9888;</span>` +
      `<span><strong>${escapeHtml(v.description)}</strong> &mdash; ${escapeHtml(v.detail)}</span>`;
    wrap.appendChild(item);
  }
  return wrap;
}

// Re-render just the two text cells for a paragraph after a toggle.
function refreshParaRow(paraIndex) {
  const para = reportData.paragraphs.find(p => p.index === paraIndex);
  if (!para) return;

  const container = $("paragraphs-container");
  const leftCell  = container.querySelector(`.para-cell[data-para-index="${paraIndex}"][data-side="left"]`);
  const rightCell = container.querySelector(`.para-cell[data-para-index="${paraIndex}"][data-side="right"]`);
  if (leftCell)  leftCell.innerHTML  = buildOriginalHtml(para);
  if (rightCell) rightCell.innerHTML = buildCorrectedHtml(para);
}

// ---------------------------------------------------------------------------
// Accept/reject helpers
// ---------------------------------------------------------------------------

function isAccepted(paraIndex, ruleId) {
  return accepted.get(acceptedKey(paraIndex, ruleId)) !== false;
}

function getAcceptedRuleIds() {
  // Return the union of all rule IDs that are accepted across all paragraphs.
  // For rules that appear in multiple paragraphs, a rule is "accepted" if it
  // is accepted in at least one paragraph (server re-applies globally).
  const ids = new Set();
  for (const [key, val] of accepted.entries()) {
    if (val) {
      const ruleId = key.split("::")[1];
      ids.add(ruleId);
    }
  }
  return [...ids];
}

// ---------------------------------------------------------------------------
// Show/hide unchanged paragraphs
// ---------------------------------------------------------------------------

$("show-all-toggle").addEventListener("change", applyUnchangedVisibility);

function applyUnchangedVisibility() {
  const show = $("show-all-toggle").checked;
  document
    .querySelectorAll(".para-row.unchanged")
    .forEach(row => { row.hidden = !show; });
}

// ---------------------------------------------------------------------------
// Back button
// ---------------------------------------------------------------------------

$("back-btn").addEventListener("click", () => {
  $("review-section").hidden = true;
  $("upload-section").hidden = false;
  sessionId = null; reportData = null; accepted.clear();
  $("upload-form").reset();
  fileDropLabel.textContent = "Click to choose a .docx file";
  fileDropLabel.classList.remove("has-file");
  submitBtn.disabled = true;
});

// ---------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------

$("download-btn").addEventListener("click", async () => {
  if (!sessionId) return;

  $("download-btn").disabled = true;
  $("download-btn").textContent = "Preparing…";

  try {
    const res = await fetch("/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        accepted_rule_ids: getAcceptedRuleIds(),
      }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error ?? "Download failed");
    }

    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") ?? "";
    const match = disposition.match(/filename[^;=\n]*=["']?([^"';\n]+)/i);
    const filename = match ? match[1] : "corrected.docx";

    const url = URL.createObjectURL(blob);
    const a   = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(err.message);
  } finally {
    $("download-btn").disabled = false;
    $("download-btn").innerHTML = "&#8681; Download corrected document";
  }
});

// ---------------------------------------------------------------------------
// Text processing utilities
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Apply a substitution rule to plain text (no HTML involved).
function applySubstitution(text, find, replace, caseSensitive, wholeWord) {
  const escaped = escapeRegex(find);
  const pattern = wholeWord ? `\\b${escaped}\\b` : escaped;
  const flags   = caseSensitive ? "g" : "gi";
  return text.replace(new RegExp(pattern, flags), replace);
}

// Wrap occurrences of `phrase` in already-HTML-escaped text with a <mark>.
// We escape the phrase, search the escaped HTML, and wrap with the mark tag.
// This works because the text cells contain only escaped text (no inner HTML
// from user content), so phrase matching on the HTML string is safe.
function highlightPattern(escapedHtml, phrase, caseSensitive, wholeWord, markClass) {
  const escapedPhrase = escapeRegex(escapeHtml(phrase));
  const pattern = wholeWord ? `\\b${escapedPhrase}\\b` : escapedPhrase;
  const flags   = caseSensitive ? "g" : "gi";
  return escapedHtml.replace(
    new RegExp(pattern, flags),
    m => `<mark class="${markClass}">${m}</mark>`
  );
}

// ---------------------------------------------------------------------------
// UI utilities
// ---------------------------------------------------------------------------

function showLoading(on) {
  $("loading-overlay").hidden = !on;
}

function showError(msg) {
  const banner = $("upload-error");
  if (msg) {
    banner.textContent = msg;
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}
