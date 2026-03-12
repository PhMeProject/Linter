"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let sessionId  = null;
let reportData = null;

// "paraIndex::ruleId" → true (accepted) | false (rejected) | not set (pending)
const accepted = new Map();

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
  accepted.clear(); // everything starts as pending

  renderSummary(data);
  renderDocument(data.paragraphs);

  $("upload-section").hidden = true;
  $("review-section").hidden = false;
}

function acceptedKey(paraIndex, ruleId) {
  return `${paraIndex}::${ruleId}`;
}

function changeState(paraIndex, ruleId) {
  const key = acceptedKey(paraIndex, ruleId);
  if (!accepted.has(key)) return "pending";
  return accepted.get(key) ? "accepted" : "rejected";
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
// Document rendering
// ---------------------------------------------------------------------------

function renderDocument(paragraphs) {
  const col = $("document-column");
  col.innerHTML = "";
  for (const para of paragraphs) {
    col.appendChild(buildDocPara(para));
  }
  applyUnchangedVisibility();
}

function buildDocPara(para) {
  const div = document.createElement("div");
  div.className = "doc-para";
  div.dataset.paraIndex = para.index;

  applyDocStyle(div, para);

  if (para.original.trim() === "") {
    div.innerHTML = "\u00a0";
    div.classList.add("empty-para");
  } else {
    div.innerHTML = buildParaHtml(para);
  }

  if (!para.has_changes && !para.has_violations) {
    div.classList.add("unchanged");
  }

  return div;
}

function applyDocStyle(div, para) {
  const s = para.style_name || "Normal";
  if      (/^Title$/i.test(s))            div.classList.add("doc-style-title");
  else if (/^Heading\s*1$/i.test(s))      div.classList.add("doc-style-h1");
  else if (/^Heading\s*2$/i.test(s))      div.classList.add("doc-style-h2");
  else if (/^Heading\s*3$/i.test(s))      div.classList.add("doc-style-h3");
  else if (/^Heading\s*[4-9]$/i.test(s))  div.classList.add("doc-style-h4");
  else                                     div.classList.add("doc-style-body");

  const isHeading = /^(Title|Heading)/i.test(s);
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

// ---------------------------------------------------------------------------
// Build paragraph HTML — inline marks for changes + violations
// ---------------------------------------------------------------------------

// Split text into segments, each carrying a type and optional ruleId.
// Segments marked with a ruleId won't be re-processed by later rules.
function applyChangeToSegments(segments, change, paraIndex) {
  const state = changeState(paraIndex, change.rule_id);

  const escaped = escapeRegex(change.find);
  const pattern = change.whole_word ? `\\b${escaped}\\b` : escaped;
  const flags   = change.case_sensitive ? "g" : "gi";
  const re      = new RegExp(pattern, flags);

  const out = [];
  for (const seg of segments) {
    if (seg.type !== "plain") { out.push(seg); continue; }

    let last = 0;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(seg.text)) !== null) {
      if (m.index > last) out.push({ text: seg.text.slice(last, m.index), type: "plain" });

      if (state === "rejected") {
        // Keep original word, no mark
        out.push({ text: m[0], type: "plain" });
      } else if (state === "accepted") {
        // Show replacement with subtle accepted tint
        out.push({ text: change.replace, type: "accepted", ruleId: change.rule_id });
      } else {
        // Pending: show original word with blue wavy underline
        out.push({ text: m[0], type: "pending", ruleId: change.rule_id });
      }
      last = m.index + m[0].length;
    }
    if (last < seg.text.length) out.push({ text: seg.text.slice(last), type: "plain" });
  }
  return out.length ? out : segments;
}

function buildParaHtml(para) {
  // Step 1: build segments from changes
  let segments = [{ text: para.original, type: "plain" }];
  for (const change of para.changes) {
    if (!change.find) continue;
    segments = applyChangeToSegments(segments, change, para.index);
  }

  // Step 2: convert segments → HTML string
  let html = segments.map(seg => {
    const t = escapeHtml(seg.text);
    if (seg.type === "plain")    return t;
    const ri = escapeHtml(seg.ruleId);
    const pi = para.index;
    if (seg.type === "pending")  return `<span class="change-mark pending"  data-para-index="${pi}" data-rule-id="${ri}">${t}</span>`;
    if (seg.type === "accepted") return `<span class="change-mark accepted" data-para-index="${pi}" data-rule-id="${ri}">${t}</span>`;
    return t;
  }).join("");

  // Step 3: if this paragraph has violations, wrap content in a violation span
  // (amber wavy underline over the text, tooltip shows the violations)
  if (para.has_violations) {
    html = `<span class="violation-text" data-para-index="${para.index}">${html}</span>`;
  }

  return html;
}

// Refresh a single paragraph after a state change
function refreshPara(paraIndex) {
  const para = reportData.paragraphs.find(p => p.index === paraIndex);
  if (!para) return;
  const div = document.querySelector(`#document-column .doc-para[data-para-index="${paraIndex}"]`);
  if (div) div.innerHTML = buildParaHtml(para);
}

// ---------------------------------------------------------------------------
// Tooltip system
// ---------------------------------------------------------------------------

const _tooltip    = $("change-tooltip");
let   _ttHideTimer = null;

function showTooltip(anchor) {
  clearTimeout(_ttHideTimer);

  const isChange    = anchor.classList.contains("change-mark");
  const isViolation = anchor.classList.contains("violation-text");
  const paraIndex   = +anchor.dataset.paraIndex;
  const para        = reportData?.paragraphs.find(p => p.index === paraIndex);
  if (!para) return;

  let html = "";

  if (isChange) {
    const ruleId = anchor.dataset.ruleId;
    const change = para.changes.find(c => c.rule_id === ruleId);
    if (!change) return;
    const state  = changeState(paraIndex, ruleId);

    html += `<div class="tt-type">Suggested change</div>`;
    html += `<div class="tt-desc">${escapeHtml(change.description)}</div>`;
    if (change.find && change.replace) {
      html += `<div class="tt-rule">
        <span class="tt-del">${escapeHtml(change.find)}</span>
        <span class="tt-arr">→</span>
        <span class="tt-ins">${escapeHtml(change.replace)}</span>
      </div>`;
    }
    // Always show both buttons; highlight whichever state is active
    const isAcc = state === "accepted";
    html += `<div class="tt-actions">
      <button class="tt-btn tt-reject-btn"
              data-action="reject" data-para="${paraIndex}" data-rule="${escapeHtml(ruleId)}">
        Reject
      </button>
      <button class="tt-btn tt-accept-btn${isAcc ? " tt-active" : ""}"
              data-action="accept" data-para="${paraIndex}" data-rule="${escapeHtml(ruleId)}">
        &#10003; Accept
      </button>
    </div>`;

  } else if (isViolation) {
    html += `<div class="tt-type">Violation</div>`;
    for (const v of para.violations) {
      html += `<div class="tt-desc">${escapeHtml(v.description)}</div>`;
      html += `<div class="tt-detail">${escapeHtml(v.detail)}</div>`;
    }
  }

  if (!html) return;
  _tooltip.innerHTML = html;
  _tooltip.hidden = false;
  positionTooltip(anchor);
}

function positionTooltip(anchor) {
  const rect = anchor.getBoundingClientRect();
  const tw   = _tooltip.offsetWidth  || 240;
  const th   = _tooltip.offsetHeight || 110;

  let top  = rect.bottom + 8;
  let left = rect.left;

  if (top + th > window.innerHeight - 16) top = rect.top - th - 8;
  if (left + tw > window.innerWidth  - 12) left = window.innerWidth - tw - 12;
  if (left < 8) left = 8;

  _tooltip.style.top  = top  + "px";
  _tooltip.style.left = left + "px";
}

function hideTooltip() { _tooltip.hidden = true; }

// Show on hover
document.addEventListener("mouseover", e => {
  const t = e.target.closest?.(".change-mark, .violation-text");
  if (t) { clearTimeout(_ttHideTimer); showTooltip(t); }
});

// Hide when leaving (unless entering the tooltip itself)
document.addEventListener("mouseout", e => {
  const t = e.target.closest?.(".change-mark, .violation-text");
  if (t && !e.relatedTarget?.closest?.("#change-tooltip")) {
    _ttHideTimer = setTimeout(hideTooltip, 240);
  }
});

_tooltip.addEventListener("mouseenter", () => clearTimeout(_ttHideTimer));
_tooltip.addEventListener("mouseleave", () => { _ttHideTimer = setTimeout(hideTooltip, 240); });

// Tooltip button actions
_tooltip.addEventListener("click", e => {
  const btn = e.target.closest(".tt-btn");
  if (!btn) return;
  const paraIndex = +btn.dataset.para;
  const ruleId    = btn.dataset.rule;
  const action    = btn.dataset.action;

  if (action === "accept") {
    accepted.set(acceptedKey(paraIndex, ruleId), true);
  } else if (action === "reject") {
    accepted.set(acceptedKey(paraIndex, ruleId), false);
  }

  refreshPara(paraIndex);
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
  renderDocument(reportData.paragraphs);
});

// ---------------------------------------------------------------------------
// Show / hide unchanged paragraphs
// ---------------------------------------------------------------------------

$("show-all-toggle").addEventListener("change", applyUnchangedVisibility);

function applyUnchangedVisibility() {
  const show = $("show-all-toggle").checked;
  document.querySelectorAll(".doc-para.unchanged").forEach(e => { e.hidden = !show; });
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
    const disp = res.headers.get("Content-Disposition") ?? "";
    const match = disp.match(/filename[^;=\n]*=["']?([^"';\n]+)/i);
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
