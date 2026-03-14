"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let sessionId  = null;
let reportData = null;

// "paraIndex::ruleId" → true (accepted) | false (rejected) | not set (pending)
const accepted = new Map();

// paraIndex → {font_name?, font_size?, bold?, italic?, color_hex?}
// Stores accepted style-violation fixes so renderDocument can re-apply them.
const fixedParaStyles = new Map();

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
  accepted.clear();        // everything starts as pending
  fixedParaStyles.clear(); // no accepted style fixes yet

  renderSummary(data);
  renderDocument(data.paragraphs);
  renderSidebar(data);

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

  // Overlay any accepted style-violation fixes (applied after base styles
  // so they take precedence and survive a full renderDocument rebuild).
  const fix = fixedParaStyles.get(para.index);
  if (fix) {
    if (fix.font_name  !== undefined) div.style.fontFamily  = fix.font_name;
    if (fix.font_size  !== undefined) {
      const px = Math.round(fix.font_size * 1.25);
      if (px > 8 && px < 28) div.style.fontSize = `${px}px`;
    }
    if (fix.bold       !== undefined) div.style.fontWeight  = fix.bold ? "700" : "400";
    if (fix.italic     !== undefined) div.style.fontStyle   = fix.italic ? "italic" : "normal";
    if (fix.color_hex  !== undefined) div.style.color       = `#${fix.color_hex}`;
  }
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
        // Accepted: show replacement with green tint so the edit is visible
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

// Like applyChangeToSegments but for TextProhibitionRule violations —
// marks the prohibited term with type "violation" in plain segments only.
function applyViolationToSegments(segments, violation, paraIndex) {
  // If the violation has been accepted or dismissed, leave the text plain
  if (accepted.has(acceptedKey(paraIndex, violation.rule_id))) return segments;

  const escaped = escapeRegex(violation.find);
  const pattern = violation.whole_word ? `\\b${escaped}\\b` : escaped;
  const flags   = violation.case_sensitive ? "g" : "gi";
  const re      = new RegExp(pattern, flags);

  const out = [];
  for (const seg of segments) {
    if (seg.type !== "plain") { out.push(seg); continue; }

    let last = 0;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(seg.text)) !== null) {
      if (m.index > last) out.push({ text: seg.text.slice(last, m.index), type: "plain" });
      out.push({ text: m[0], type: "violation", ruleId: violation.rule_id });
      last = m.index + m[0].length;
    }
    if (last < seg.text.length) out.push({ text: seg.text.slice(last), type: "plain" });
  }
  return out.length ? out : segments;
}

function buildParaHtml(para) {
  // Step 1: build segments from changes (blue wavy)
  let segments = [{ text: para.original, type: "plain" }];
  for (const change of para.changes) {
    if (!change.find) continue;
    segments = applyChangeToSegments(segments, change, para.index);
  }

  // Step 2: overlay prohibition violations (amber wavy) on remaining plain text
  for (const vio of para.violations) {
    if (!vio.find) continue; // style violations have no specific word to mark
    segments = applyViolationToSegments(segments, vio, para.index);
  }

  // Step 3: convert segments → HTML string
  let html = segments.map(seg => {
    const t = escapeHtml(seg.text);
    const pi = para.index;
    if (seg.type === "plain")     return t;
    const ri = escapeHtml(seg.ruleId);
    if (seg.type === "pending")   return `<span class="change-mark pending"  data-para-index="${pi}" data-rule-id="${ri}">${t}</span>`;
    if (seg.type === "accepted")  return `<span class="change-mark accepted" data-para-index="${pi}" data-rule-id="${ri}">${t}</span>`;
    if (seg.type === "violation") return `<span class="vio-mark" data-para-index="${pi}" data-rule-id="${ri}">${t}</span>`;
    return t;
  }).join("");

  // Step 4: append a ⚠ badge for any unhandled style-level violations
  const hasStyleViolations = para.violations.some(
    v => !v.find && !accepted.has(acceptedKey(para.index, v.rule_id))
  );
  if (hasStyleViolations) {
    html += `<span class="vio-badge" data-para-index="${para.index}" title="">⚠</span>`;
  }

  return html;
}

// Flash a paragraph green briefly to signal an accepted change
function flashPara(paraIndex) {
  const div = document.querySelector(`#document-column .doc-para[data-para-index="${paraIndex}"]`);
  if (!div) return;
  div.classList.remove("accept-flash");
  // Force reflow so the animation restarts if called twice quickly
  void div.offsetWidth;
  div.classList.add("accept-flash");
  div.addEventListener("animationend", () => div.classList.remove("accept-flash"), { once: true });
}

// Refresh a single paragraph after a state change
function refreshPara(paraIndex) {
  const para = reportData.paragraphs.find(p => p.index === paraIndex);
  if (!para) return;
  const div = document.querySelector(`#document-column .doc-para[data-para-index="${paraIndex}"]`);
  if (div) div.innerHTML = buildParaHtml(para);
}

// Fully rebuild a paragraph element in place (replaces the DOM node).
// Used after accepting a style violation so applyDocStyle re-runs and
// picks up the fix from fixedParaStyles in one clean pass.
function refreshParaFull(paraIndex) {
  const para = reportData.paragraphs.find(p => p.index === paraIndex);
  if (!para) return;
  const old = document.querySelector(`#document-column .doc-para[data-para-index="${paraIndex}"]`);
  if (!old) return;
  old.replaceWith(buildDocPara(para));
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

let _activeCard = null;

function renderSidebar(data) {
  const list = $("sidebar-list");
  list.innerHTML = "";

  let changeCount = 0;
  let issueCount  = 0;

  for (const para of data.paragraphs) {
    // Change cards (one per unique rule per paragraph)
    const seenChanges = new Set();
    for (const change of para.changes) {
      if (!change.find || seenChanges.has(change.rule_id)) continue;
      seenChanges.add(change.rule_id);
      changeCount++;
      const card = document.createElement("div");
      card.className = "change-card";
      card.dataset.paraIndex = para.index;
      card.dataset.ruleId    = change.rule_id;
      card.dataset.cardType  = "change";
      const state = changeState(para.index, change.rule_id);
      if (state !== "pending") card.classList.add(state);
      card.innerHTML = buildChangeCardHTML(para.index, change);
      list.appendChild(card);
    }

    // Prohibition violation cards
    const seenVios = new Set();
    for (const vio of para.violations) {
      if (!vio.find || seenVios.has(vio.rule_id)) continue;
      seenVios.add(vio.rule_id);
      issueCount++;
      const card = document.createElement("div");
      card.className = "change-card";
      card.dataset.paraIndex = para.index;
      card.dataset.ruleId    = vio.rule_id;
      card.dataset.cardType  = "violation";
      const vioVal = accepted.get(acceptedKey(para.index, vio.rule_id));
      if (vioVal === "accepted") card.classList.add("accepted");
      if (vioVal === "dismissed") card.classList.add("rejected");
      card.innerHTML = buildVioCardHTML(vio, para.index);
      list.appendChild(card);
    }

    // Style violation cards (whole-paragraph issues)
    const seenStyle = new Set();
    for (const vio of para.violations) {
      if (vio.find || seenStyle.has(vio.rule_id)) continue;
      seenStyle.add(vio.rule_id);
      issueCount++;
      const card = document.createElement("div");
      card.className = "change-card";
      card.dataset.paraIndex = para.index;
      card.dataset.ruleId    = vio.rule_id;
      card.dataset.cardType  = "style";
      const styleVal = accepted.get(acceptedKey(para.index, vio.rule_id));
      if (styleVal === "accepted") card.classList.add("accepted");
      if (styleVal === "dismissed") card.classList.add("rejected");
      card.innerHTML = buildStyleVioCardHTML(vio, para.index);
      list.appendChild(card);
    }
  }

  // Update header with counts so it's clear what was detected
  const hdr = $("sidebar-header");
  if (hdr) {
    const parts = [];
    if (changeCount) parts.push(`${changeCount} change${changeCount !== 1 ? "s" : ""}`);
    if (issueCount)  parts.push(`${issueCount} issue${issueCount  !== 1 ? "s" : ""}`);
    hdr.textContent = parts.length ? `Suggestions — ${parts.join(", ")}` : "Suggestions";
  }

  if (list.children.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">&#10003; No issues found</div>';
  }
}

function buildChangeCardHTML(paraIndex, change) {
  const state = changeState(paraIndex, change.rule_id);
  // Rule IDs are alphanumeric+hyphens — safe to single-quote in onclick
  const pi = paraIndex;
  const ri = change.rule_id;

  let h = `<div class="card-header"><span class="card-badge card-badge-change">Suggested change</span></div>`;
  h += `<div class="card-desc">${escapeHtml(change.description)}</div>`;

  if (change.find) {
    const findHtml    = escapeHtml(change.find);
    const replaceHtml = escapeHtml(change.replace || "");
    const struck      = state !== "pending";
    const arrow       = state === "rejected" ? "&#10005;" : "&#8594;";
    h += `<div class="card-rule">
      <span class="tt-del${struck ? " tt-struck" : ""}">${findHtml}</span>
      <span class="tt-arr">${arrow}</span>
      <span class="tt-ins${state === "rejected" ? " tt-struck" : ""}">${replaceHtml}</span>
    </div>`;
  }

  if (state === "pending") {
    h += `<div class="card-actions">
      <button class="card-btn card-reject-btn" onclick="event.stopPropagation();rejectChange(${pi},'${ri}')">&#10005; Reject</button>
      <button class="card-btn card-accept-btn" onclick="event.stopPropagation();acceptChange(${pi},'${ri}')">&#10003; Accept</button>
    </div>`;
  } else if (state === "accepted") {
    h += `<div class="card-status card-accepted">&#10003; Accepted</div>`;
  } else {
    h += `<div class="card-status card-rejected">&#10005; Rejected</div>`;
  }
  return h;
}

function buildVioCardHTML(vio, paraIndex) {
  const pi  = paraIndex;
  const ri  = vio.rule_id;
  const val = accepted.get(acceptedKey(paraIndex, vio.rule_id));
  const state = val === "accepted" ? "accepted" : val === "dismissed" ? "dismissed" : "pending";
  let h = `<div class="card-header"><span class="card-badge card-badge-vio">Prohibited term</span></div>`;
  h += `<div class="card-desc${state !== "pending" ? " tt-struck" : ""}">${escapeHtml(vio.description)}</div>`;
  h += `<div class="card-detail">${escapeHtml(vio.detail)}</div>`;
  if (state === "pending") {
    h += `<div class="card-actions">
      <button class="card-btn card-reject-btn" onclick="event.stopPropagation();dismissViolation(${pi},'${ri}')">&#10005; Dismiss</button>
      <button class="card-btn card-accept-btn" onclick="event.stopPropagation();acceptViolation(${pi},'${ri}')">&#10003; Accept</button>
    </div>`;
  } else if (state === "accepted") {
    h += `<div class="card-status card-accepted">&#10003; Accepted</div>`;
  } else {
    h += `<div class="card-status card-rejected">&#10005; Dismissed</div>`;
  }
  return h;
}

function buildStyleVioCardHTML(vio, paraIndex) {
  const pi  = paraIndex;
  const ri  = vio.rule_id;
  const val = accepted.get(acceptedKey(paraIndex, vio.rule_id));
  const state = val === "accepted" ? "accepted" : val === "dismissed" ? "dismissed" : "pending";
  let h = `<div class="card-header"><span class="card-badge card-badge-style">Style issue</span></div>`;
  h += `<div class="card-desc${state !== "pending" ? " tt-struck" : ""}">${escapeHtml(vio.description)}</div>`;
  h += `<div class="card-detail">${escapeHtml(vio.detail)}</div>`;
  if (state === "pending") {
    h += `<div class="card-actions">
      <button class="card-btn card-reject-btn" onclick="event.stopPropagation();dismissViolation(${pi},'${ri}')">&#10005; Dismiss</button>
      <button class="card-btn card-accept-btn" onclick="event.stopPropagation();acceptViolation(${pi},'${ri}')">&#10003; Accept</button>
    </div>`;
  } else if (state === "accepted") {
    h += `<div class="card-status card-accepted">&#10003; Accepted</div>`;
  } else {
    h += `<div class="card-status card-rejected">&#10005; Dismissed</div>`;
  }
  return h;
}

function refreshCard(paraIndex, ruleId) {
  const para   = reportData.paragraphs.find(p => p.index === paraIndex);
  const change = para?.changes.find(c => c.rule_id === ruleId);
  if (!para || !change) return;

  let card = null;
  $("sidebar-list").querySelectorAll(`.change-card[data-para-index="${paraIndex}"]`)
    .forEach(c => { if (c.dataset.ruleId === ruleId) card = c; });
  if (!card) return;

  const state = changeState(paraIndex, ruleId);
  card.classList.remove("accepted", "rejected");
  if (state !== "pending") card.classList.add(state);
  card.innerHTML = buildChangeCardHTML(paraIndex, change);
}

function activateCard(paraIndex, ruleId, cardType) {
  // Clear previous active state
  if (_activeCard) _activeCard.classList.remove("active");
  document.querySelectorAll(".change-mark.active-change, .vio-mark.active-change")
    .forEach(s => s.classList.remove("active-change"));
  document.querySelectorAll(".doc-para.para-highlight")
    .forEach(s => s.classList.remove("para-highlight"));

  // Activate the clicked card
  let card = null;
  $("sidebar-list").querySelectorAll(`.change-card[data-para-index="${paraIndex}"]`)
    .forEach(c => { if (c.dataset.ruleId === ruleId) card = c; });
  if (card) { card.classList.add("active"); _activeCard = card; }

  // Highlight in document
  const docPara = document.querySelector(`#document-column .doc-para[data-para-index="${paraIndex}"]`);
  if (!docPara) return;

  if (cardType === "change") {
    docPara.querySelectorAll(".change-mark")
      .forEach(s => { if (s.dataset.ruleId === ruleId) s.classList.add("active-change"); });
  } else if (cardType === "violation") {
    docPara.querySelectorAll(".vio-mark")
      .forEach(s => { if (s.dataset.ruleId === ruleId) s.classList.add("active-change"); });
  } else {
    docPara.classList.add("para-highlight");
  }

  // Scroll document panel to the paragraph
  docPara.scrollIntoView({ behavior: "smooth", block: "center" });
}

// Sidebar click handler — card activation only (buttons use onclick globals below)
$("sidebar-list").addEventListener("click", e => {
  if (e.target.closest(".card-btn")) return; // let onclick handle button clicks
  const card = e.target.closest(".change-card");
  if (!card) return;
  const paraIndex = +card.dataset.paraIndex;
  const ruleId    = card.dataset.ruleId;
  const cardType  = card.dataset.cardType;
  activateCard(paraIndex, ruleId, cardType);
});

// Global accept / reject — called directly from onclick attributes in cards
window.acceptChange = function(paraIndex, ruleId) {
  accepted.set(acceptedKey(paraIndex, ruleId), true);
  refreshPara(paraIndex);
  refreshCard(paraIndex, ruleId);
  document.querySelectorAll(".change-mark.active-change, .vio-mark.active-change")
    .forEach(s => s.classList.remove("active-change"));
  flashPara(paraIndex);
};

window.rejectChange = function(paraIndex, ruleId) {
  accepted.set(acceptedKey(paraIndex, ruleId), false);
  refreshPara(paraIndex);
  refreshCard(paraIndex, ruleId);
  document.querySelectorAll(".change-mark.active-change")
    .forEach(s => s.classList.remove("active-change"));
};

function refreshVioCard(paraIndex, ruleId) {
  let card = null;
  $("sidebar-list").querySelectorAll(`.change-card[data-para-index="${paraIndex}"]`)
    .forEach(c => { if (c.dataset.ruleId === ruleId) card = c; });
  if (!card) return;
  const para = reportData.paragraphs.find(p => p.index === paraIndex);
  if (!para) return;
  const vio = para.violations.find(v => v.rule_id === ruleId);
  if (!vio) return;
  const val = accepted.get(acceptedKey(paraIndex, ruleId));
  card.classList.remove("accepted", "rejected");
  if (val === "accepted") card.classList.add("accepted");
  if (val === "dismissed") card.classList.add("rejected");
  if (card.dataset.cardType === "violation") {
    card.innerHTML = buildVioCardHTML(vio, paraIndex);
  } else {
    card.innerHTML = buildStyleVioCardHTML(vio, paraIndex);
  }
}

window.acceptViolation = function(paraIndex, ruleId) {
  accepted.set(acceptedKey(paraIndex, ruleId), "accepted");
  // Store any style fix so applyDocStyle picks it up during the full rebuild
  const para = reportData.paragraphs.find(p => p.index === paraIndex);
  const vio  = para?.violations.find(v => v.rule_id === ruleId);
  if (vio?.fix) {
    fixedParaStyles.set(paraIndex,
      Object.assign(fixedParaStyles.get(paraIndex) || {}, vio.fix));
  }
  // Full element rebuild: applyDocStyle runs fresh and applies fixedParaStyles
  refreshParaFull(paraIndex);
  refreshVioCard(paraIndex, ruleId);
  flashPara(paraIndex);
};

window.dismissViolation = function(paraIndex, ruleId) {
  accepted.set(acceptedKey(paraIndex, ruleId), "dismissed");
  refreshPara(paraIndex);
  refreshVioCard(paraIndex, ruleId);
};

// ---------------------------------------------------------------------------
// Accept all
// ---------------------------------------------------------------------------

$("accept-all-btn").addEventListener("click", () => {
  if (!reportData) return;
  const changedParas = new Set();
  for (const para of reportData.paragraphs) {
    for (const change of para.changes) {
      accepted.set(acceptedKey(para.index, change.rule_id), true);
      changedParas.add(para.index);
    }
    for (const vio of para.violations) {
      accepted.set(acceptedKey(para.index, vio.rule_id), "accepted");
      changedParas.add(para.index);
      // Stage any style fix so applyDocStyle applies it during renderDocument
      if (vio.fix) {
        fixedParaStyles.set(para.index,
          Object.assign(fixedParaStyles.get(para.index) || {}, vio.fix));
      }
    }
  }
  renderDocument(reportData.paragraphs);
  renderSidebar(reportData);
  _activeCard = null;
  changedParas.forEach(idx => flashPara(idx));
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
  _activeCard = null;
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
