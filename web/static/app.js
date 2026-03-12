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

  // Default: accept all changes
  for (const para of data.paragraphs) {
    for (const change of para.changes) {
      accepted.set(acceptedKey(para.index, change.rule_id), true);
    }
  }

  renderSummary(data);
  renderDocColumns(data.paragraphs);
  renderViolationsSection(data.paragraphs);

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
  $("summary-banner").innerHTML = parts.join("");
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

  applyDocStyle(div, para);

  if (para.original.trim() === "") {
    div.innerHTML = "\u00a0";
    div.classList.add("empty-para");
  } else if (side === "original") {
    // Original: plain text only — no highlights
    div.innerHTML = escapeHtml(para.original);
  } else {
    // Corrected: inline change-mark spans for each substitution
    div.innerHTML = buildCorrectedHtml(para);
  }

  if (!para.has_changes && !para.has_violations) {
    div.classList.add("unchanged");
  }

  return div;
}

function applyDocStyle(div, para) {
  const styleName = para.style_name || "Normal";

  if      (/^Title$/i.test(styleName))             div.classList.add("doc-style-title");
  else if (/^Heading\s*1$/i.test(styleName))       div.classList.add("doc-style-h1");
  else if (/^Heading\s*2$/i.test(styleName))       div.classList.add("doc-style-h2");
  else if (/^Heading\s*3$/i.test(styleName))       div.classList.add("doc-style-h3");
  else if (/^Heading\s*[4-9]$/i.test(styleName))  div.classList.add("doc-style-h4");
  else                                              div.classList.add("doc-style-body");

  const isHeading = /^(Title|Heading)/i.test(styleName);
  const parts = [];
  if (!isHeading && para.font_size) {
    const px = Math.round(para.font_size * 1.25);
    if (px > 8 && px < 28) parts.push(`font-size: ${px}px`);
  }
  if (para.bold   === true && !isHeading) parts.push("font-weight: 700");
  if (para.italic === true)               parts.push("font-style: italic");
  if (para.color_hex)                     parts.push(`color: #${para.color_hex}`);
  if (parts.length) div.setAttribute("style", parts.join("; "));
}

// Sync paragraph heights so the two columns stay aligned
function syncParaHeights() {
  const origParas = [...document.querySelectorAll("#original-column .doc-para")];
  const corrParas = [...document.querySelectorAll("#corrected-column .doc-para")];

  origParas.forEach(e => e.style.minHeight = "");
  corrParas.forEach(e => e.style.minHeight = "");

  origParas.forEach((e, i) => {
    const c = corrParas[i];
    if (!c) return;
    const h = Math.max(e.offsetHeight, c.offsetHeight);
    e.style.minHeight = h + "px";
    c.style.minHeight = h + "px";
  });
}

new ResizeObserver(syncParaHeights).observe(document.body);

// ---------------------------------------------------------------------------
// Build corrected HTML with inline change-mark spans
// ---------------------------------------------------------------------------

// Split `text` on occurrences of `find` and return an array of segments.
// Each segment is either {text, ruleId:null} (plain) or
// {text, ruleId, accepted} (a substitution match).
function applyChangeToSegments(segments, change, paraIndex) {
  const escaped = escapeRegex(change.find);
  const pattern = change.whole_word ? `\\b${escaped}\\b` : escaped;
  const flags   = change.case_sensitive ? "g" : "gi";
  const re      = new RegExp(pattern, flags);
  const acc     = isAccepted(paraIndex, change.rule_id);

  const out = [];
  for (const seg of segments) {
    if (seg.ruleId !== null) { out.push(seg); continue; } // already marked

    let last = 0;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(seg.text)) !== null) {
      if (m.index > last) out.push({ text: seg.text.slice(last, m.index), ruleId: null });
      out.push({
        text:     acc ? change.replace : m[0],  // replacement or kept original
        ruleId:   change.rule_id,
        accepted: acc,
      });
      last = m.index + m[0].length;
    }
    if (last < seg.text.length) out.push({ text: seg.text.slice(last), ruleId: null });
  }
  return out.length ? out : segments;
}

function buildCorrectedHtml(para) {
  if (para.original.trim() === "") return "\u00a0";

  let segments = [{ text: para.original, ruleId: null }];

  for (const change of para.changes) {
    if (!change.find) continue;
    segments = applyChangeToSegments(segments, change, para.index);
  }

  return segments.map(seg => {
    const t = escapeHtml(seg.text);
    if (!seg.ruleId) return t;
    const cls = `change-mark ${seg.accepted ? "accepted" : "rejected"}`;
    return `<span class="${cls}" ` +
           `data-para-index="${para.index}" ` +
           `data-rule-id="${escapeHtml(seg.ruleId)}">${t}</span>`;
  }).join("");
}

// Refresh the corrected cell for one paragraph
function refreshDocPara(paraIndex) {
  const para = reportData.paragraphs.find(p => p.index === paraIndex);
  if (!para) return;

  const corrEl = document.querySelector(`#corrected-column .doc-para[data-para-index="${paraIndex}"]`);
  if (corrEl) corrEl.innerHTML = buildCorrectedHtml(para);

  // Re-sync height for this pair
  const origEl = document.querySelector(`#original-column .doc-para[data-para-index="${paraIndex}"]`);
  if (origEl && corrEl) {
    origEl.style.minHeight = "";
    corrEl.style.minHeight = "";
    const h = Math.max(origEl.offsetHeight, corrEl.offsetHeight);
    origEl.style.minHeight = h + "px";
    corrEl.style.minHeight = h + "px";
  }
}

// ---------------------------------------------------------------------------
// Hover tooltip
// ---------------------------------------------------------------------------

const _tooltip    = $("change-tooltip");
let   _ttHideTimer = null;

function showTooltip(anchor, paraIndex, ruleId) {
  const para   = reportData?.paragraphs.find(p => p.index === paraIndex);
  const change = para?.changes.find(c => c.rule_id === ruleId);
  if (!change) return;

  clearTimeout(_ttHideTimer);

  const acc = isAccepted(paraIndex, ruleId);

  _tooltip.innerHTML =
    `<div class="tt-desc">${escapeHtml(change.description)}</div>` +
    (change.find && change.replace
      ? `<div class="tt-rule">
           <span class="tt-del">${escapeHtml(change.find)}</span>
           <span class="tt-arr">→</span>
           <span class="tt-ins">${escapeHtml(change.replace)}</span>
         </div>`
      : "") +
    `<div class="tt-actions">
       <button class="tt-btn tt-reject${!acc ? " tt-active" : ""}"
               data-action="reject" data-para="${paraIndex}" data-rule="${escapeHtml(ruleId)}">
         &#10005; Reject
       </button>
       <button class="tt-btn tt-accept${acc ? " tt-active" : ""}"
               data-action="accept" data-para="${paraIndex}" data-rule="${escapeHtml(ruleId)}">
         &#10003; Accept
       </button>
     </div>`;

  _tooltip.hidden = false;

  // Position: just below the anchor, clamped to viewport
  const rect = anchor.getBoundingClientRect();
  const tw   = _tooltip.offsetWidth  || 240;
  const th   = _tooltip.offsetHeight || 110;

  let top  = rect.bottom + 8;
  let left = rect.left;

  // Flip upward if too close to bottom edge
  if (top + th > window.innerHeight - 16) top = rect.top - th - 8;
  if (left + tw > window.innerWidth  - 12) left = window.innerWidth - tw - 12;
  if (left < 8) left = 8;

  _tooltip.style.top  = top  + "px";
  _tooltip.style.left = left + "px";
}

function hideTooltip() { _tooltip.hidden = true; }

// Show on hover over any .change-mark
document.addEventListener("mouseover", e => {
  const mark = e.target.closest?.(".change-mark");
  if (mark) {
    clearTimeout(_ttHideTimer);
    showTooltip(mark, +mark.dataset.paraIndex, mark.dataset.ruleId);
  }
});

// Hide when leaving the mark (unless entering the tooltip itself)
document.addEventListener("mouseout", e => {
  if (e.target.closest?.(".change-mark") && !e.relatedTarget?.closest?.("#change-tooltip")) {
    _ttHideTimer = setTimeout(hideTooltip, 240);
  }
});

// Keep visible while hovering over tooltip
_tooltip.addEventListener("mouseenter", () => clearTimeout(_ttHideTimer));
_tooltip.addEventListener("mouseleave", () => { _ttHideTimer = setTimeout(hideTooltip, 240); });

// Accept / Reject buttons inside tooltip
_tooltip.addEventListener("click", e => {
  const btn = e.target.closest(".tt-btn");
  if (!btn) return;
  const paraIndex = +btn.dataset.para;
  const ruleId    = btn.dataset.rule;
  accepted.set(acceptedKey(paraIndex, ruleId), btn.dataset.action === "accept");
  refreshDocPara(paraIndex);
  hideTooltip();
});

// ---------------------------------------------------------------------------
// Accept all
// ---------------------------------------------------------------------------

$("accept-all-btn").addEventListener("click", () => {
  if (!reportData) return;
  for (const para of reportData.paragraphs) {
    for (const change of para.changes) {
      accepted.set(acceptedKey(para.index, change.rule_id), true);
    }
  }
  renderDocColumns(reportData.paragraphs);
});

// ---------------------------------------------------------------------------
// Full-document violations section
// ---------------------------------------------------------------------------

function renderViolationsSection(paragraphs) {
  // Aggregate violations by rule_id across all paragraphs
  const byRule = new Map();
  for (const para of paragraphs) {
    for (const v of para.violations) {
      if (!byRule.has(v.rule_id)) {
        byRule.set(v.rule_id, { description: v.description, detail: v.detail, count: 0 });
      }
      byRule.get(v.rule_id).count++;
    }
  }

  const wrap = $("full-doc-issues-wrap");
  const list = $("full-doc-issues");
  list.innerHTML = "";

  if (byRule.size === 0) { wrap.hidden = true; return; }

  wrap.hidden = false;
  for (const [, info] of byRule) {
    const item = el("div", "issue-item");
    item.innerHTML =
      `<span class="issue-icon">&#9888;</span>` +
      `<div class="issue-body">
         <strong>${escapeHtml(info.description)}</strong>
         <span class="issue-detail">${escapeHtml(info.detail)}</span>
         ${info.count > 1
           ? `<span class="issue-count">${info.count} paragraphs affected</span>`
           : ""}
       </div>`;
    list.appendChild(item);
  }
}

// ---------------------------------------------------------------------------
// Show / hide unchanged paragraphs
// ---------------------------------------------------------------------------

$("show-all-toggle").addEventListener("change", applyUnchangedVisibility);

function applyUnchangedVisibility() {
  const show = $("show-all-toggle").checked;
  document.querySelectorAll(".doc-para.unchanged").forEach(e => { e.hidden = !show; });
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
  hideTooltip();
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
      const d = await res.json().catch(() => ({}));
      throw new Error(d.error ?? "Download failed");
    }
    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") ?? "";
    const match = disposition.match(/filename[^;=\n]*=["']?([^"';\n]+)/i);
    const filename = match ? match[1] : "corrected.docx";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "&#8681; Download";
  }
});

// ---------------------------------------------------------------------------
// Helpers
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

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function showLoading(on) { $("loading-overlay").hidden = !on; }

function showError(msg) {
  const b = $("upload-error");
  if (msg) { b.textContent = msg; b.hidden = false; }
  else     { b.hidden = true; }
}
