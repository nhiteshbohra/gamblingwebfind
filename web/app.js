// app.js — Frontend Controller for GamblingWebFind Dashboard
const API = "";
let _activeTab = "overview";
let _domainsPage = 1;
let _currentFilter = "all";
let _activeFilterParams = {};
let _domainsQ = "";
let _domainsPerPage = 50;
let _currentDomainData = null;
let _searchDebounceTimer = null;
let _confirmResolve = null;

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

    // Row C: Compliance & Export
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = fmtNum(val);
    };
    setVal("st-ss-taken", chk.screenshot_taken);
    setVal("st-ss-pending", chk.screenshot_pending);
    setVal("st-exp-done", chk.exported);
    setVal("st-exp-pending", chk.pending_export);

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

    // Deep Metrics — Throughput
    setVal("m-src-processed", src.processed);
    setVal("m-src-today", src.added_today);
    setVal("m-src-week", src.added_this_week);

    setVal("m-rate-text", `${chk.gambling_rate || 0}%`);
    const rateBar = document.getElementById("m-rate-bar");
    if (rateBar) rateBar.style.width = `${Math.min(100, (chk.gambling_rate || 0) * 4)}%`;
    setVal("m-ai-count", chk.ai_classified);
    setVal("m-kw-count", chk.keyword_classified);

    // Deep Metrics — Reports
    setVal("m-rep-runs", rep.runs);
    setVal("m-rep-pdf", `${fmtNum(rep.pdf_files)} PDF`);
    setVal("m-rep-xlsx", `${fmtNum(rep.xlsx_files)} Excel`);
    setVal("m-rep-docx", `${fmtNum(rep.docx_files)} Word`);
    setVal("m-rep-last", rep.last_export_timestamp || "Never");

  } catch (err) {
    console.error("Overview error:", err);
    if (errBanner) {
      document.getElementById("global-error-msg").textContent = `Error connecting to backend services: ${err.message}`;
      errBanner.classList.remove("hidden");
    }
    toast(`[!] Connection error: ${err.message}`);
  }
}

async function triggerDatabaseBackup() {
  const ok = await showConfirm(
    "💾 Backup Databases",
    "Create a timestamped JSON dump of both 'domain_Listed' (source) and 'checked_domains' (classified results) MongoDB collections now?"
  );
  if (!ok) return;

  try {
    toast("📦 Creating MongoDB database backup...");
    const res = await fetch(`${API}/api/backup`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Backup failed");
    }
    const data = await res.json();
    toast(`✅ Backup Complete! Saved ${fmtNum(data.source_count)} source & ${fmtNum(data.checked_count)} checked records to ${data.backup_dir}`);
  } catch (err) {
    toast(`[!] Backup error: ${err.message}`);
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
  return `
    <tr>
      <td><span style="font-weight:700;color:#fff">${d.domain}</span></td>
      <td><span class="badge badge-${d.status || 'dead'}">${d.status || 'unknown'}</span></td>
      <td class="muted" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${d.reason || '—'}</td>
      <td class="mono muted" style="font-size:12px">${d.added_date || '—'}</td>
    </tr>
  `;
}

function renderDomainFullRow(d) {
  const expBadge = d.exported
    ? `<span class="badge" style="background:var(--success-bg);color:#6ee7b7">Exported</span>`
    : `<span class="badge badge-idle">No</span>`;

  const targetUrl = d.url || `https://${d.domain}`;
  const ipText = Array.isArray(d.ip) ? d.ip.join(", ") : d.ip;
  const ipDisplay = ipText
    ? `<span class="mono" style="font-size:12px;color:var(--text-light)" title="${ipText}">${ipText}</span>`
    : `<span class="muted mono" style="font-size:12px">—</span>`;

  return `
    <tr>
      <td>
        <span style="font-weight:700;color:#fff">${d.domain}</span>
        <a href="${targetUrl}" target="_blank" rel="noopener noreferrer" style="margin-left:6px;color:var(--accent-light);font-size:11px" title="Visit website in new tab">↗</a>
      </td>
      <td>${ipDisplay}</td>
      <td><span class="badge badge-${d.status || 'dead'}">${d.status || 'unknown'}</span></td>
      <td class="muted" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${d.reason || '—'}</td>
      <td class="mono muted" style="font-size:12px">${d.added_date || '—'}</td>
      <td>${expBadge}</td>
    </tr>
  `;
}

function copyDomainJson() {
  if (!_currentDomainData) return;
  navigator.clipboard.writeText(JSON.stringify(_currentDomainData, null, 2));
  toast("Record JSON copied to clipboard!");
}

// ── Initial Boot ─────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadOverview();
  setInterval(() => {
    if (_activeTab === "overview") loadOverview();
  }, 30000);
});
