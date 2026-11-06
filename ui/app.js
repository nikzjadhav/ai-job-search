const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

let state = null;
let pollTimer = null;

const PORTAL_LABELS = {
  "linkedin-search": "LinkedIn",
  "freehire-search": "freehire",
  "websearch": "Web search",
};

const APP_STATUSES = [
  ["drafted", "Drafted"],
  ["applied", "Applied"],
  ["interview", "Interview"],
  ["offer", "Offer"],
  ["hired", "Hired"],
  ["rejected", "Rejected"],
  ["no_response", "No response"],
  ["withdrawn", "Withdrawn"],
];

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  const raw = String(value || "").trim();
  return /^https?:\/\//i.test(raw) ? raw : "";
}

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { el.className = "toast"; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return data;
}

function fitBadge(fit) {
  const label = fit ? fit.charAt(0).toUpperCase() + fit.slice(1) : "—";
  return `<span class="badge fit-${esc(fit || "medium")}">${esc(label)}</span>`;
}

function statusBadge(status) {
  return `<span class="badge status-${esc(status || "new")}">${esc(status || "new")}</span>`;
}

function appBadge(status) {
  const safe = String(status || "applied").replace(/[^a-z_]/g, "");
  return `<span class="badge app-${esc(safe)}">${esc((status || "applied").replaceAll("_", " "))}</span>`;
}

function statusSelect(application) {
  const current = String(application.status || "applied");
  const options = APP_STATUSES.map(
    ([value, label]) => `<option value="${value}"${value === current ? " selected" : ""}>${label}</option>`
  ).join("");
  return `<select class="status-select" data-app-id="${esc(application.id)}" aria-label="Update application status">${options}</select>`;
}

function renderStats(stats) {
  const cards = [
    [stats.total, "Roles found", "#4d6bfe"],
    [stats.eligible, "Eligible", "#0ea5e9"],
    [stats.high, "High fit", "#16a34a"],
    [stats.applications, "Applications", "#8b5cf6"],
    [stats.interviews, "Interviews+", "#f59e0b"],
    [stats.offers, "Offers", "#22c55e"],
  ];
  $("#stats").innerHTML = cards
    .map(
      ([value, label, color]) =>
        `<div class="stat" style="border-left-color:${color}"><div class="stat-number">${esc(value)}</div><div class="stat-label">${esc(label)}</div></div>`
    )
    .join("");
}

function renderMarkets(meta) {
  const container = $("#markets");
  if (container.dataset.ready === "1") return;
  container.innerHTML = (meta.markets || [])
    .map(
      (name) =>
        `<label class="chip"><input type="checkbox" class="market-check" value="${esc(name)}" checked>${esc(name)}</label>`
    )
    .join("");
  container.dataset.ready = "1";
}

function fillSelect(selector, values, allLabel) {
  const select = $(selector);
  const current = select.value;
  select.innerHTML = `<option value="">${allLabel}</option>` + values
    .map((value) => `<option value="${esc(value)}">${esc(value)}</option>`)
    .join("");
  if (values.includes(current)) select.value = current;
}

function renderFilters() {
  fillSelect("#filter-country", Object.keys(state.by_country).sort(), "All markets");
  fillSelect("#filter-fit", ["high", "medium", "low", "skipped"].filter((fit) => state.by_fit[fit]), "All fit");
  fillSelect("#filter-status", Object.keys(state.jobs.reduce((acc, job) => { acc[job.status] = 1; return acc; }, {})).sort(), "All status");
  fillSelect("#filter-portal", Object.keys(state.by_portal).sort(), "All sources");
}

function renderJobs() {
  const body = $("#jobs-body");
  body.innerHTML = state.jobs.map((job) => {
    const url = safeUrl(job.url);
    const title = url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(job.title)}</a>`
      : esc(job.title);
    const search = `${job.title} ${job.company} ${job.location}`.toLowerCase();
    const open = url
      ? `<a class="btn-mini" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open</a>`
      : "";
    return `<tr data-country="${esc(job.country)}" data-fit="${esc(job.fit)}" data-status="${esc(job.status)}" data-portal="${esc(job.portal)}" data-search="${esc(search)}">
      <td><div class="role">${title}</div><div class="sub">${esc(job.location || "")}</div></td>
      <td>${esc(job.company)}</td>
      <td>${esc(job.country || "Other")}</td>
      <td>${fitBadge(job.fit)}</td>
      <td>${statusBadge(job.status)}${job.applied ? `<div class="sub">${esc(job.application_status || "applied")}</div>` : ""}</td>
      <td>${esc(job.posted_date || "—")}</td>
      <td>${esc(PORTAL_LABELS[job.portal] || job.portal || "—")}</td>
      <td><div class="table-actions">${open}<button class="btn-mini ai" type="button" data-ai="${esc(job.id)}">AI generate</button><button class="btn-mini" type="button" data-cover="${esc(job.id)}">Cover letter</button><button class="btn-mini primary" type="button" data-apply="${esc(job.id)}">${job.applied ? "Update" : "Mark applied"}</button></div></td>
    </tr>`;
  }).join("");
  $("#jobs-count").textContent = `${state.jobs.length} roles · ${state.stats.eligible} eligible · ${state.stats.high} high fit`;
  applyFilters();
}

function renderApplications() {
  const body = $("#apps-body");
  const applications = state.applications || [];
  $("#apps-empty").classList.toggle("hidden", applications.length > 0);
  $("#apps-table").classList.toggle("hidden", applications.length === 0);
  body.innerHTML = applications.map((application) => {
    const url = safeUrl(application.job_url || application.source);
    const posting = url
      ? `<a class="btn-mini" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open</a>`
      : "—";
    const files = [application.cv_file, application.cover_letter_file, application.cover_letter_pdf].filter(Boolean);
    const fileText = files.length
      ? files.map((file) => `<div class="sub">${esc(file)}</div>`).join("")
      : "—";
    return `<tr>
      <td>${esc(application.company)}</td>
      <td class="role">${esc(application.title)}</td>
      <td>${esc(application.applied_date || "—")}</td>
      <td>${statusSelect(application)}</td>
      <td class="note-cell" title="${esc(application.notes || "")}">${esc(application.notes || "—")}</td>
      <td>${fileText}</td>
      <td>${posting}</td>
    </tr>`;
  }).join("");
}

function applyFilters() {
  const query = $("#search").value.trim().toLowerCase();
  const country = $("#filter-country").value;
  const fit = $("#filter-fit").value;
  const status = $("#filter-status").value;
  const portal = $("#filter-portal").value;
  const onlyTarget = $("#only-target").checked;
  let visible = 0;
  $$("#jobs-body tr").forEach((row) => {
    const show =
      (!query || (row.dataset.search || "").includes(query)) &&
      (!country || row.dataset.country === country) &&
      (!fit || row.dataset.fit === fit) &&
      (!status || row.dataset.status === status) &&
      (!portal || row.dataset.portal === portal) &&
      (!onlyTarget || row.dataset.country !== "Other");
    row.classList.toggle("hidden", !show);
    if (show) visible += 1;
  });
  $("#jobs-empty").classList.toggle("hidden", visible > 0);
  if (state) {
    $("#jobs-count").textContent = `${visible} of ${state.jobs.length} roles shown · ${state.stats.eligible} eligible · ${state.stats.high} high fit`;
  }
}

function renderBackend() {
  const meta = state.meta || {};
  $("#backend-note").textContent = meta.bun ? "DuckDB · Bun" : "DuckDB · npx fallback";
  $("#db-path").textContent = meta.db || "";
}

function renderAll() {
  renderStats(state.stats);
  renderMarkets(state.meta);
  renderFilters();
  renderJobs();
  renderApplications();
  renderBackend();
}

async function loadState(showError = true) {
  try {
    state = await api("/api/state");
    renderAll();
  } catch (error) {
    if (showError) toast(error.message, true);
  }
}

async function openApplyDialog(jobId, draftOnly = false) {
  const job = state.jobs.find((item) => item.id === jobId);
  if (!job) return;
  const application = (state.applications || []).find((item) => item.job_id === jobId);
  $("#dialog-job-id").value = job.id;
  $("#dialog-role").textContent = `${job.title} · ${job.company}`;
  $("#dialog-status").value = job.application_status || application?.status || (draftOnly ? "drafted" : "applied");
  $("#dialog-date").value = job.applied_date || application?.applied_date || new Date().toISOString().slice(0, 10);
  $("#dialog-notes").value = job.notes || application?.notes || "";
  $("#dialog-cv").value = application?.cv_file || "";
  $("#dialog-cover-text").value = application?.cover_letter_text || "";
  $("#apply-dialog").showModal();
  if (!$("#dialog-cover-text").value) {
    try {
      const draft = await api("/api/cover-letter/draft", {
        method: "POST",
        body: JSON.stringify({ job_id: job.id }),
      });
      $("#dialog-cover-text").value = draft.text || "";
    } catch (error) {
      toast(`Could not prefill cover letter: ${error.message}`, true);
    }
  }
}

async function submitApplication(event) {
  event.preventDefault();
  const payload = {
    job_id: $("#dialog-job-id").value,
    status: $("#dialog-status").value,
    applied_date: $("#dialog-date").value,
    notes: $("#dialog-notes").value,
    cv_file: $("#dialog-cv").value,
    cover_letter_text: $("#dialog-cover-text").value,
  };
  try {
    const result = await api("/api/apply", { method: "POST", body: JSON.stringify(payload) });
    $("#apply-dialog").close();
    if (result.cover_error) {
      toast(`Saved, but the PDF was not compiled: ${result.cover_error}`, true);
    } else if (result.cover_letter_pdf) {
      toast(`Application saved. PDF: ${result.cover_letter_pdf}`);
    } else if (result.cover_letter_file) {
      toast(`Application saved. LaTeX: ${result.cover_letter_file}`);
    } else {
      toast("Application saved to DuckDB and the tracker.");
    }
    await loadState(false);
  } catch (error) {
    toast(error.message, true);
  }
}

async function generateWithAI(button) {
  const jobId = button.dataset.ai;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Generating…";
  toast("Generating cover letter with DeepSeek. This can take 20-60 seconds…");
  try {
    const result = await api("/api/cover-letter/generate", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId }),
    });
    await loadState(false);
    await openApplyDialog(jobId);
    if (result.compiled) {
      toast(`Cover letter generated and compiled: ${result.pdf_file}`);
    } else {
      toast(`Cover letter .tex saved. PDF not compiled: ${result.error || "unknown error"}`, true);
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function updateApplicationStatus(select) {
  const id = select.dataset.appId;
  try {
    await api("/api/application", {
      method: "POST",
      body: JSON.stringify({ id, status: select.value }),
    });
    toast(`Status updated to ${select.value.replaceAll("_", " ")}.`);
    await loadState(false);
  } catch (error) {
    toast(error.message, true);
    await loadState(false);
  }
}

function setScrapeBusy(busy) {
  $("#scrape-all").disabled = busy;
  $("#scrape-selected").disabled = busy;
  $("#refresh-inline").disabled = busy;
  $("#scrape-dot").className = busy ? "dot-run" : "dot-idle";
}

async function startScrape(markets) {
  if (!markets.length) {
    toast("Select at least one market.", true);
    return;
  }
  try {
    setScrapeBusy(true);
    $("#scrape-status").className = "status-line";
    $("#scrape-status").textContent = `Scraping ${markets.join(", ")}…`;
    await api("/api/scrape", { method: "POST", body: JSON.stringify({ markets }) });
    pollScrape();
  } catch (error) {
    setScrapeBusy(false);
    $("#scrape-status").className = "status-line error";
    $("#scrape-status").textContent = error.message;
    toast(error.message, true);
  }
}

async function pollScrape() {
  clearTimeout(pollTimer);
  try {
    const status = await api("/api/scrape/status");
    const last = status.last;
    if (last) {
      const detail = `${last.status} · ${last.result_count || 0} rows${last.error ? ` · ${last.error}` : ""}`;
      $("#scrape-status").className = `status-line${last.status === "failed" ? " error" : ""}`;
      $("#scrape-status").textContent = `Last scrape: ${detail}`;
      $("#scrape-dot").className = last.status === "failed" ? "dot-err" : last.status === "running" ? "dot-run" : "dot-ok";
    }
    if (status.running) {
      setScrapeBusy(true);
      pollTimer = setTimeout(pollScrape, 1800);
      return;
    }
    setScrapeBusy(false);
    if (last && ["completed", "completed_with_errors"].includes(last.status)) {
      await loadState(false);
      toast(`Scrape finished: ${last.result_count || 0} results.`);
    }
  } catch (error) {
    setScrapeBusy(false);
    $("#scrape-status").className = "status-line error";
    $("#scrape-status").textContent = error.message;
  }
}

async function refreshFiles() {
  try {
    await api("/api/refresh", { method: "POST", body: "{}" });
    await loadState(false);
    toast("Re-imported JSON and tracker into DuckDB.");
  } catch (error) {
    toast(error.message, true);
  }
}

function bindEvents() {
  $("#search").addEventListener("input", applyFilters);
  ["#filter-country", "#filter-fit", "#filter-status", "#filter-portal", "#only-target"].forEach((selector) => {
    $(selector).addEventListener("change", applyFilters);
  });
  $("#refresh").addEventListener("click", refreshFiles);
  $("#refresh-inline").addEventListener("click", refreshFiles);
  $("#scrape-all").addEventListener("click", () => {
    const boxes = $$(".market-check");
    boxes.forEach((box) => { box.checked = true; });
    startScrape(boxes.map((box) => box.value));
  });
  $("#scrape-selected").addEventListener("click", () => {
    startScrape($$(".market-check:checked").map((box) => box.value));
  });
  $("#jobs-body").addEventListener("click", (event) => {
    const aiButton = event.target.closest("[data-ai]");
    if (aiButton) {
      generateWithAI(aiButton);
      return;
    }
    const button = event.target.closest("[data-apply], [data-cover]");
    if (button) {
      openApplyDialog(button.dataset.apply || button.dataset.cover, Boolean(button.dataset.cover));
    }
  });
  $("#apps-body").addEventListener("change", (event) => {
    if (event.target.matches(".status-select")) updateApplicationStatus(event.target);
  });
  $("#apply-form").addEventListener("submit", submitApplication);
  $("#dialog-close").addEventListener("click", () => $("#apply-dialog").close());
  $("#dialog-cancel").addEventListener("click", () => $("#apply-dialog").close());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("#apply-dialog").open) $("#apply-dialog").close();
  });
}

async function init() {
  bindEvents();
  await loadState();
  try {
    const status = await api("/api/scrape/status");
    if (status.running) pollScrape();
    else if (status.last) {
      const last = status.last;
      $("#scrape-status").textContent = `Last scrape: ${last.status} · ${last.result_count || 0} rows`;
      $("#scrape-dot").className = last.status === "failed" ? "dot-err" : "dot-ok";
    }
  } catch (error) {
    toast(error.message, true);
  }
}

init();
