// app.js — Frontend Controller for GamblingWebFind Intelligence Dashboard
const API = "";
let _activeTab = "overview";
let _domainsPage = 1;
let _currentFilter = "all";
let _activeFilterParams = {};
let _domainsQ = "";
let _ipFilter = "";
let _datePreset = "all";
let _domainsPerPage = 50;
let _currentDomainData = null;
let _searchDebounceTimer = null;
let _ipDebounceTimer = null;
let _confirmResolve = null;
let _lastSandboxResult = null;

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
  if (name === "sandbox") {
    const input = document.getElementById("sandbox-url-input");
    if (input && !input.value) input.focus();
  }
}

// ── Date Preset Helpers ──────────────────────────────────────────────────────
function formatDateYMD(d) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function setDatePreset(presetKey) {
  _datePreset = presetKey;
  const fromEl = document.getElementById("filter-from-date");
  const toEl = document.getElementById("filter-to-date");
  if (!fromEl || !toEl) return;

  const now = new Date();
  let fromStr = "";
  let toStr = "";

  if (presetKey === "today") {
    fromStr = toStr = formatDateYMD(now);
  } else if (presetKey === "yesterday") {
    const yest = new Date(now);
    yest.setDate(yest.getDate() - 1);
    fromStr = toStr = formatDateYMD(yest);
  } else if (presetKey === "7days") {
    const past = new Date(now);
    past.setDate(past.getDate() - 7);
    fromStr = formatDateYMD(past);
    toStr = formatDateYMD(now);
  } else if (presetKey === "30days") {
    const past = new Date(now);
    past.setDate(past.getDate() - 30);
    fromStr = formatDateYMD(past);
    toStr = formatDateYMD(now);
  } else if (presetKey === "this_month") {
    const firstDay = new Date(now.getFullYear(), now.getMonth(), 1);
    fromStr = formatDateYMD(firstDay);
    toStr = formatDateYMD(now);
  } else {
    // "all"
    fromStr = "";
    toStr = "";
  }

  fromEl.value = fromStr;
  toEl.value = toStr;

  document.querySelectorAll(".date-chip").forEach(chip => {
    chip.classList.toggle("active", chip.dataset.preset === presetKey);
  });

  onDateFilterChange();
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

// ── IP Filter Handlers ───────────────────────────────────────────────────────
function onIpFilterInput(val) {
  _ipFilter = val.trim();
  const clearBtn = document.getElementById("ip-clear-btn");
  if (clearBtn) clearBtn.classList.toggle("hidden", !_ipFilter);

  clearTimeout(_ipDebounceTimer);
  _ipDebounceTimer = setTimeout(() => {
    updateFilterSummary();
    loadDomains(1);
  }, 300);
}

function clearIpInput() {
  _ipFilter = "";
  const ipInput = document.getElementById("filter-ip-input");
  if (ipInput) ipInput.value = "";
  const clearBtn = document.getElementById("ip-clear-btn");
  if (clearBtn) clearBtn.classList.add("hidden");

  updateFilterSummary();
  loadDomains(1);
}

// ── Search & Filter Controls ─────────────────────────────────────────────────
function jumpToDomains(filterObj = {}) {
  _domainsPage = 1;
  _domainsQ = "";
  _ipFilter = "";
  const sInput = document.getElementById("domain-search");
  if (sInput) sInput.value = "";
  const sClear = document.getElementById("search-clear-btn");
  if (sClear) sClear.classList.add("hidden");

  const ipInput = document.getElementById("filter-ip-input");
  if (ipInput) ipInput.value = "";
  const ipClear = document.getElementById("ip-clear-btn");
  if (ipClear) ipClear.classList.add("hidden");

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

  syncDropdownsFromActiveFilter();

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

  document.querySelectorAll(".chip-btn").forEach(chip => chip.classList.remove("active"));
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
  _ipFilter = "";
  _activeFilterParams = {};
  _currentFilter = "all";
  _domainsPage = 1;

  const sInput = document.getElementById("domain-search");
  if (sInput) sInput.value = "";
  const sClear = document.getElementById("search-clear-btn");
  if (sClear) sClear.classList.add("hidden");

  const ipInput = document.getElementById("filter-ip-input");
  if (ipInput) ipInput.value = "";
  const ipClear = document.getElementById("ip-clear-btn");
  if (ipClear) ipClear.classList.add("hidden");

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

  document.querySelectorAll(".date-chip").forEach(chip => {
    chip.classList.toggle("active", chip.dataset.preset === "all");
  });

  document.querySelectorAll(".chip-btn").forEach(chip => {
    chip.classList.toggle("active", chip.dataset.filter === "all");
  });

  updateFilterSummary();
  loadDomains(1);
  toast("All search, IP, and date filters have been reset.");
}

function updateFilterSummary() {
  const summaryEl = document.getElementById("filter-tags-summary");
  if (!summaryEl) return;

  const tags = [];
  if (_domainsQ) tags.push(`Query: "${_domainsQ}"`);
  if (_ipFilter) tags.push(`IP: "${_ipFilter}"`);
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

// ── Screenshot Lightbox Modal ────────────────────────────────────────────────
function openScreenshotModal(domain, url) {
  const modal = document.getElementById("screenshot-modal");
  const img = document.getElementById("ss-modal-img");
  const title = document.getElementById("ss-modal-domain");
  const caption = document.getElementById("ss-modal-caption");
  const dLink = document.getElementById("ss-modal-download-link");

  const shotUrl = `${API}/api/domains/${domain}/screenshot?t=${Date.now()}`;
  img.src = shotUrl;
  title.textContent = `📸 Evidence: ${domain}`;
  caption.textContent = `Target URL: ${url || 'https://' + domain}`;
  dLink.href = shotUrl;
  dLink.download = `${domain}_screenshot.jpg`;

  modal.classList.remove("hidden");
}

function closeScreenshotModal() {
  const modal = document.getElementById("screenshot-modal");
  modal.classList.add("hidden");
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

    const mDot = document.getElementById("hdr-mongo-dot");
    const mTxt = document.getElementById("hdr-mongo-text");
    if (mDot && mTxt) {
      mDot.className = "status-dot";
      mTxt.textContent = `Mongo: ${fmtNum(chk.total_checked || src.total_listed)}`;
    }

    document.getElementById("st-src-total").textContent = fmtNum(src.total_listed);
    document.getElementById("st-src-pending").textContent = fmtNum(src.pending);
    document.getElementById("st-src-active").textContent = fmtNum(src.active);
    document.getElementById("st-src-inactive").textContent = fmtNum(src.inactive);
    document.getElementById("st-src-blocked").textContent = fmtNum(src.blocked_source);

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

    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = fmtNum(val);
    };
    setVal("st-exp-done", chk.exported);
    setVal("st-exp-pending", chk.pending_export);

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
    setChip("chip-cnt-exported", chk.exported);
    setChip("chip-cnt-not-exported", Math.max(0, totalChk - (chk.exported || 0)));

    setVal("m-src-processed", src.processed);
    setVal("m-src-today", src.added_today);
    setVal("m-src-week", src.added_this_week);

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
    "Create a timestamped JSON dump of both 'domain_Listed' and 'checked_domains' MongoDB collections now?"
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
    toast(`✅ Backup Complete! Saved ${fmtNum(data.source_count)} source & ${fmtNum(data.checked_count)} checked records.`);
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
    if (_ipFilter) url += `&ip=${encodeURIComponent(_ipFilter)}`;

    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status} loading domains`);
    const data = await res.json();

    const results = data.results || [];
    const total = data.total || 0;
    const maxPage = Math.max(1, Math.ceil(total / _domainsPerPage));

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
            <div class="empty-state-desc">Try modifying search query, IP address, adjusting date filters, or selecting a different status.</div>
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

function renderDomainFullRow(d) {
  const targetUrl = d.url || `https://${d.domain}`;
  const ipText = Array.isArray(d.ip) ? d.ip.join(", ") : d.ip;
  const ipDisplay = ipText
    ? `<span class="mono" style="font-size:12px;color:var(--cyan);font-weight:600" title="${ipText}">${ipText}</span>`
    : `<span class="muted mono" style="font-size:12px">—</span>`;

  let actionButtons = "";
  if (d.has_screenshot_file || d.screenshot_taken) {
    actionButtons += `
      <button class="btn btn-ghost btn-xs" onclick="openScreenshotModal('${d.domain}', '${targetUrl}')" title="View Captured Screenshot">
        📸 Proof
      </button>
    `;
  }
  actionButtons += `
    <button class="btn btn-ghost btn-xs" onclick="inspectInSandbox('${targetUrl}')" title="Test Live in Sandbox">
      ⚡ Inspect
    </button>
  `;

  return `
    <tr>
      <td>
        <span style="font-weight:700;color:#fff">${d.domain}</span>
        <a href="${targetUrl}" target="_blank" rel="noopener noreferrer" style="margin-left:6px;color:var(--accent-light);font-size:11px" title="Visit website">↗</a>
      </td>
      <td>${ipDisplay}</td>
      <td><span class="badge badge-${d.status || 'dead'}">${d.status || 'unknown'}</span></td>
      <td class="muted" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${d.reason || ''}">${d.reason || '—'}</td>
      <td class="mono muted" style="font-size:12px">${d.added_date || '—'}</td>
      <td>
        <div style="display:flex;gap:4px;align-items:center">
          ${actionButtons}
        </div>
      </td>
    </tr>
  `;
}

function inspectInSandbox(url) {
  showTab("sandbox");
  const input = document.getElementById("sandbox-url-input");
  if (input) {
    input.value = url;
    runUrlTest();
  }
}

// ── 3. Interactive Sandbox / Single URL Tester ───────────────────────────────
function setSandboxSample(url) {
  const input = document.getElementById("sandbox-url-input");
  if (input) {
    input.value = url;
    runUrlTest();
  }
}

async function pasteSandboxUrl() {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      document.getElementById("sandbox-url-input").value = text.trim();
      toast("URL pasted from clipboard!");
    }
  } catch (err) {
    toast("Please allow clipboard permissions or paste manually.");
  }
}

async function runUrlTest() {
  const input = document.getElementById("sandbox-url-input");
  const rawUrl = (input ? input.value : "").trim();
  if (!rawUrl) {
    toast("Please enter a valid URL or domain.");
    return;
  }

  const runAi = document.getElementById("toggle-run-ai").checked;
  const takeScreenshot = document.getElementById("toggle-take-screenshot").checked;

  const btn = document.getElementById("btn-run-sandbox");
  const btnText = document.getElementById("btn-run-sandbox-text");
  const loader = document.getElementById("sandbox-loading-indicator");
  const resContainer = document.getElementById("sandbox-result-container");
  const loadTitle = document.getElementById("sandbox-loading-title");
  const loadSubtitle = document.getElementById("sandbox-loading-subtitle");

  btn.disabled = true;
  btnText.textContent = "⏳ Analyzing...";
  loader.classList.remove("hidden");
  resContainer.classList.add("hidden");

  loadTitle.textContent = `Connecting to ${rawUrl}...`;
  loadSubtitle.textContent = "Bypassing WAF, analyzing keywords & querying Ollama AI challenge...";

  try {
    const res = await fetch(`${API}/api/test-url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: rawUrl,
        run_ai: runAi,
        take_screenshot: takeScreenshot
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || `Server returned error HTTP ${res.status}`);
    }

    const data = await res.json();
    _lastSandboxResult = data;
    renderSandboxResult(data);
    toast(`Diagnostic Complete for ${data.domain}!`);
  } catch (err) {
    console.error("Sandbox error:", err);
    toast(`[!] Sandbox error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btnText.textContent = "🚀 Inspect Live URL";
    loader.classList.add("hidden");
  }
}

function renderSandboxResult(data) {
  const resContainer = document.getElementById("sandbox-result-container");
  resContainer.classList.remove("hidden");

  // Top Banner
  const statusBadge = document.getElementById("res-status-badge");
  statusBadge.className = `status-badge-lg badge-${data.status}`;
  if (data.status === "gambling") statusBadge.textContent = "🎰 Confirmed Gambling";
  else if (data.status === "regular") statusBadge.textContent = "🏢 Regular Website";
  else if (data.status === "blocked") statusBadge.textContent = "🛡️ Blocked / 403 WAF";
  else if (data.status === "dead") statusBadge.textContent = "💀 Dead / Unreachable";
  else statusBadge.textContent = "⏳ Unconfirmed Site";

  document.getElementById("res-domain-text").textContent = data.domain;
  document.getElementById("res-url-text").textContent = data.url;
  document.getElementById("res-http-status").textContent = data.http_status ? `${data.http_status} Response` : (data.fetch_error || "Connection Failed");
  document.getElementById("res-latency").textContent = `${data.latency_sec}s`;
  document.getElementById("res-score").textContent = data.heuristic ? data.heuristic.score.toFixed(1) : "0.0";

  // Card 1: Network & IP
  document.getElementById("res-net-domain").textContent = data.domain;
  const ipText = Array.isArray(data.ip) ? data.ip.join(", ") : (data.ip || "Unresolved (DNS fail)");
  document.getElementById("res-net-ip").textContent = ipText;
  document.getElementById("res-net-code").textContent = data.http_status || "—";
  document.getElementById("res-net-latency").textContent = `${data.latency_sec}s`;
  document.getElementById("res-net-fetch-status").textContent = data.fetch_error ? `Error: ${data.fetch_error}` : "HTTP 200 Success";

  // Card 2: Heuristics
  const scoreBadge = document.getElementById("res-heuristic-badge");
  scoreBadge.textContent = `Score: ${data.heuristic.score.toFixed(1)}`;
  scoreBadge.className = data.heuristic.score >= 5.0 ? "badge badge-gambling" : (data.heuristic.score >= 2.5 ? "badge badge-unconfirmed" : "badge badge-regular");

  const kwContainer = document.getElementById("res-keywords-container");
  if (data.heuristic.matched_keywords && data.heuristic.matched_keywords.length > 0) {
    kwContainer.innerHTML = data.heuristic.matched_keywords.map(kw => `<span class="keyword-pill">${kw}</span>`).join(" ");
  } else {
    kwContainer.innerHTML = `<span class="muted" style="font-size:12px">No gambling signals matched</span>`;
  }

  const negContainer = document.getElementById("res-negatives-container");
  if (data.heuristic.negative_signals && data.heuristic.negative_signals.length > 0) {
    negContainer.innerHTML = data.heuristic.negative_signals.map(ns => `<span class="keyword-pill neg">${ns}</span>`).join(" ");
  } else {
    negContainer.innerHTML = `<span class="muted" style="font-size:12px">None triggered (Passed gate)</span>`;
  }

  // Card 3: AI Challenge
  const ai = data.ai;
  const aiAnalyst = document.getElementById("res-ai-analyst");
  const aiConf = document.getElementById("res-ai-confidence");
  const aiValidator = document.getElementById("res-ai-validator");
  const aiReasoning = document.getElementById("res-ai-reasoning");

  if (ai && !ai.error) {
    aiAnalyst.textContent = ai.analyst_verdict || ai.verdict || "Completed";
    aiConf.textContent = ai.confidence ? `${(ai.confidence * 100).toFixed(0)}%` : "High";
    aiValidator.textContent = ai.validator_confirmed ? "Confirmed by Judge" : (ai.validator_verdict || "Passed");
    aiReasoning.textContent = ai.reason || data.reason || "No detailed reasoning text provided.";
  } else {
    aiAnalyst.textContent = "AI Bypassed / Offline";
    aiConf.textContent = "—";
    aiValidator.textContent = "—";
    aiReasoning.textContent = ai && ai.error ? ai.error : (data.reason || "Evaluated purely via high-confidence heuristic fast path.");
  }

  // Card 4: Screenshot Evidence
  const shotImg = document.getElementById("sandbox-screenshot-img");
  const shotPlaceholder = document.getElementById("sandbox-screenshot-placeholder");
  const expandBtn = document.getElementById("btn-expand-screenshot");

  if (data.screenshot_url) {
    shotImg.src = data.screenshot_url;
    shotImg.classList.remove("hidden");
    shotPlaceholder.classList.add("hidden");
    expandBtn.classList.remove("hidden");
  } else {
    shotImg.classList.add("hidden");
    shotPlaceholder.classList.remove("hidden");
    expandBtn.classList.add("hidden");
    shotPlaceholder.textContent = data.screenshot_taken === false ? "Screenshot capture was skipped or timed out." : "No visual proof available.";
  }
}

function openScreenshotModalFromSandbox() {
  if (!_lastSandboxResult || !_lastSandboxResult.screenshot_url) return;
  openScreenshotModal(_lastSandboxResult.domain, _lastSandboxResult.url);
}

async function saveTestedDomainFromSandbox() {
  if (!_lastSandboxResult) return;
  const saveBtn = document.getElementById("btn-save-sandbox-domain");
  saveBtn.disabled = true;

  try {
    toast("Saving domain to MongoDB...");
    const res = await fetch(`${API}/api/save-domain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain: _lastSandboxResult.domain,
        url: _lastSandboxResult.url,
        status: _lastSandboxResult.status,
        reason: _lastSandboxResult.reason,
        ip: _lastSandboxResult.ip,
        screenshot_taken: _lastSandboxResult.screenshot_taken
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed saving domain");
    }

    const respData = await res.json();
    toast(`✅ ${respData.message}`);
  } catch (err) {
    toast(`[!] Save error: ${err.message}`);
  } finally {
    saveBtn.disabled = false;
  }
}

function copyForensicJson() {
  if (!_lastSandboxResult) return;
  navigator.clipboard.writeText(JSON.stringify(_lastSandboxResult, null, 2));
  toast("Forensic diagnostic JSON copied to clipboard!");
}

// ── Initial Boot ─────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadOverview();
  setInterval(() => {
    if (_activeTab === "overview") loadOverview();
  }, 30000);
});
