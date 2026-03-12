"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let sessionId  = null;
let reportData = null;
const accepted = new Map(); // "paraIndex::ruleId" → boolean

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

const $ = id => document.getElementById(id);
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
const fileDrop      = $("file-drop");

fileInput.addEventListener("change", () => {
  const name = fileInput.files[0]?.name ?? null;
  if (name) {
    fileDropLabel.textContent = name;
    fileDropLabel.classList.add("has-file");
    submitBtn.disabled = false;
  } else {
    fileDropLabel.textContent = "Drop your file here, or click to browse";
    fileDropLabel.classList.remove("has-file");
    submitBtn.disabled = true;
  }
});

fileDrop.addEventListener("dragover",  e => { e.preventDefault(); fileDrop.classList.add("drag-over"); });
fileDrop.addEventListener("dragleave", () => fileDrop.classList.remove("drag-over"));
fileDrop.addEventListener("drop", e => {
  e.preventDefault();
  fileDrop.classList.remove("drag-over");
  if (e.dataTransfer.files.length) {
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
// Review – main entry point
// ---------------------------------------------------------------------------

function renderReview(data) {
  reportData = data;
  sessionId  = data.session_id;
  accepted.clear();

  // Pre-accept all changes
  for (const para of data.paragraphs) {
    for (const change of para.changes) {
      accepted.set(acceptedKey(para.index, change.rule_id), true);
    }
  }

  renderSummary(data);
  renderDocColumns(data.paragraphs);
  renderChangesPanel(data.paragraphs);

  $("upload-section").hidden = true;
  $("review-section").hidden = false;
}

function acceptedKey(paraIndex, ruleId) {
  return `${paraIndex}::${ruleId}`;
}

// ---------------------------------------------------------------------------
// Summary banner
// ---------------------------------------------------------------------------

function renderSummary(data) {
  const s = data.summary;
  const banner = $("summary-banner");

  const parts = [
    `<span class="summary-stat">
       <span class="pill pill-change">${s.changes}</span>
       <span class="label">${s.changes === 1 ? "change" : "changes"}</span>
     </span>`,
    `<span class="summary-stat">
       <span class="pill pill-violation">${s.violations}</span>
       <span class="label">${s.violations === 1 ? "violation" : "violations"}</span>
     </span>`,
  ];
  if (s.changes === 0 && s.violations === 0) {
    parts.push(`<span class="pill pill-clean">&#10003; Clean</span>`);
  }
  parts.push(`<span class="template-name">${escapeHtml(data.template_name)}</span>`);
  banner.innerHTML = parts.join("");
}

// ---------------------------------------------------------------------------
// Document columns
// ---------------------------------------------------------------------------

function renderDocColumns(paragraphs) {
  const origCol = $("original-column");
  const corrCol = $("corrected-column");
  origCol.innerHTML = "";
  corrCol.innerHTML = "";

  for (const para of paragraphs) {
    origCol.appendChild(buildDocPara(para, "original"));
    corrCol.appendChild(buildDocPara(para, "corrected"));
  }

  applyUnchangedVisibility();
  requestAnimationFrame(syncParaHeights);
}

function buildDocPara(para, side) {
  const div = document.createElement("div");
  div.className = "doc-para";
  div.dataset.paraIndex = para.index;
  div.dataset.side = side;

  // Apply Word-like styling from document metadata
  applyDocStyle(div, para);

  if (para.original.trim() === "") {
    div.innerHTML = "\u00a0";
    div.classList.add("empty-para");
  } else if (side === "original") {
    div.innerHTML = buildOriginalHtml(para);
    if (para.has_changes)    div.classList.add("changed-orig");
    if (para.has_violations) div.classList.add("violated");
  } else {
    div.innerHTML = buildCorrectedHtml(para);
    if (para.has_changes)    div.classList.add("changed-corr");
    if (para.has_violations) div.classList.add("violated");
  }

  if (!para.has_changes && !para.has_violations) {
    div.classList.add("unchanged");
  }

  return div;
}

function applyDocStyle(div, para) {
  const styleName = para.style_name || "Normal";

  // Map Word style names → CSS class
  if      (/^Title$/i.test(styleName))              div.classList.add("doc-style-title");
  else if (/^Heading\s*1$/i.test(styleName))        div.classList.add("doc-style-h1");
  else if (/^Heading\s*2$/i.test(styleName))        div.classList.add("doc-style-h2");
  else if (/^Heading\s*3$/i.test(styleName))        div.classList.add("doc-style-h3");
  else if (/^Heading\s*[4-9]$/i.test(styleName))   div.classList.add("doc-style-h4");
  else                                               div.classList.add("doc-style-body");

  // Inline style overrides (body text only; headings are handled by CSS)
  const isHeading = /^(Title|Heading)/i.test(styleName);
  const parts = [];

  if (!isHeading && para.font_size) {
    // Convert pt → px and scale slightly (Word 11pt ≈ browser 14px, we scale to ~13px)
    const px = Math.round(para.font_size * 1.25);
    if (px > 8 && px < 28) parts.push(`font-size: ${px}px`);
  }
  if (para.bold   === true  && !isHeading) parts.push("font-weight: 700");
  if (para.italic === true)                parts.push("font-style: italic");
  if (para.color_hex)                      parts.push(`color: #${para.color_hex}`);

  if (parts.length) div.setAttribute("style", parts.join("; "));
}

// Sync min-heights so corresponding left/right paragraphs stay aligned
function syncParaHeights() {
  const origParas = [...document.querySelectorAll("#original-column .doc-para")];
  const corrParas = [...document.querySelectorAll("#corrected-column .doc-para")];

  // Reset
  origParas.forEach(e => e.style.minHeight = "");
  corrParas.forEach(e => e.style.minHeight = "");

  // Apply
  origParas.forEach((e, i) => {
    const c = corrParas[i];
    if (!c) return;
    const h = Math.max(e.offsetHeight, c.offsetHeight);
    e.style.minHeight = h + "px";
    c.style.minHeight = h + "px";
  });
}

// Re-sync on window resize
const _resizeObs = new ResizeObserver(() => syncParaHeights());
_resizeObs.observe(document.body);

// ---------------------------------------------------------------------------
// Build paragraph HTML
// ---------------------------------------------------------------------------

function buildOriginalHtml(para) {
  let html = escapeHtml(para.original);
  for (const change of para.changes) {
    if (isAccepted(para.index, change.rule_id) && change.find) {
      html = highlightPattern(html, change.find, change.case_sensitive, change.whole_word, "del");
    }
  }
  return html;
}

function buildCorrectedHtml(para) {
  // Re-apply accepted substitutions client-side for live preview
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

// ---------------------------------------------------------------------------
// Changes & violations panel
// ---------------------------------------------------------------------------

function renderChangesPanel(paragraphs) {
  const panel = $("changes-panel");
  panel.innerHTML = "";

  for (const para of paragraphs) {
    if (para.changes.length > 0) {
      panel.appendChild(buildChangesItem(para));
    }
    if (para.violations.length > 0) {
      panel.appendChild(buildViolationsItem(para.violations));
    }
  }

  if (panel.children.length === 0) {
    const msg = el("div", "clean-msg");
    msg.innerHTML = `<span class="pill pill-clean">&#10003; No changes or violations found</span>`;
    panel.appendChild(msg);
  }
}

function buildChangesItem(para) {
  const item = el("div", "change-item");
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
      refreshDocPara(para.index);
    });

    const desc = el("label", "change-desc");
    desc.innerHTML = `<strong>${escapeHtml(change.description)}</strong>`;
    if (change.find && change.replace) {
      desc.innerHTML +=
        `<span class="arrow-sep">&middot;</span>` +
        `<mark class="del">${escapeHtml(change.find)}</mark>` +
        ` &#8594; ` +
        `<mark class="ins">${escapeHtml(change.replace)}</mark>`;
    }

    row.appendChild(chk);
    row.appendChild(desc);
    item.appendChild(row);
  }
  return item;
}

function buildViolationsItem(violations) {
  const wrap = el("div", "violation-item-wrap");
  for (const v of violations) {
    const item = el("div", "violation-item");
    item.innerHTML =
      `<span class="icon">&#9888;</span>` +
      `<span><strong>${escapeHtml(v.description)}</strong> &mdash; ${escapeHtml(v.detail)}</span>`;
    wrap.appendChild(item);
  }
  return wrap;
}

// ---------------------------------------------------------------------------
// Refresh a single corrected paragraph after accept/reject toggle
// ---------------------------------------------------------------------------

function refreshDocPara(paraIndex) {
  const para = reportData.paragraphs.find(p => p.index === paraIndex);
  if (!para) return;

  const corrEl = document.querySelector(`#corrected-column .doc-para[data-para-index="${paraIndex}"]`);
  const origEl = document.querySelector(`#original-column .doc-para[data-para-index="${paraIndex}"]`);

  if (origEl) origEl.innerHTML = buildOriginalHtml(para);
  if (corrEl) corrEl.innerHTML = buildCorrectedHtml(para);

  // Re-sync heights for this pair
  if (origEl && corrEl) {
    origEl.style.minHeight = "";
    corrEl.style.minHeight = "";
    const h = Math.max(origEl.offsetHeight, corrEl.offsetHeight);
    origEl.style.minHeight = h + "px";
    corrEl.style.minHeight = h + "px";
  }
}

// ---------------------------------------------------------------------------
// Accept/reject helpers
// ---------------------------------------------------------------------------

function isAccepted(paraIndex, ruleId) {
  return accepted.get(acceptedKey(paraIndex, ruleId)) !== false;
}

function getAcceptedRuleIds() {
  const ids = new Set();
  for (const [key, val] of accepted.entries()) {
    if (val) ids.add(key.split("::")[1]);
  }
  return [...ids];
}

// ---------------------------------------------------------------------------
// Show / hide unchanged paragraphs
// ---------------------------------------------------------------------------

$("show-all-toggle").addEventListener("change", applyUnchangedVisibility);

function applyUnchangedVisibility() {
  const show = $("show-all-toggle").checked;
  document.querySelectorAll(".doc-para.unchanged").forEach(el => { el.hidden = !show; });
  requestAnimationFrame(syncParaHeights);
}

// ---------------------------------------------------------------------------
// Back button
// ---------------------------------------------------------------------------

$("back-btn").addEventListener("click", () => {
  $("review-section").hidden = true;
  $("upload-section").hidden = false;
  sessionId = null; reportData = null; accepted.clear();
  $("upload-form").reset();
  fileDropLabel.textContent = "Drop your file here, or click to browse";
  fileDropLabel.classList.remove("has-file");
  submitBtn.disabled = true;
});

// ---------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------

$("download-btn").addEventListener("click", async () => {
  if (!sessionId) return;

  const btn = $("download-btn");
  btn.disabled = true;
  btn.textContent = "Preparing…";

  try {
    const res = await fetch("/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, accepted_rule_ids: getAcceptedRuleIds() }),
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
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "&#8681; Download";
  }
});

// ---------------------------------------------------------------------------
// Text utilities
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

function applySubstitution(text, find, replace, caseSensitive, wholeWord) {
  const escaped = escapeRegex(find);
  const pattern = wholeWord ? `\\b${escaped}\\b` : escaped;
  const flags   = caseSensitive ? "g" : "gi";
  return text.replace(new RegExp(pattern, flags), replace);
}

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
