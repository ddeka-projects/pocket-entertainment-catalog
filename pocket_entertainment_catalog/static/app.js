"use strict";

const MEDIA = {
  anime: { label: "Anime", color: "#eaa2b6" },
  animation: { label: "Animation", color: "#91c4e3" },
  game: { label: "Game", color: "#8bcaa3" },
  live_action_movie: { label: "Live Action Movie", short: "Movie", color: "#e8b86a" },
  live_action_series: { label: "Live Action Series", short: "Series", color: "#bca0dd" },
  manga: { label: "Manga", color: "#d8d7cf" },
  visual_novel: { label: "Visual Novel", color: "#de9c82" },
};

const STATUS = {
  investigate: { label: "Investigate", color: "#9b9fa6" },
  planned: { label: "Planned", color: "#87add2" },
  ongoing: { label: "Ongoing", color: "#75c49d" },
  completed: { label: "Completed", color: "#e8b86a" },
  discontinued: { label: "Discontinued", color: "#d9817d" },
};

const STATUS_ORDER = ["all", "ongoing", "planned", "investigate", "completed", "discontinued", "trash"];
const DATE_LABELS = {
  investigated_on: "Investigated",
  planned_on: "Planned",
  started_on: "Started",
  ended_on: "Ended",
};
const NEXT_STATUS = {
  investigate: ["planned"],
  planned: ["ongoing"],
  ongoing: ["completed", "discontinued"],
  completed: [],
  discontinued: [],
};

const elements = {
  grid: document.querySelector("#catalog-grid"),
  empty: document.querySelector("#empty-state"),
  search: document.querySelector("#catalog-search"),
  sort: document.querySelector("#catalog-sort"),
  statusFilters: document.querySelector("#status-filters"),
  mediaFilters: document.querySelector("#media-filters"),
  resultCount: document.querySelector("#results-count"),
  catalogSummary: document.querySelector("#catalog-summary"),
  totalCount: document.querySelector("#total-count"),
  add: document.querySelector("#add-entry"),
  clearFilters: document.querySelector("#clear-filters"),
  syncButton: document.querySelector("#sync-button"),
  syncLabel: document.querySelector("#sync-label"),
  layer: document.querySelector("#sheet-layer"),
  sheet: document.querySelector("#entry-sheet"),
  sheetBody: document.querySelector("#sheet-body"),
  sheetTitle: document.querySelector("#sheet-title"),
  sheetEyebrow: document.querySelector("#sheet-eyebrow"),
  sheetScrim: document.querySelector("#sheet-scrim"),
  closeSheet: document.querySelector("#close-sheet"),
  toastRegion: document.querySelector("#toast-region"),
};

const state = {
  entries: [],
  mediaTypes: Object.keys(MEDIA),
  statuses: Object.keys(STATUS),
  statusFilter: "all",
  mediaFilter: "all",
  search: "",
  sort: "journey",
  sync: { state: "pending", message: "Connecting to the catalog." },
  editor: { owned: false, claimed: false, heartbeat: null, heartbeatBusy: false },
  sheet: { mode: null, entryId: null, returnFocus: null, dirty: false },
  loading: true,
  refreshing: false,
  catalogRevision: null,
};

const clientId = persistentIdentity();
const pageId = randomIdentity("page");

class APIError extends Error {
  constructor(message, code, status) {
    super(message);
    this.name = "APIError";
    this.code = code;
    this.status = status;
  }
}

function randomIdentity(prefix) {
  if (globalThis.crypto?.randomUUID) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  const random = Math.random().toString(36).slice(2);
  return `${prefix}-${Date.now().toString(36)}-${random}`;
}

function persistentIdentity() {
  const key = "pocket-catalog-client-id";
  try {
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const created = randomIdentity("client");
    sessionStorage.setItem(key, created);
    return created;
  } catch (_error) {
    return randomIdentity("client");
  }
}

async function request(path, options = {}) {
  const method = options.method || "GET";
  const headers = { Accept: "application/json" };
  let body;

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  if (options.editor) {
    headers["X-Catalog-Client"] = clientId;
    headers["X-Catalog-Page"] = pageId;
  }
  if (options.etag) headers["If-Match"] = options.etag;

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body,
      cache: "no-store",
      credentials: "same-origin",
      keepalive: Boolean(options.keepalive),
    });
  } catch (_error) {
    throw new APIError("The catalog server could not be reached.", "network_error", 0);
  }

  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new APIError("The server returned an unreadable response.", "bad_response", response.status);
  }
  if (!response.ok || payload.ok === false) {
    const detail = payload?.error || {};
    throw new APIError(detail.message || "The request could not be completed.", detail.code, response.status);
  }
  return payload;
}

async function loadCatalog({ quiet = false } = {}) {
  if (state.refreshing) return;
  state.refreshing = true;
  if (!quiet) {
    state.loading = true;
    renderLoading();
  }
  try {
    const payload = await request("/api/catalog?deleted=include");
    state.entries = payload.entries.map((entry, index) => ({ ...entry, ordinal: index }));
    state.mediaTypes = payload.media_types;
    state.statuses = payload.statuses;
    state.sync = payload.sync;
    state.editor.claimed = Boolean(payload.editor?.claimed);
    state.catalogRevision = payload.catalog_revision;
    state.loading = false;
    renderFilters();
    render();
    if (state.sheet.mode === "detail" && state.sheet.entryId) {
      const current = entryById(state.sheet.entryId);
      if (current) renderDetail(current);
      else await closeLayer({ force: true });
    }
  } catch (error) {
    state.loading = false;
    if (quiet) toast(error.message, "error");
    else renderLoadError(error);
  } finally {
    state.refreshing = false;
  }
}

function renderLoading() {
  elements.grid.hidden = false;
  elements.grid.setAttribute("aria-busy", "true");
  elements.grid.innerHTML = `<div class="loading-grid">${"<div class=\"skeleton\"></div>".repeat(6)}</div>`;
  elements.empty.hidden = true;
}

function renderLoadError(error) {
  elements.grid.hidden = false;
  elements.grid.removeAttribute("aria-busy");
  elements.grid.innerHTML = `
    <div class="error-state">
      <strong>Couldn’t open the catalog</strong>
      <span>${escapeHTML(error.message)}</span>
      <div class="form-actions"><button class="secondary-button" data-action="retry-load" type="button">Try again</button></div>
    </div>`;
  elements.resultCount.textContent = "Catalog unavailable";
  updateSync({ state: "error", message: error.message });
}

function renderFilters() {
  const activeRecords = state.entries.filter(({ record }) => !record.deleted_at);
  const statusCounts = Object.fromEntries(state.statuses.map((status) => [status, 0]));
  let trashCount = 0;
  for (const { record } of state.entries) {
    if (record.deleted_at) trashCount += 1;
    else statusCounts[record.status] = (statusCounts[record.status] || 0) + 1;
  }

  elements.statusFilters.innerHTML = STATUS_ORDER.map((value) => {
    const label = value === "all" ? "All" : value === "trash" ? "Trash" : statusInfo(value).label;
    const count = value === "all" ? activeRecords.length : value === "trash" ? trashCount : statusCounts[value] || 0;
    return `<button class="filter-chip" type="button" data-status="${value}" aria-pressed="${state.statusFilter === value}">${escapeHTML(label)} · ${count}</button>`;
  }).join("");

  const mediaValues = ["all", ...state.mediaTypes];
  elements.mediaFilters.innerHTML = mediaValues.map((value) => {
    const label = value === "all" ? "All media" : mediaInfo(value).label;
    return `<button class="filter-chip" type="button" data-media="${value}" aria-pressed="${state.mediaFilter === value}">${escapeHTML(label)}</button>`;
  }).join("");
}

function render() {
  const active = state.entries.filter(({ record }) => !record.deleted_at);
  const statusCounts = countBy(active, ({ record }) => record.status);
  elements.totalCount.textContent = String(active.length);
  elements.catalogSummary.textContent = summaryText(statusCounts);
  updateSync(state.sync);

  const visible = visibleEntries();
  elements.grid.removeAttribute("aria-busy");
  elements.grid.innerHTML = visible.map(cardTemplate).join("");
  elements.empty.hidden = visible.length !== 0;
  elements.grid.hidden = visible.length === 0;
  const label = visible.length === 1 ? "journey" : "journeys";
  elements.resultCount.textContent = `${visible.length} ${label}`;
}

function visibleEntries() {
  const query = normalizeSearch(state.search);
  const filtered = state.entries.filter(({ record }) => {
    const isDeleted = Boolean(record.deleted_at);
    if (state.statusFilter === "trash") {
      if (!isDeleted) return false;
    } else {
      if (isDeleted) return false;
      if (state.statusFilter !== "all" && record.status !== state.statusFilter) return false;
    }
    if (state.mediaFilter !== "all" && record.media_type !== state.mediaFilter) return false;
    if (!query) return true;
    return normalizeSearch([
      record.work_title,
      record.unit_title,
      record.notes,
      record.release_year,
      mediaInfo(record.media_type).label,
      statusInfo(record.status).label,
    ].filter(Boolean).join(" ")).includes(query);
  });

  return filtered.sort((left, right) => {
    const a = left.record;
    const b = right.record;
    if (state.sort === "title") {
      return a.work_title.localeCompare(b.work_title, undefined, { sensitivity: "base", numeric: true })
        || (a.unit_title || "").localeCompare(b.unit_title || "", undefined, { sensitivity: "base", numeric: true });
    }
    if (state.sort === "updated") return b.updated_at.localeCompare(a.updated_at) || b.ordinal - a.ordinal;
    if (state.sort === "rating") {
      return (b.rating ?? -1) - (a.rating ?? -1) || b.ordinal - a.ordinal;
    }
    return b.ordinal - a.ordinal;
  });
}

function cardTemplate(entry) {
  const record = entry.record;
  const media = mediaInfo(record.media_type);
  const status = statusInfo(record.status);
  const dateLabel = cardDate(record);
  const year = record.release_year ? String(record.release_year) : "";
  const supplemental = [dateLabel, year].filter(Boolean).join(" · ") || "Date unknown";
  const rating = record.rating === null
    ? ""
    : `<span class="rating-badge" aria-label="Rated ${record.rating} out of 10">${record.rating}<span>/10</span></span>`;

  return `
    <button class="entry-card${record.deleted_at ? " deleted-card" : ""}" type="button" data-entry-id="${escapeAttribute(record.id)}" data-media="${escapeAttribute(record.media_type)}" data-status="${escapeAttribute(record.status)}">
      <span class="card-rail" aria-hidden="true"></span>
      <span class="card-content">
        <span class="card-topline">
          <span class="media-label">${escapeHTML(media.short || media.label)}</span>
          <span class="status-label">${record.deleted_at ? "Deleted" : escapeHTML(status.label)}</span>
        </span>
        <h2>${escapeHTML(record.work_title)}</h2>
        ${record.unit_title ? `<span class="unit-title">${escapeHTML(record.unit_title)}</span>` : ""}
        <span class="card-footer"><span>${escapeHTML(supplemental)}</span>${rating}</span>
      </span>
    </button>`;
}

function summaryText(counts) {
  const parts = [
    pluralCount(counts.ongoing || 0, "ongoing journey", "ongoing journeys"),
    pluralCount(counts.planned || 0, "planned next", "planned next"),
    pluralCount(counts.investigate || 0, "on your radar", "on your radar"),
  ];
  return parts.join(" · ");
}

function pluralCount(value, singular, plural) {
  return `${value} ${value === 1 ? singular : plural}`;
}

function countBy(items, selector) {
  const counts = {};
  for (const item of items) {
    const key = selector(item);
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

function normalizeSearch(value) {
  return String(value || "").normalize("NFKD").toLocaleLowerCase().trim();
}

function mediaInfo(value) {
  return MEDIA[value] || { label: titleCase(value), color: "#d8d7cf" };
}

function statusInfo(value) {
  return STATUS[value] || { label: titleCase(value), color: "#9b9fa6" };
}

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function cardDate(record) {
  if (record.deleted_at) return `Deleted ${formatTimestamp(record.deleted_at)}`;
  const field = {
    investigate: "investigated_on",
    planned: "planned_on",
    ongoing: "started_on",
    completed: "ended_on",
    discontinued: "ended_on",
  }[record.status];
  if (field && record.dates[field]) return formatDate(record.dates[field], { compact: true });
  if (record.import?.position) return `Legacy #${record.import.position}`;
  if (record.import) return "Legacy import";
  return "Date unknown";
}

function formatDate(value, { compact = false } = {}) {
  if (!value) return "Unknown";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, compact
    ? { month: "short", day: "numeric", year: "numeric" }
    : { month: "long", day: "numeric", year: "numeric" }).format(parsed);
}

function formatTimestamp(value) {
  if (!value) return "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function today() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.valueOf() - offset).toISOString().slice(0, 10);
}

function entryById(recordId) {
  return state.entries.find(({ record }) => record.id === recordId) || null;
}

function upsertEntry(entry) {
  const index = state.entries.findIndex(({ record }) => record.id === entry.record.id);
  if (index === -1) {
    const highest = state.entries.reduce((maximum, item) => Math.max(maximum, item.ordinal), -1);
    state.entries.push({ ...entry, ordinal: highest + 1 });
  } else {
    state.entries[index] = { ...entry, ordinal: state.entries[index].ordinal };
  }
  renderFilters();
  render();
}

function updateSync(sync) {
  if (!sync) return;
  state.sync = sync;
  const labels = {
    synced: "Synced",
    pending: "Push pending",
    error: "Sync issue",
    unavailable: "Local only",
  };
  elements.syncButton.dataset.state = sync.state || "pending";
  elements.syncLabel.textContent = labels[sync.state] || "Checking";
  elements.syncButton.title = sync.message || "Catalog storage status";
}

async function acquireEditor() {
  if (state.editor.owned) return true;
  try {
    await request("/api/editor/claim", {
      method: "POST",
      body: { client_id: clientId, page_id: pageId },
    });
    state.editor.owned = true;
    state.editor.claimed = true;
    startHeartbeat();
    return true;
  } catch (error) {
    if (error.code === "lease_conflict" || error.status === 423) {
      toast("Another page is editing right now. You can keep browsing and try again shortly.", "error", 5200);
    } else {
      toast(error.message, "error");
    }
    return false;
  }
}

function startHeartbeat() {
  stopHeartbeat();
  state.editor.heartbeat = window.setInterval(async () => {
    if (!state.editor.owned || state.editor.heartbeatBusy) return;
    state.editor.heartbeatBusy = true;
    try {
      await request("/api/editor/heartbeat", {
        method: "POST",
        body: { client_id: clientId, page_id: pageId },
      });
    } catch (error) {
      state.editor.owned = false;
      state.editor.claimed = error.code === "lease_conflict";
      stopHeartbeat();
      toast("The editing lease was lost. Reopen the editor before saving.", "error", 5200);
    } finally {
      state.editor.heartbeatBusy = false;
    }
  }, 5_000);
}

function stopHeartbeat() {
  if (state.editor.heartbeat !== null) {
    clearInterval(state.editor.heartbeat);
    state.editor.heartbeat = null;
  }
}

async function releaseEditor({ keepalive = false } = {}) {
  if (!state.editor.owned) return;
  stopHeartbeat();
  state.editor.owned = false;
  state.editor.claimed = false;
  try {
    await request("/api/editor/release", {
      method: "POST",
      body: { client_id: clientId, page_id: pageId },
      keepalive,
    });
  } catch (_error) {
    // The lease expires automatically after 15 seconds if release cannot reach the server.
  }
}

function openLayer(mode, recordId = null) {
  state.sheet.mode = mode;
  state.sheet.entryId = recordId;
  state.sheet.returnFocus = document.activeElement;
  state.sheet.dirty = false;
  elements.layer.hidden = false;
  document.body.classList.add("sheet-open");
  window.requestAnimationFrame(() => focusFirstInSheet());
}

async function closeLayer({ force = false } = {}) {
  if (!force && state.sheet.dirty && !window.confirm("Discard the unsaved changes in this form?")) return;
  const returnFocus = state.sheet.returnFocus;
  elements.layer.hidden = true;
  document.body.classList.remove("sheet-open");
  state.sheet.mode = null;
  state.sheet.entryId = null;
  state.sheet.returnFocus = null;
  state.sheet.dirty = false;
  elements.sheetBody.innerHTML = "";
  await releaseEditor();
  if (returnFocus instanceof HTMLElement && document.contains(returnFocus)) returnFocus.focus();
}

function focusFirstInSheet() {
  const preferred = elements.sheet.querySelector("input:not([type=hidden]), button:not(#close-sheet), select, textarea");
  (preferred || elements.closeSheet).focus();
}

function setSheetHeading(eyebrow, title) {
  elements.sheetEyebrow.textContent = eyebrow;
  elements.sheetTitle.textContent = title;
}

function openDetail(recordId) {
  const entry = entryById(recordId);
  if (!entry) return;
  openLayer("detail", recordId);
  renderDetail(entry);
}

function renderDetail(entry) {
  const record = entry.record;
  const media = mediaInfo(record.media_type);
  const status = statusInfo(record.status);
  state.sheet.mode = "detail";
  state.sheet.entryId = record.id;
  state.sheet.dirty = false;
  setSheetHeading(record.unit_title || media.label, record.work_title);

  const chips = [
    `<span class="detail-chip" data-status="${record.deleted_at ? "deleted" : escapeAttribute(record.status)}">${record.deleted_at ? "Deleted" : escapeHTML(status.label)}</span>`,
    `<span class="detail-chip" data-media="${escapeAttribute(record.media_type)}">${escapeHTML(media.label)}</span>`,
    record.release_year ? `<span class="detail-chip">${record.release_year}</span>` : "",
    record.rating !== null ? `<span class="detail-chip">${record.rating} / 10</span>` : "",
  ].filter(Boolean).join("");

  const timeline = Object.entries(DATE_LABELS).map(([field, label]) => {
    const value = record.dates[field];
    return `<div class="timeline-row${value ? " is-known" : ""}">
      <span class="timeline-dot" aria-hidden="true"></span>
      <span>${label}</span>
      <span class="timeline-date${value ? "" : " is-unknown"}">${value ? escapeHTML(formatDate(value)) : "Unknown"}</span>
    </div>`;
  }).join("");

  const transitions = record.deleted_at ? "" : NEXT_STATUS[record.status].map((target) => {
    const labels = {
      planned: "Move to planned",
      ongoing: "Start journey",
      completed: "Mark completed",
      discontinued: "Discontinue",
    };
    const className = target === "discontinued" ? "danger-button" : "primary-button";
    return `<button class="${className}" type="button" data-action="transition" data-target-status="${target}">${labels[target]}</button>`;
  }).join("");

  const activeActions = record.deleted_at
    ? `<button class="primary-button" type="button" data-action="restore">Restore entry</button>`
    : `${transitions}<button class="secondary-button" type="button" data-action="edit">Edit details</button><button class="text-button" type="button" data-action="correct-history">Correct history</button><button class="danger-button" type="button" data-action="delete">Delete</button>`;

  elements.sheetBody.innerHTML = `
    <div class="detail-meta">${chips}</div>
    <p class="detail-notes${record.notes ? "" : " is-empty"}">${record.notes ? escapeHTML(record.notes) : "No notes yet."}</p>
    <div class="timeline" aria-label="Lifecycle dates">${timeline}</div>
    ${record.import ? `<p class="form-note">Backfilled from the ${escapeHTML(record.import.source)} sheet${record.import.position ? ` at legacy position ${record.import.position}` : ""}. Unknown lifecycle dates are intentionally left blank.</p>` : ""}
    <div class="detail-actions">${activeActions}</div>
    <details class="correction-panel">
      <summary>Record information</summary>
      <p class="form-note">ID: ${escapeHTML(record.id)}<br>Created: ${escapeHTML(formatTimestamp(record.created_at))}<br>Updated: ${escapeHTML(formatTimestamp(record.updated_at))}${record.deleted_at ? `<br>Deleted: ${escapeHTML(formatTimestamp(record.deleted_at))}` : ""}</p>
    </details>`;
}

async function openCreate() {
  if (!await acquireEditor()) return;
  openLayer("create");
  setSheetHeading("New journey", "Add to your radar");
  elements.sheetBody.innerHTML = entryFormTemplate(null);
  bindDirtyForm();
}

async function openEdit(recordId) {
  if (!await acquireEditor()) return;
  const entry = entryById(recordId);
  if (!entry) {
    await releaseEditor();
    toast("That journey is no longer available.", "error");
    return;
  }
  state.sheet.mode = "edit";
  state.sheet.entryId = recordId;
  state.sheet.dirty = false;
  setSheetHeading("Edit journey", entry.record.work_title);
  elements.sheetBody.innerHTML = entryFormTemplate(entry.record);
  bindDirtyForm();
  focusFirstInSheet();
}

function entryFormTemplate(record) {
  const isCreate = record === null;
  const selectedMedia = record?.media_type || preferredMediaType();
  const mediaOptions = state.mediaTypes.map((value) => `<option value="${value}"${selectedMedia === value ? " selected" : ""}>${escapeHTML(mediaInfo(value).label)}</option>`).join("");
  const ratingField = record && ["completed", "discontinued"].includes(record.status)
    ? `<fieldset class="field full fieldset-reset"><legend>Rating</legend>${ratingPicker(record.rating)}</fieldset>`
    : "";
  return `
    ${isCreate ? `<p class="form-note">Only the work title needs to be typed. New entries begin in <strong>Investigate</strong> with today’s date; the other details can be adjusted now or later.</p>` : ""}
    <form id="entry-form">
      <div class="form-grid">
        <label class="field full">
          <span>Work title <span class="required" aria-hidden="true">*</span></span>
          <input name="work_title" maxlength="300" required autocomplete="off" value="${escapeAttribute(record?.work_title || "")}">
        </label>
        <label class="field full">
          <span>Journey unit</span>
          <input name="unit_title" maxlength="200" autocomplete="off" placeholder="Season 2, Part 1, Finale…" value="${escapeAttribute(record?.unit_title || "")}">
          <small>Optional and flexible. Use whatever best describes this continuous journey.</small>
        </label>
        <label class="field">
          <span>Media type</span>
          <select name="media_type" required>
            ${mediaOptions}
          </select>
        </label>
        <label class="field">
          <span>Release year</span>
          <input name="release_year" type="number" inputmode="numeric" min="1000" max="9999" placeholder="Optional" value="${escapeAttribute(record?.release_year ?? "")}">
        </label>
        <label class="field full">
          <span>${record && ["completed", "discontinued"].includes(record.status) ? "Review / final impression" : "Notes"}</span>
          <textarea name="notes" maxlength="20000" placeholder="Anything you want to remember…">${escapeHTML(record?.notes || "")}</textarea>
        </label>
        ${ratingField}
      </div>
      <div class="form-actions">
        <button class="primary-button" type="submit">${isCreate ? "Add journey" : "Save changes"}</button>
        <button class="secondary-button" type="button" data-action="cancel-form">Cancel</button>
      </div>
    </form>`;
}

function ratingPicker(selected = null, name = "rating") {
  const values = [null, ...Array.from({ length: 10 }, (_, index) => index + 1)];
  return `<div class="rating-picker">${values.map((rating) => `
    <label title="${rating === null ? "Unrated" : `${rating} out of 10`}"><input type="radio" name="${name}" value="${rating ?? ""}"${selected === rating ? " checked" : ""}><span>${rating ?? "—"}</span></label>`).join("")}</div>`;
}

function preferredMediaType() {
  if (state.mediaFilter !== "all" && state.mediaTypes.includes(state.mediaFilter)) {
    return state.mediaFilter;
  }
  try {
    const remembered = localStorage.getItem("pocket-catalog-last-media");
    if (remembered && state.mediaTypes.includes(remembered)) return remembered;
  } catch (_error) {
    // Storage can be unavailable in strict privacy modes.
  }
  return state.mediaTypes.includes("anime") ? "anime" : state.mediaTypes[0];
}

function rememberMediaType(value) {
  try {
    localStorage.setItem("pocket-catalog-last-media", value);
  } catch (_error) {
    // Remembering the convenience default is optional.
  }
}

async function openTransition(recordId, target) {
  if (!await acquireEditor()) return;
  const entry = entryById(recordId);
  if (!entry) {
    await releaseEditor();
    toast("That journey is no longer available.", "error");
    return;
  }
  const labels = {
    planned: "Move to planned",
    ongoing: "Start journey",
    completed: "Complete journey",
    discontinued: "Discontinue journey",
  };
  state.sheet.mode = "transition";
  state.sheet.entryId = recordId;
  state.sheet.dirty = false;
  setSheetHeading(entry.record.unit_title || "Lifecycle update", labels[target]);
  const terminal = ["completed", "discontinued"].includes(target);
  elements.sheetBody.innerHTML = `
    <form id="transition-form" data-target-status="${target}">
      <p class="form-note">${escapeHTML(entry.record.work_title)} will move from <strong>${escapeHTML(statusInfo(entry.record.status).label)}</strong> to <strong>${escapeHTML(statusInfo(target).label)}</strong>.</p>
      <div class="form-grid">
        <label class="field full">
          <span>${target === "ongoing" ? "Started on" : target === "planned" ? "Planned on" : "Ended on"}</span>
          <input name="on" type="date" required value="${today()}">
        </label>
        ${terminal ? `<fieldset class="field full fieldset-reset"><legend>Rating <small>(optional)</small></legend>${ratingPicker(null)}</fieldset>` : ""}
        ${terminal ? `<label class="field full"><span>Final impression</span><textarea name="notes" maxlength="20000" placeholder="How did this journey leave you feeling?">${escapeHTML(entry.record.notes || "")}</textarea></label>` : ""}
      </div>
      <div class="form-actions">
        <button class="${target === "discontinued" ? "danger-button" : "primary-button"}" type="submit">${labels[target]}</button>
        <button class="secondary-button" type="button" data-action="back-to-detail">Back</button>
      </div>
    </form>`;
  bindDirtyForm();
  focusFirstInSheet();
}

async function openHistoryCorrection(recordId) {
  if (!await acquireEditor()) return;
  const entry = entryById(recordId);
  if (!entry) {
    await releaseEditor();
    toast("That journey is no longer available.", "error");
    return;
  }
  const record = entry.record;
  state.sheet.mode = "history";
  state.sheet.entryId = recordId;
  state.sheet.dirty = false;
  setSheetHeading(record.unit_title || "Lifecycle", "Correct history");
  const statusOptions = state.statuses.map((value) => `<option value="${value}"${record.status === value ? " selected" : ""}>${escapeHTML(statusInfo(value).label)}</option>`).join("");
  const dates = Object.entries(DATE_LABELS).map(([field, label]) => `
    <label class="field">
      <span>${label} on</span>
      <input name="${field}" type="date" value="${escapeAttribute(record.dates[field] || "")}">
    </label>`).join("");
  elements.sheetBody.innerHTML = `
    <form id="history-form">
      <p class="warning-panel">This is for correcting backfilled or mistaken history. Normal progress should use the lifecycle buttons so states stay sequential.</p>
      <div class="form-grid">
        <label class="field full"><span>Status</span><select name="status">${statusOptions}</select></label>
        ${dates}
        <fieldset class="field full fieldset-reset"><legend>Rating</legend>${ratingPicker(record.rating)}</fieldset>
      </div>
      <div class="form-actions">
        <button class="primary-button" type="submit">Save corrected history</button>
        <button class="secondary-button" type="button" data-action="back-to-detail">Back</button>
      </div>
    </form>`;
  applyHistoryStatusRules();
  bindDirtyForm();
  focusFirstInSheet();
}

function applyHistoryStatusRules() {
  const form = elements.sheetBody.querySelector("#history-form");
  if (!form) return;
  const status = form.elements.status.value;
  const permitted = {
    investigate: ["investigated_on"],
    planned: ["investigated_on", "planned_on"],
    ongoing: ["investigated_on", "planned_on", "started_on"],
    completed: Object.keys(DATE_LABELS),
    discontinued: Object.keys(DATE_LABELS),
  }[status];
  for (const field of Object.keys(DATE_LABELS)) {
    const input = form.elements[field];
    const enabled = permitted.includes(field);
    input.disabled = !enabled;
    if (!enabled) input.value = "";
  }
  const ratingInputs = form.querySelectorAll("input[name=rating]");
  const terminal = ["completed", "discontinued"].includes(status);
  for (const input of ratingInputs) {
    input.disabled = !terminal;
    if (!terminal) input.checked = false;
  }
}

function bindDirtyForm() {
  const form = elements.sheetBody.querySelector("form");
  if (!form) return;
  form.addEventListener("input", () => { state.sheet.dirty = true; });
  form.addEventListener("change", () => { state.sheet.dirty = true; });
}

async function submitEntryForm(form) {
  if (!state.editor.owned && !await acquireEditor()) return;
  const data = new FormData(form);
  const payload = {
    work_title: data.get("work_title"),
    unit_title: data.get("unit_title") || null,
    media_type: data.get("media_type"),
    release_year: data.get("release_year") || null,
    notes: data.get("notes") || null,
  };
  rememberMediaType(payload.media_type);
  const existing = state.sheet.entryId ? entryById(state.sheet.entryId) : null;
  if (existing && ["completed", "discontinued"].includes(existing.record.status)) {
    payload.rating = data.get("rating") || null;
  }
  await runMutation(form, async () => {
    const response = existing
      ? await request(`/api/entries/${encodeURIComponent(existing.record.id)}`, { method: "PATCH", body: payload, editor: true, etag: existing.etag })
      : await request("/api/entries", { method: "POST", body: payload, editor: true });
    upsertEntry(response.entry);
    updateSync(response.sync);
    state.sheet.dirty = false;
    toast(existing ? "Journey updated." : "Journey added to Investigate.", "success");
    await closeLayer({ force: true });
  });
}

async function submitTransition(form) {
  if (!state.editor.owned && !await acquireEditor()) return;
  const entry = entryById(state.sheet.entryId);
  if (!entry) return;
  const data = new FormData(form);
  const target = form.dataset.targetStatus;
  const payload = { status: target, on: data.get("on") };
  if (["completed", "discontinued"].includes(target)) {
    payload.rating = data.get("rating") || null;
    payload.notes = data.get("notes") || null;
  }
  await runMutation(form, async () => {
    const response = await request(`/api/entries/${encodeURIComponent(entry.record.id)}/transition`, {
      method: "POST", body: payload, editor: true, etag: entry.etag,
    });
    upsertEntry(response.entry);
    updateSync(response.sync);
    state.sheet.dirty = false;
    renderDetail(response.entry);
    await releaseEditor();
    toast(`Journey moved to ${statusInfo(target).label}.`, "success");
  });
}

async function submitHistory(form) {
  if (!state.editor.owned && !await acquireEditor()) return;
  const entry = entryById(state.sheet.entryId);
  if (!entry) return;
  const data = new FormData(form);
  const status = data.get("status");
  const payload = {
    status,
    dates: Object.fromEntries(Object.keys(DATE_LABELS).map((field) => [field, data.get(field) || null])),
    rating: ["completed", "discontinued"].includes(status) ? data.get("rating") || null : null,
  };
  await runMutation(form, async () => {
    const response = await request(`/api/entries/${encodeURIComponent(entry.record.id)}/history`, {
      method: "PUT", body: payload, editor: true, etag: entry.etag,
    });
    upsertEntry(response.entry);
    updateSync(response.sync);
    state.sheet.dirty = false;
    renderDetail(response.entry);
    await releaseEditor();
    toast("Lifecycle history corrected.", "success");
  });
}

async function deleteEntry() {
  const entry = entryById(state.sheet.entryId);
  if (!entry || !window.confirm(`Move “${entry.record.work_title}” to Trash?`)) return;
  if (!await acquireEditor()) return;
  try {
    const response = await request(`/api/entries/${encodeURIComponent(entry.record.id)}`, {
      method: "DELETE", editor: true, etag: entry.etag,
    });
    upsertEntry(response.entry);
    updateSync(response.sync);
    toast("Journey moved to Trash.", "success");
    await closeLayer({ force: true });
  } catch (error) {
    mutationError(error);
  }
}

async function restoreEntry() {
  const entry = entryById(state.sheet.entryId);
  if (!entry || !await acquireEditor()) return;
  try {
    const response = await request(`/api/entries/${encodeURIComponent(entry.record.id)}/restore`, {
      method: "POST", body: {}, editor: true, etag: entry.etag,
    });
    upsertEntry(response.entry);
    updateSync(response.sync);
    toast("Journey restored.", "success");
    await closeLayer({ force: true });
  } catch (error) {
    mutationError(error);
  }
}

async function runMutation(form, operation) {
  const buttons = [...form.querySelectorAll("button")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    await operation();
  } catch (error) {
    mutationError(error);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function mutationError(error) {
  if (["edit_conflict", "catalog_changed"].includes(error.code) || error.status === 412) {
    toast("This entry changed elsewhere. The catalog is being reloaded before you edit again.", "error", 6000);
    loadCatalog({ quiet: true });
    closeLayer({ force: true });
    return;
  }
  if (["lease_expired", "lease_conflict"].includes(error.code)) {
    state.editor.owned = false;
    stopHeartbeat();
  }
  toast(error.message || "The change could not be saved.", "error", 5400);
}

async function retrySync() {
  if (state.sync.state === "unavailable") {
    toast(state.sync.message || "Git synchronization is not configured yet.");
    return;
  }
  if (state.sync.state === "synced") {
    toast(state.sync.message || "The catalog is synchronized.", "success");
    return;
  }
  if (!await acquireEditor()) return;
  try {
    const payload = await request("/api/sync/retry", { method: "POST", body: {}, editor: true });
    updateSync(payload.sync);
    toast("Synchronization retry started.");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    if (state.sheet.mode === null) await releaseEditor();
  }
}

function toast(message, kind = "info", duration = 3600) {
  const item = document.createElement("div");
  item.className = `toast ${kind}`;
  item.textContent = message;
  elements.toastRegion.append(item);
  window.setTimeout(() => item.remove(), duration);
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  })[character]);
}

function escapeAttribute(value) {
  return escapeHTML(value).replace(/`/g, "&#96;");
}

elements.statusFilters.addEventListener("click", (event) => {
  const button = event.target.closest("[data-status]");
  if (!button) return;
  state.statusFilter = button.dataset.status;
  renderFilters();
  render();
});

elements.mediaFilters.addEventListener("click", (event) => {
  const button = event.target.closest("[data-media]");
  if (!button) return;
  state.mediaFilter = button.dataset.media;
  renderFilters();
  render();
});

elements.search.addEventListener("input", () => {
  state.search = elements.search.value;
  render();
});

elements.sort.addEventListener("change", () => {
  state.sort = elements.sort.value;
  render();
});

elements.grid.addEventListener("click", (event) => {
  const retry = event.target.closest("[data-action=retry-load]");
  if (retry) {
    loadCatalog();
    return;
  }
  const card = event.target.closest("[data-entry-id]");
  if (card) openDetail(card.dataset.entryId);
});

elements.sheetBody.addEventListener("click", async (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  if (action === "edit") await openEdit(state.sheet.entryId);
  if (action === "transition") await openTransition(state.sheet.entryId, event.target.closest("[data-target-status]").dataset.targetStatus);
  if (action === "correct-history") await openHistoryCorrection(state.sheet.entryId);
  if (action === "delete") await deleteEntry();
  if (action === "restore") await restoreEntry();
  if (action === "back-to-detail" || action === "cancel-form") {
    if (state.sheet.dirty && !window.confirm("Discard the unsaved changes in this form?")) return;
    const entry = entryById(state.sheet.entryId);
    if (entry) renderDetail(entry);
    await releaseEditor();
  }
});

elements.sheetBody.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!event.target.reportValidity()) return;
  if (event.target.id === "entry-form") await submitEntryForm(event.target);
  if (event.target.id === "transition-form") await submitTransition(event.target);
  if (event.target.id === "history-form") await submitHistory(event.target);
});

elements.sheetBody.addEventListener("change", (event) => {
  if (event.target.matches("#history-form [name=status]")) applyHistoryStatusRules();
});

elements.add.addEventListener("click", openCreate);
elements.closeSheet.addEventListener("click", () => closeLayer());
elements.sheetScrim.addEventListener("click", () => closeLayer());
elements.syncButton.addEventListener("click", retrySync);
elements.clearFilters.addEventListener("click", () => {
  state.statusFilter = "all";
  state.mediaFilter = "all";
  state.search = "";
  elements.search.value = "";
  renderFilters();
  render();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "/" && elements.layer.hidden && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
    event.preventDefault();
    elements.search.focus();
    return;
  }
  if (event.key === "Escape" && !elements.layer.hidden) {
    event.preventDefault();
    closeLayer();
    return;
  }
  if (event.key === "Tab" && !elements.layer.hidden) trapSheetFocus(event);
});

function trapSheetFocus(event) {
  const focusable = [...elements.sheet.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), details > summary")]
    .filter((element) => element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

window.addEventListener("pagehide", () => {
  if (!state.editor.owned) return;
  stopHeartbeat();
  fetch("/api/editor/release", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: clientId, page_id: pageId }),
    keepalive: true,
    credentials: "same-origin",
  }).catch(() => {});
  state.editor.owned = false;
});

window.setInterval(async () => {
  try {
    const payload = await request("/api/state");
    updateSync(payload.sync);
    state.editor.claimed = state.editor.owned || Boolean(payload.editor?.claimed);
    if (
      payload.catalog_revision !== state.catalogRevision
      && !state.editor.owned
      && !["create", "edit", "transition", "history"].includes(state.sheet.mode)
    ) {
      await loadCatalog({ quiet: true });
    }
  } catch (_error) {
    updateSync({ state: "error", message: "The catalog server could not be reached." });
  }
}, 10_000);

loadCatalog();
