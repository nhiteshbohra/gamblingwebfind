// app.js — Frontend Controller for GamblingWebFind Dashboard
const API = "";
let _activeTab = "overview";
let _domainsPage = 1;
let _currentFilter = "all";
let _activeFilterParams = {};
let _domainsQ = "";
let _domainsPerPage = 50;
let _currentDomainData = null;
let _jobEventSources = {};
let _searchDebounceTimer = null;
let _confirmResolve = null;
let _runningStages = new Set();

// ── Toast Notifications ──────────────────────────────────────────────────────
function toast(msg, duration = 3500) {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), duration);
}

// ── Navigation ───────────────────────────────────────────────────────────────
function showTab(name) {
  _activeTab = name;
  document.querySelectorAll("nav button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-view").forEach(view => {
    view.classList.toggle("hidden", view.id !== `tab-${name}`);
  });

  if (name === "overview") loadOverview();
  if (name === "domains") loadDomains(_domainsPage);
}

function focusStage(stageId) {
  const el = document.getElementById(`card-stage-${stageId}`);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
}

// ── Filtering & Jump to Domains ──────────────────────────────────────────────
function jumpToDomains(filterObj = {}) {
  _domainsPage = 1;
  _domainsQ = "";
  const sInput = document.getElementById("domain-search");
  if (sInput) sInput.value = "";
  const sClear = document.getElementById("search-clear-btn");
  if (sClear) sClear.classList.add("hidden");

  if (filterObj.screenshot === "pending") {
    setFilterChip("ss_pending");
  } else if (filterObj.screenshot === "taken") {
    setFilterChip("ss_taken");
  } else if (filterObj.exported === "true") {
    setFilterChip("exported");
  } else if (filterObj.exported === "false") {
    setFilterChip("not_exported");
  } else if (filterObj.status) {
    setFilterChip(filterObj.status);
  } else {
    setFilterChip("all");
  }

  showTab("domains");
}

function setFilterChip(filterKey) {
  _currentFilter = filterKey;
  _domainsPage = 1;

  // Clear specific dropdown overrides to stay synced
  _activeFilterParams = {};
  if (filterKey === "gambling") {
    _activeFilterParams.status = "gambling";
  } else if (filterKey === "regular") {
    _activeFilterParams.status = "regular";
  } else if (filterKey === "blocked") {
    _activeFilterParams.status = "blocked";
  } else if (filterKey === "dead") {
    _activeFilterParams.status = "dead";
  } else if (filterKey === "unconfirmed") {
    _activeFilterParams.status = "unconfirmed";
  } else if (filterKey === "ss_pending") {
    _activeFilterParams.status = "gambling";
    _activeFilterParams.screenshot = "pending";
  } else if (filterKey === "ss_taken") {
    _activeFilterParams.status = "gambling";
    _activeFilterParams.screenshot = "taken";
  } else if (filterKey === "exported") {
    _activeFilterParams.exported = "true";
  } else if (filterKey === "not_exported") {
    _activeFilterParams.exported = "false";
  }

  // Sync dropdown selectors with chip
  syncDropdownsFromActiveFilter();

  // Update chip active classes
  document.querySelectorAll(".chip-btn").forEach(chip => {
    chip.classList.toggle("active", chip.dataset.filter === filterKey);
  });

  updateFilterSummary();
  if (_activeTab === "domains") {
    loadDomains(1);
  }
}

function syncDropdownsFromActiveFilter() {
  const statusSel = document.getElementById("filter-status-select");
  const ssSel = document.getElementById("filter-screenshot-select");
  const expSel = document.getElementById("filter-export-select");

  if (statusSel) statusSel.value = _activeFilterParams.status || "all";
  if (ssSel) ssSel.value = _activeFilterParams.screenshot || "all";
  if (expSel) expSel.value = _activeFilterParams.exported || "all";
}

function onDropdownFilterChange(filterType, val) {
  _domainsPage = 1;
  if (val === "all" || !val) {
    delete _activeFilterParams[filterType];
  } else {
    _activeFilterParams[filterType] = val;
  }

  // De-activate chips if dropdowns diverge
  document.querySelectorAll(".chip-btn").forEach(chip => chip.classList.remove("active"));

  updateFilterSummary();
  loadDomains(1);
}

function onDateFilterChange() {
  _domainsPage = 1;
  const fromVal = document.getElementById("filter-from-date").value;
  const toVal = document.getElementById("filter-to-date").value;

  if (fromVal) _activeFilterParams.from_date = fromVal;
  else delete _activeFilterParams.from_date;

  if (toVal) _activeFilterParams.to_date = toVal;
  else delete _activeFilterParams.to_date;

  updateFilterSummary();
  loadDomains(1);
}

function onSearchInput(val) {
  _domainsQ = val.trim();
  const clearBtn = document.getElementById("search-clear-btn");
  if (clearBtn) clearBtn.classList.toggle("hidden", !_domainsQ);

  clearTimeout(_searchDebounceTimer);
  _searchDebounceTimer = setTimeout(() => {
    updateFilterSummary();
    loadDomains(1);
  }, 300);
}

function clearSearchInput() {
  _domainsQ = "";
  const sInput = document.getElementById("domain-search");
  if (sInput) sInput.value = "";
  const clearBtn = document.getElementById("search-clear-btn");
  if (clearBtn) clearBtn.classList.add("hidden");

  updateFilterSummary();
  loadDomains(1);
}

function resetAllFilters() {
  _domainsQ = "";
  _activeFilterParams = {};
  _currentFilter = "all";
  _domainsPage = 1;

  const sInput = document.getElementById("domain-search");
  if (sInput) sInput.value = "";
  const clearBtn = document.getElementById("search-clear-btn");
  if (clearBtn) clearBtn.classList.add("hidden");

  const statusSel = document.getElementById("filter-status-select");
  const ssSel = document.getElementById("filter-screenshot-select");
  const expSel = document.getElementById("filter-export-select");
  const fromD = document.getElementById("filter-from-date");
  const toD = document.getElementById("filter-to-date");

  if (statusSel) statusSel.value = "all";
  if (ssSel) ssSel.value = "all";
  if (expSel) expSel.value = "all";
  if (fromD) fromD.value = "";
  if (toD) toD.value = "";

  document.querySelectorAll(".chip-btn").forEach(chip => {
    chip.classList.toggle("active", chip.dataset.filter === "all");
  });

  updateFilterSummary();
  loadDomains(1);
  toast("All search filters reset.");
}

function updateFilterSummary() {
  const summaryEl = document.getElementById("filter-tags-summary");
  if (!summaryEl) return;

  const tags = [];
  if (_domainsQ) tags.push(`Query: "${_domainsQ}"`);
  if (_activeFilterParams.status) tags.push(`Status: ${_activeFilterParams.status}`);
  if (_activeFilterParams.screenshot) tags.push(`Screenshot: ${_activeFilterParams.screenshot}`);
  if (_activeFilterParams.exported) tags.push(`Exported: ${_activeFilterParams.exported}`);
  if (_activeFilterParams.from_date) tags.push(`From: ${_activeFilterParams.from_date}`);
  if (_activeFilterParams.to_date) tags.push(`To: ${_activeFilterParams.to_date}`);

  if (tags.length === 0) {
    summaryEl.innerHTML = `<span class="muted" style="font-size:12px">Filters: None active (Showing all records)</span>`;
  } else {
    summaryEl.innerHTML = `
      <span style="font-size:12px;font-weight:700;color:var(--cyan)">Active Filters:</span>
      ${tags.map(t => `<span class="filter-tag-pill">${t}</span>`).join(" ")}
    `;
  }
}

// ── Number Formatter Helper ──────────────────────────────────────────────────
function fmtNum(n) {
  if (n === null || n === undefined || isNaN(n)) return "0";
  return Number(n).toLocaleString();
}

// ── Confirmation Modal ───────────────────────────────────────────────────────
function showConfirm(title, message) {
  return new Promise((resolve) => {
    _confirmResolve = resolve;
    document.getElementById("confirm-modal-title").textContent = title;
    document.getElementById("confirm-modal-desc").textContent = message;
    document.getElementById("confirm-modal").classList.remove("hidden");
  });
}

function closeConfirmModal(result) {
  document.getElementById("confirm-modal").classList.add("hidden");
  if (_confirmResolve) {
    _confirmResolve(result);
    _confirmResolve = null;
  }
}

// ── 1. Overview & Stats Loader ───────────────────────────────────────────────
async function loadOverview() {
  const errBanner = document.getElementById("global-error-banner");
  if (errBanner) errBanner.classList.add("hidden");

  try {
    const statsRes = await fetch(`${API}/api/stats`);
    if (!statsRes.ok) throw new Error(`API returned HTTP ${statsRes.status}`);
    const data = await statsRes.json();

    const src = data.source_domains || {};
    const chk = data.checked_domains || {};
    const rep = data.reports || {};

    // Header Status Pill
    const mDot = document.getElementById("hdr-mongo-dot");
    const mTxt = document.getElementById("hdr-mongo-text");
    if (mDot && mTxt) {
      mDot.className = "status-dot";
      mTxt.textContent = `Mongo: ${fmtNum(chk.total_checked || src.total_listed)}`;
    }

    // Row A: Source Pipeline Stats
    document.getElementById("st-src-total").textContent = fmtNum(src.total_listed);
    document.getElementById("st-src-pending").textContent = fmtNum(src.pending);
    document.getElementById("st-src-active").textContent = fmtNum(src.active);
    document.getElementById("st-src-inactive").textContent = fmtNum(src.inactive);
    document.getElementById("st-src-blocked").textContent = fmtNum(src.blocked_source);

    // Row B: Classification Status
    const g = chk.gambling || 0;
    const r = chk.regular || 0;
    const b = chk.blocked || 0;
    const d = chk.dead || 0;
    const u = chk.unconfirmed || 0;
    const totalChk = chk.total_checked || 1;

    document.getElementById("st-gambling").textContent = fmtNum(g);
    document.getElementById("st-regular").textContent = fmtNum(r);
    document.getElementById("st-blocked").textContent = fmtNum(b);
    document.getElementById("st-dead").textContent = fmtNum(d);
    document.getElementById("st-unconfirmed").textContent = fmtNum(u);
    document.getElementById("st-rate").textContent = `${chk.gambling_rate || 0}%`;

    // Row C: Compliance & Screenshots
    document.getElementById("st-ss-taken").textContent = fmtNum(chk.screenshot_taken);
    document.getElementById("st-ss-pending").textContent = fmtNum(chk.screenshot_pending);
    document.getElementById("st-exp-done").textContent = fmtNum(chk.exported);
    document.getElementById("st-exp-pending").textContent = fmtNum(chk.pending_export);

    // Filter Chips Counts (on Domains tab)
    const setChip = (id, count) => {
      const el = document.getElementById(id);
      if (el) el.textContent = fmtNum(count);
    };
    setChip("chip-cnt-all", totalChk);
    setChip("chip-cnt-gambling", g);
    setChip("chip-cnt-regular", r);
    setChip("chip-cnt-blocked", b);
    setChip("chip-cnt-dead", d);
    setChip("chip-cnt-unconfirmed", u);
    setChip("chip-cnt-ss-pending", chk.screenshot_pending);
    setChip("chip-cnt-ss-taken", chk.screenshot_taken);
    setChip("chip-cnt-exported", chk.exported);
    setChip("chip-cnt-not-exported", Math.max(0, totalChk - (chk.exported || 0)));

    // Deep Metrics — Throughput & AI
    document.getElementById("m-src-processed").textContent = fmtNum(src.processed);
    document.getElementById("m-src-today").textContent = fmtNum(src.added_today);
    document.getElementById("m-src-week").textContent = fmtNum(src.added_this_week);

    document.getElementById("m-rate-text").textContent = `${chk.gambling_rate || 0}%`;
    const rateBar = document.getElementById("m-rate-bar");
    if (rateBar) rateBar.style.width = `${Math.min(100, (chk.gambling_rate || 0) * 4)}%`;
    document.getElementById("m-ai-count").textContent = fmtNum(chk.ai_classified);
    document.getElementById("m-kw-count").textContent = fmtNum(chk.keyword_classified);

    // Deep Metrics — Reports
    document.getElementById("m-rep-runs").textContent = fmtNum(rep.runs);
    document.getElementById("m-rep-pdf").textContent = `${fmtNum(rep.pdf_files)} PDF`;
    document.getElementById("m-rep-xlsx").textContent = `${fmtNum(rep.xlsx_files)} Excel`;
    document.getElementById("m-rep-docx").textContent = `${fmtNum(rep.docx_files)} Word`;
    document.getElementById("m-rep-last").textContent = rep.last_export_timestamp || "Never";

    // Recent 10 Activity Feed
    const recentRes = await fetch(`${API}/api/domains?per_page=10`);
    if (recentRes.ok) {
      const recData = await recentRes.json();
      const tbody = document.getElementById("recent-body");
      if (recData.results && recData.results.length > 0) {
        tbody.innerHTML = recData.results.map(renderDomainRow).join("");
      } else {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:24px" class="muted">No recent domains recorded in database.</td></tr>`;
      }
    }
  } catch (err) {
    console.error("Overview error:", err);
    if (errBanner) {
      document.getElementById("global-error-msg").textContent = `Error connecting to backend services: ${err.message}`;
      errBanner.classList.remove("hidden");
    }
    toast(`[!] Connection error: ${err.message}`);
  }
}

// ── 2. Domains Table & Pagination ────────────────────────────────────────────
async function loadDomains(page = 1) {
  if (page < 1) page = 1;
  _domainsPage = page;

  const tbody = document.getElementById("domains-body");
  tbody.innerHTML = `
    <tr><td colspan="6" style="text-align:center;padding:32px">
      <div style="display:inline-flex;align-items:center;gap:8px;color:var(--text-muted)">
        <span class="skeleton" style="width:20px;height:20px;border-radius:50%"></span>
        <span>Loading domain records from MongoDB...</span>
      </div>
    </td></tr>
  `;

  try {
    let url = `${API}/api/domains?page=${_domainsPage}&per_page=${_domainsPerPage}`;
    for (const [k, v] of Object.entries(_activeFilterParams)) {
      if (v) url += `&${encodeURIComponent(k)}=${encodeURIComponent(v)}`;
    }
    if (_domainsQ) url += `&q=${encodeURIComponent(_domainsQ)}`;

    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status} loading domains`);
    const data = await res.json();

    const results = data.results || [];
    const total = data.total || 0;
    const maxPage = Math.max(1, Math.ceil(total / _domainsPerPage));

    // Update Pagination Info
    const countInfo = document.getElementById("domains-count-info");
    const startItem = (page - 1) * _domainsPerPage + (results.length > 0 ? 1 : 0);
    const endItem = Math.min(total, page * _domainsPerPage);
    countInfo.textContent = `Showing ${fmtNum(startItem)} – ${fmtNum(endItem)} of ${fmtNum(total)} domains (Page ${page} of ${maxPage})`;

    document.getElementById("btn-prev").disabled = page <= 1;
    document.getElementById("btn-next").disabled = page >= maxPage;

    if (results.length === 0) {
      tbody.innerHTML = `
        <tr><td colspan="6">
          <div class="empty-state">
            <div class="empty-state-icon">🔍</div>
            <div class="empty-state-title">No matching domains found</div>
            <div class="empty-state-desc">Try modifying search query, adjusting date filters, or selecting a different status.</div>
            <button class="btn btn-ghost btn-sm" onclick="resetAllFilters()">Reset All Filters</button>
          </div>
        </td></tr>
      `;
      return;
    }

    tbody.innerHTML = results.map(renderDomainFullRow).join("");
  } catch (err) {
    tbody.innerHTML = `
      <tr><td colspan="6" style="text-align:center;padding:32px;color:var(--danger)">
        <div style="font-weight:700;margin-bottom:6px">Failed to load domains</div>
        <div style="font-size:12px">${err.message}</div>
      </td></tr>
    `;
  }
}

function renderDomainRow(d) {
  const shotImg = d.has_screenshot_file
    ? `<img src="${API}/api/domains/${encodeURIComponent(d.domain)}/screenshot" class="thumb-preview" alt="Preview" onclick="event.stopPropagation();openDetail('${d.domain}')">`
    : `<div class="thumb-placeholder">—</div>`;

  return `
    <tr class="clickable-row" onclick="openDetail('${d.domain}')">
      <td>${shotImg}</td>
      <td><span style="font-weight:700;color:#fff">${d.domain}</span></td>
      <td><span class="badge badge-${d.status || 'dead'}">${d.status || 'unknown'}</span></td>
      <td class="muted" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${d.reason || '—'}</td>
      <td class="mono muted" style="font-size:12px">${d.added_date || '—'}</td>
    </tr>
  `;
}

function renderDomainFullRow(d) {
  const shotImg = d.has_screenshot_file
    ? `<img src="${API}/api/domains/${encodeURIComponent(d.domain)}/screenshot" class="thumb-preview" alt="Preview" onclick="event.stopPropagation();openDetail('${d.domain}')">`
    : `<div class="thumb-placeholder">—</div>`;

  const expBadge = d.exported
    ? `<span class="badge" style="background:var(--success-bg);color:#6ee7b7">Exported</span>`
    : `<span class="badge badge-idle">No</span>`;

  return `
    <tr class="clickable-row" onclick="openDetail('${d.domain}')">
      <td>${shotImg}</td>
      <td>
        <span style="font-weight:700;color:#fff">${d.domain}</span>
        <a href="${d.url || 'https://' + d.domain}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()" style="margin-left:6px;color:var(--accent-light);font-size:11px">↗</a>
      </td>
      <td><span class="badge badge-${d.status || 'dead'}">${d.status || 'unknown'}</span></td>
      <td class="muted" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${d.reason || '—'}</td>
      <td class="mono muted" style="font-size:12px">${d.added_date || '—'}</td>
      <td>${expBadge}</td>
    </tr>
  `;
}

// ── 3. Detail Drawer ─────────────────────────────────────────────────────────
async function openDetail(domain) {
  const overlay = document.getElementById("detail-overlay");
  const title = document.getElementById("detail-domain-title");
  const badgeArea = document.getElementById("detail-badge-area");
  const jsonPre = document.getElementById("detail-json");
  const imgArea = document.getElementById("detail-screenshot-area");
  const img = document.getElementById("detail-img");
  const link = document.getElementById("detail-visit-link");

  if (!overlay) return;

  title.textContent = domain;
  jsonPre.textContent = "Loading full record from MongoDB...";
  badgeArea.innerHTML = "";
  imgArea.classList.add("hidden");
  overlay.classList.remove("hidden");

  try {
    const res = await fetch(`${API}/api/domains/${encodeURIComponent(domain)}`);
    if (!res.ok) throw new Error("Domain record not found");
    const data = await res.json();
    _currentDomainData = data;

    badgeArea.innerHTML = `
      <span class="badge badge-${data.status || 'dead'}" style="font-size:12px;padding:4px 12px">${data.status || 'unknown'}</span>
      ${data.exported ? `<span class="badge" style="background:var(--success-bg);color:#6ee7b7;font-size:12px;padding:4px 12px">Exported</span>` : ''}
      ${data.screenshot_taken ? `<span class="badge" style="background:var(--cyan-glow);color:var(--cyan);font-size:12px;padding:4px 12px">📸 Screenshot Captured</span>` : ''}
    `;

    jsonPre.textContent = JSON.stringify(data, null, 2);
    link.href = data.url || `https://${domain}`;

    // Screenshot image check
    const testImg = new Image();
    testImg.onload = () => {
      img.src = `${API}/api/domains/${encodeURIComponent(domain)}/screenshot`;
      imgArea.classList.remove("hidden");
    };
    testImg.onerror = () => {
      imgArea.classList.add("hidden");
    };
    testImg.src = `${API}/api/domains/${encodeURIComponent(domain)}/screenshot`;

  } catch (err) {
    jsonPre.textContent = `Error loading record: ${err.message}`;
  }
}

function closeDetail() {
  const overlay = document.getElementById("detail-overlay");
  if (overlay) overlay.classList.add("hidden");
}

function copyDomainJson() {
  if (!_currentDomainData) return;
  navigator.clipboard.writeText(JSON.stringify(_currentDomainData, null, 2));
  toast("Record JSON copied to clipboard!");
}

// ── 4. Pipeline Execution, Stop Control & SSE Log Streaming ───────────────────
async function onRunCheckClick() {
  const mode = document.getElementById("check-mode").value;
  if (mode === "unconfirmed" || mode === "blocked") {
    const ok = await showConfirm(
      "⚠️ Re-Check Confirmation",
      `Running Stage 2 in '${mode}' mode will trigger web fetches and AI cross-examinations for all ${mode} domains. This may take several minutes under load. Do you want to proceed?`
    );
    if (!ok) return;
  }
  runStage("check", { mode });
}

async function onRunImportClick() {
  const txt = document.getElementById("import-domains").value;
  const domains = txt.split("\n").map(s => s.trim()).filter(Boolean);
  if (domains.length === 0) {
    toast("[!] Please paste at least one domain or URL");
    return;
  }

  const ok = await showConfirm(
    "📥 Import Confirmation",
    `Are you sure you want to import and queue ${domains.length} domain(s) directly as confirmed gambling true positives?`
  );
  if (!ok) return;

  try {
    const res = await fetch(`${API}/api/run/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domains }),
    });

    if (!res.ok) {
      const err = await res.json();
      toast(`[!] Error: ${err.detail}`);
      return;
    }

    const data = await res.json();
    toast(`[+] Imported: ${data.inserted} new | Reset for re-capture: ${data.reset}`);
    document.getElementById("import-domains").value = "";
    loadOverview();
  } catch (err) {
    toast(`[!] Import error: ${err.message}`);
  }
}

async function runStage(stage, body = {}) {
  const startBtn = document.getElementById(`btn-${stage}`);
  const stopBtn = document.getElementById(`btn-stop-${stage}`);
  const stopAllBtn = document.getElementById("btn-stop-all");
  const logEl = document.getElementById(`log-${stage}`);
  const badge = document.getElementById(`badge-${stage}`);

  if (startBtn) { startBtn.disabled = true; startBtn.classList.add("hidden"); }
  if (stopBtn) { stopBtn.classList.remove("hidden"); stopBtn.disabled = false; stopBtn.textContent = `⏹ Stop ${formatStageName(stage)}`; }
  if (stopAllBtn) stopAllBtn.classList.remove("hidden");

  _runningStages.add(stage);

  if (badge) {
    badge.className = "badge badge-running";
    badge.textContent = "Running...";
  }
  if (logEl) logEl.innerHTML = `<p class="info">// Starting ${stage} stage execution...</p>`;

  try {
    const res = await fetch(`${API}/api/run/${stage}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({ detail: "Unknown error" }));
      toast(`[!] ${errData.detail || 'Stage failed to start'}`);
      resetStageButtons(stage, "failed");
      return;
    }

    const { job_id } = await res.json();
    streamLogs(job_id, stage, logEl, startBtn, stopBtn, badge);
  } catch (err) {
    toast(`[!] Network error: ${err.message}`);
    resetStageButtons(stage, "failed");
  }
}

async function stopStage(stage) {
  const stopBtn = document.getElementById(`btn-stop-${stage}`);
  if (stopBtn) {
    stopBtn.disabled = true;
    stopBtn.textContent = "Stopping...";
  }

  toast(`[!] Stopping ${stage} stage...`);

  try {
    const res = await fetch(`${API}/api/stop/${stage}`, { method: "POST" });
    const data = await res.json();
    if (data.stopped) {
      toast(`[✓] ${stage} stage stop command received.`);
    }
  } catch (err) {
    toast(`[!] Error stopping stage: ${err.message}`);
  }
}

async function stopAllStages() {
  const stopAllBtn = document.getElementById("btn-stop-all");
  if (stopAllBtn) {
    stopAllBtn.disabled = true;
    stopAllBtn.textContent = "Stopping All...";
  }

  toast("[!] Stopping all running pipeline stages...");

  try {
    const res = await fetch(`${API}/api/stop`, { method: "POST" });
    const data = await res.json();
    toast(`[✓] Stop signal sent to ${data.stopped_count || 0} active job(s).`);
  } catch (err) {
    toast(`[!] Error: ${err.message}`);
  }
}

function resetStageButtons(stage, finalStatus = "idle") {
  _runningStages.delete(stage);

  const startBtn = document.getElementById(`btn-${stage}`);
  const stopBtn = document.getElementById(`btn-stop-${stage}`);
  const stopAllBtn = document.getElementById("btn-stop-all");
  const badge = document.getElementById(`badge-${stage}`);

  if (startBtn) {
    startBtn.disabled = false;
    startBtn.classList.remove("hidden");
  }
  if (stopBtn) {
    stopBtn.classList.add("hidden");
    stopBtn.disabled = false;
    stopBtn.textContent = `⏹ Stop ${formatStageName(stage)}`;
  }

  if (badge) {
    if (finalStatus === "done") {
      badge.className = "badge badge-done";
      badge.textContent = "Done";
    } else if (finalStatus === "stopped") {
      badge.className = "badge badge-failed";
      badge.textContent = "Stopped";
    } else if (finalStatus === "failed") {
      badge.className = "badge badge-failed";
      badge.textContent = "Failed";
    } else {
      badge.className = "badge badge-idle";
      badge.textContent = "Idle";
    }
  }

  if (_runningStages.size === 0 && stopAllBtn) {
    stopAllBtn.classList.add("hidden");
    stopAllBtn.disabled = false;
    stopAllBtn.textContent = "⏹ Stop All Running Jobs";
  }
}

function formatStageName(stage) {
  if (stage === "keywords") return "Search";
  if (stage === "check") return "Check";
  if (stage === "export") return "Export";
  if (stage === "screenshot") return "Capture";
  return stage;
}

function streamLogs(job_id, stage, logEl, startBtn, stopBtn, badge) {
  if (_jobEventSources[job_id]) _jobEventSources[job_id].close();

  const es = new EventSource(`${API}/api/logs/${job_id}`);
  _jobEventSources[job_id] = es;

  let stoppedByUser = false;

  es.onmessage = (e) => {
    if (e.data === "__PING__") return;
    if (e.data.includes("stopped by user")) {
      stoppedByUser = true;
    }
    if (e.data === "__DONE__") {
      es.close();
      const finalStatus = stoppedByUser ? "stopped" : "done";
      resetStageButtons(stage, finalStatus);
      toast(stoppedByUser ? `Stage '${stage}' stopped by user.` : `Stage '${stage}' finished successfully!`);
      loadOverview();
      return;
    }

    const p = document.createElement("p");
    p.textContent = e.data;
    const txtLower = e.data.toLowerCase();
    if (txtLower.includes("error") || txtLower.includes("failed")) p.className = "err";
    else if (txtLower.includes("stopped") || txtLower.includes("warning") || txtLower.includes("[!]")) p.className = "warn";
    else if (txtLower.includes("[+]") || txtLower.includes("success") || txtLower.includes("[✓]")) p.className = "info";

    logEl.appendChild(p);
    logEl.scrollTop = logEl.scrollHeight;
  };

  es.onerror = () => {
    es.close();
    resetStageButtons(stage, stoppedByUser ? "stopped" : "failed");
  };
}

function runScreenshotStage() {
  const mode = document.getElementById("ss-mode").value;
  const rawDomains = document.getElementById("ss-domains").value;
  const domainsList = mode === "file"
    ? rawDomains.split("\n").map(s => s.trim()).filter(Boolean)
    : [];

  runStage("screenshot", { mode, domains: domainsList });
}

// ── Initial Boot ─────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadOverview();
  setInterval(() => {
    if (_activeTab === "overview") loadOverview();
  }, 30000);
});
