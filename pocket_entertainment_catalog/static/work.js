"use strict";

// Work uses the shared cards, sheets, editor lease, and request helpers in app.js.
// Only this profile asks the server to search/sort/page its local SQLite records.
function setupProfile() {
  document.querySelector(`[data-profile="${isWork ? "work" : "journey"}"]`).setAttribute("aria-current", "page");
  if (!isWork) return;
  document.body.classList.add("work-profile");
  document.title = "Work · Pocket Entertainment Catalog";
  document.querySelector(".app-header h1").textContent = "Work Catalog";
  document.querySelector(".hero-copy .eyebrow").textContent = "Your editing backlog";
  document.querySelector(".orbit-label").textContent = "works";
  document.querySelector(".empty-state h2").textContent = "No works found";
  document.querySelector(".empty-state p").textContent = "Add a project or task to investigate, or adjust your filters.";
  document.querySelector("#add-entry span").textContent = "Add work";
  elements.mediaFilters.hidden = true;
  document.querySelector("#work-controls").hidden = false;
  elements.sort.querySelector('[value="journey"]').textContent = "Work order";
  const priorityOption = elements.sort.querySelector('[value="rating"]');
  priorityOption.value = "priority";
  priorityOption.textContent = "Priority";
  state.sortDirections.priority = "desc";
  elements.syncLabel.textContent = "Local storage";
  elements.syncButton.dataset.state = "unavailable";
  elements.syncButton.title = "Work changes are saved on the server.";
  elements.syncButton.setAttribute("aria-label", "Work storage status");
  for (const [id, field] of [["work-category", "categoryFilter"], ["work-kind", "kindFilter"], ["work-priority", "priorityFilter"]]) {
    document.querySelector(`#${id}`).addEventListener("change", (event) => {
      state[field] = event.target.value;
      refreshWorkFilters();
    });
  }
  document.querySelector("#manage-categories").addEventListener("click", openCategories);
  for (const [id, delta] of [["previous-page", -1], ["next-page", 1]]) {
    document.querySelector(`#${id}`).addEventListener("click", () => {
      state.page += delta;
      loadWorkCatalog({ quiet: true });
    });
  }
}

async function loadWorkCatalog({ quiet = false } = {}) {
  const generation = ++state.workRequest;
  state.refreshing = true;
  if (!quiet) renderLoading();
  const params = new URLSearchParams({
    status: state.statusFilter, category: state.categoryFilter, kind: state.kindFilter,
    priority: state.priorityFilter, search: state.search, sort: state.sort,
    direction: state.sortDirections[state.sort], page: String(state.page), page_size: "48",
  });
  try {
    const payload = await request(`/api/work/catalog?${params}`);
    if (generation !== state.workRequest) return;
    Object.assign(state, {
      entries: payload.entries, categories: payload.categories, statuses: payload.statuses,
      counts: payload.counts, total: payload.total, page: payload.page, pages: payload.pages,
      catalogRevision: payload.catalog_revision, loading: false,
    });
    renderWorkFilters();
    renderWork();
    elements.syncLabel.textContent = "Local storage";
    elements.syncButton.dataset.state = "unavailable";
    if (state.sheet.mode === "detail" && state.sheet.entryId) {
      const fresh = await request(`/api/entries/${encodeURIComponent(state.sheet.entryId)}`);
      if (generation === state.workRequest && state.sheet.mode === "detail" && state.sheet.entryId === fresh.entry.record.id) {
        renderWorkDetail(fresh.entry);
      }
    }
  } catch (error) {
    if (generation !== state.workRequest) return;
    state.loading = false;
    elements.syncLabel.textContent = "Connection issue";
    elements.syncButton.dataset.state = "error";
    if (quiet) toast(error.message, "error");
    else renderLoadError(error);
  } finally {
    if (generation === state.workRequest) state.refreshing = false;
  }
}

function refreshWorkFilters() {
  state.page = 1;
  renderWorkFilters();
  return loadWorkCatalog({ quiet: true });
}

let workSearchTimer;
function queueWorkSearch() {
  clearTimeout(workSearchTimer);
  // Invalidate a preceding response before the debounce expires.
  state.workRequest += 1;
  workSearchTimer = setTimeout(refreshWorkFilters, 220);
}

function categoryLabel(id) {
  return state.categories.find((c) => c.id === id)?.name || "Uncategorized";
}

function openWorkStorage() {
  openLayer("storage");
  setSheetHeading("Work", "Local storage");
  elements.sheetBody.innerHTML = '<p class="form-note">Your work is saved on the computer running this app. Download a backup to keep a separate copy, or export your records as JSON. Both include works in Trash.</p><p class="form-note">The app also keeps the latest seven daily snapshots, captured after the first change each day.</p><div class="detail-actions"><a class="primary-button backup-link" href="/api/work/backup" download>Download backup</a><a class="secondary-button backup-link" href="/api/work/export" download>Export JSON</a></div>';
}

function renderWorkFilters() {
  elements.statusFilters.innerHTML = STATUS_ORDER.map((value) => {
    const label = value === "all" ? "All" : value === "trash" ? "Trash" : statusInfo(value).label;
    return `<button class="filter-chip" type="button" data-status="${value}" aria-pressed="${state.statusFilter === value}">${label} · ${state.counts[value] || 0}</button>`;
  }).join("");
  const category = document.querySelector("#work-category");
  category.innerHTML = '<option value="all">All categories</option>' + state.categories.map((c) =>
    `<option value="${escapeAttribute(c.id)}">${escapeHTML(c.name)}</option>`).join("");
  category.value = state.categoryFilter;
  document.querySelector("#work-kind").value = state.kindFilter;
  document.querySelector("#work-priority").value = state.priorityFilter;
}

function renderWork() {
  elements.totalCount.textContent = String(state.counts.all || 0);
  elements.catalogSummary.textContent = [
    `${state.counts.ongoing || 0} ongoing`, `${state.counts.paused || 0} paused`,
    `${state.counts.planned || 0} planned next`, `${state.counts.investigate || 0} on your radar`,
  ].join(" · ");
  elements.grid.removeAttribute("aria-busy");
  elements.grid.innerHTML = state.entries.map(workCardTemplate).join("");
  elements.grid.hidden = state.entries.length === 0;
  elements.empty.hidden = state.entries.length !== 0;
  const start = (state.page - 1) * 48 + 1;
  const end = start + state.entries.length - 1;
  elements.resultCount.textContent = state.total > 48
    ? `${start}–${end} of ${state.total} works`
    : `${state.total} ${state.total === 1 ? "work" : "works"}`;
  document.querySelector("#work-pagination").hidden = state.pages <= 1;
  document.querySelector("#previous-page").disabled = state.page <= 1;
  document.querySelector("#next-page").disabled = state.page >= state.pages;
  document.querySelector("#page-label").textContent = `Page ${state.page} of ${state.pages}`;
}

function workCardTemplate({ record }) {
  return `<button class="entry-card${record.deleted_at ? " deleted-card" : ""}" type="button" data-entry-id="${escapeAttribute(record.id)}" data-status="${record.status}">
    <span class="card-rail" aria-hidden="true"></span><span class="card-content">
      <span class="card-topline"><span class="media-label">${escapeHTML(categoryLabel(record.category_id))}</span><span class="status-label">${record.deleted_at ? "Deleted" : statusInfo(record.status).label}</span></span>
      <h2>${escapeHTML(record.work_title)}</h2>
      ${record.unit_title ? `<span class="unit-title">${escapeHTML(record.unit_title)}</span>` : ""}
      <span class="work-badges"><span>${titleCase(record.kind)}</span>${record.priority ? `<span class="priority-badge" data-priority="${record.priority}">${titleCase(record.priority)} priority</span>` : ""}</span>
      <span class="card-footer"><span>${escapeHTML([cardDate(record), record.release_year].filter(Boolean).join(" · ") || "Date unknown")}</span></span>
    </span></button>`;
}

function renderWorkDetail(entry) {
  state.detailEntry = entry;
  const record = entry.record;
  state.sheet.mode = "detail";
  state.sheet.entryId = record.id;
  state.sheet.dirty = false;
  setSheetHeading(record.unit_title || titleCase(record.kind), record.work_title);
  const chips = [record.deleted_at ? "Deleted" : statusInfo(record.status).label,
    categoryLabel(record.category_id), titleCase(record.kind),
    record.priority ? `${titleCase(record.priority)} priority` : null, record.release_year].filter(Boolean);
  const timeline = Object.entries(DATE_LABELS).map(([field, label]) =>
    `<div class="timeline-row${record.dates[field] ? " is-known" : ""}"><span class="timeline-dot" aria-hidden="true"></span><span>${label}</span><span class="timeline-date${record.dates[field] ? "" : " is-unknown"}">${record.dates[field] ? escapeHTML(formatDate(record.dates[field])) : "Unknown"}</span></div>`).join("");
  const transitions = NEXT_STATUS[record.status].map((target) =>
    `<button class="${target === "discontinued" ? "danger-button" : target === "paused" ? "secondary-button" : "primary-button"}" type="button" data-action="transition" data-target-status="${target}">${transitionLabel(record.status, target)}</button>`).join("");
  elements.sheetBody.innerHTML = `<div class="detail-meta">${chips.map((c) => `<span class="detail-chip">${escapeHTML(c)}</span>`).join("")}</div>
    <p class="detail-notes${record.notes ? "" : " is-empty"}">${escapeHTML(record.notes || "No notes yet.")}</p>
    <div class="timeline" aria-label="Lifecycle dates">${timeline}</div>
    ${record.legacy_history ? '<p class="form-note">Backfilled history may have unknown lifecycle dates.</p>' : ""}
    <div class="detail-actions">${record.deleted_at
      ? '<button class="primary-button" type="button" data-action="restore">Restore work</button>'
      : `${transitions}<button class="secondary-button" type="button" data-action="edit">Edit details</button><button class="text-button" type="button" data-action="correct-history">Correct dates</button><button class="danger-button" type="button" data-action="delete">Delete</button>`}</div>
    <details class="correction-panel"><summary>Record information</summary><p class="form-note">ID: ${escapeHTML(record.id)}<br>Created: ${escapeHTML(formatTimestamp(record.created_at))}<br>Updated: ${escapeHTML(formatTimestamp(record.updated_at))}${record.deleted_at ? `<br>Deleted: ${escapeHTML(formatTimestamp(record.deleted_at))}` : ""}</p></details>`;
}

function workPrioritySelect(value) {
  return `<select name="priority" required><option value="" disabled${!value ? " selected" : ""}>Choose a priority</option><option value="high"${value === "high" ? " selected" : ""}>High</option><option value="low"${value === "low" ? " selected" : ""}>Low</option></select>`;
}

async function openLegacyCreate() {
  if (state.sheet.dirty && !window.confirm("Discard the unsaved changes in this form?")) return;
  if (!await acquireEditor()) return;
  if (state.sheet.mode === null) openLayer("legacy");
  else {
    state.sheet.mode = "legacy";
    state.sheet.entryId = null;
    state.sheet.dirty = false;
  }
  setSheetHeading("Historical project or task", "Add legacy work");
  elements.sheetBody.innerHTML = workFormTemplate(null, { legacy: true });
  applyLegacyStatusRules();
  bindDirtyForm();
  focusFirstInSheet();
}

function applyLegacyStatusRules() {
  const form = elements.sheetBody.querySelector('#entry-form[data-legacy="true"]');
  if (!form) return;
  const investigate = form.elements.status.value === "investigate";
  form.elements.priority.disabled = investigate;
  form.elements.priority.required = !investigate;
  if (investigate) form.elements.priority.value = "";
}

function workFormTemplate(record, { legacy = false } = {}) {
  const categories = state.categories.map((c) => `<option value="${escapeAttribute(c.id)}"${c.id === (record?.category_id || "uncategorized") ? " selected" : ""}>${escapeHTML(c.name)}</option>`).join("");
  const introduction = legacy
    ? '<p class="warning-panel">For historical work whose dates are unknown. Choose its existing status; all lifecycle dates will be left blank.</p>'
    : '<p class="form-note">New works begin in Investigate. Choose a priority when you move to Planned.</p><button class="text-button" type="button" data-action="add-legacy-work">Adding old work? Add legacy work</button>';
  return `${record ? "" : introduction}
    <form id="entry-form" data-legacy="${legacy}"><div class="form-grid">
      <label class="field full"><span>Work title <span class="required">*</span></span><input name="work_title" required maxlength="300" autocomplete="off" value="${escapeAttribute(record?.work_title || "")}"></label>
      <label class="field full"><span>Work unit</span><input name="unit_title" maxlength="200" placeholder="Season 1, Launch Trailer, a video name…" value="${escapeAttribute(record?.unit_title || "")}"><small>Optional scope within a title, creator, or account.</small></label>
      <label class="field"><span>Kind</span><select name="kind" required><option value="project">Project</option><option value="task"${record?.kind === "task" ? " selected" : ""}>Task</option></select></label>
      <label class="field"><span>Category</span><select name="category_id" required>${categories}</select><small>Categories are managed separately.</small></label>
      ${legacy ? `<label class="field"><span>Existing status</span><select name="status" required>${state.statuses.map((s) => `<option value="${s}"${s === "completed" ? " selected" : ""}>${statusInfo(s).label}</option>`).join("")}</select></label>` : ""}
      ${legacy || (record && record.status !== "investigate") ? `<label class="field"><span>Priority</span>${workPrioritySelect(record?.priority || null)}${legacy ? '<small>Investigate has no priority; other statuses require High or Low.</small>' : ""}</label>` : ""}
      <label class="field"><span>Release year</span><input name="release_year" type="number" inputmode="numeric" min="1000" max="9999" placeholder="Optional" value="${record?.release_year ?? ""}"></label>
      <label class="field full"><span>Notes</span><textarea name="notes" maxlength="20000">${escapeHTML(record?.notes || "")}</textarea></label>
    </div><div class="form-actions"><button class="primary-button" type="submit">${record ? "Save changes" : legacy ? "Add legacy work" : "Add work"}</button><button class="secondary-button" type="button" data-action="cancel-form">Cancel</button></div></form>`;
}

async function submitWorkEntry(form) {
  if (!state.editor.owned && !await acquireEditor()) return;
  const data = new FormData(form);
  const existing = state.sheet.entryId ? entryById(state.sheet.entryId) : null;
  const legacy = !existing && form.dataset.legacy === "true";
  const payload = Object.fromEntries(["work_title", "unit_title", "kind", "category_id", "release_year", "notes"].map((f) => [f, data.get(f) || null]));
  if (existing && existing.record.status !== "investigate") payload.priority = data.get("priority");
  if (legacy) {
    payload.status = data.get("status");
    payload.priority = payload.status === "investigate" ? null : data.get("priority");
  }
  await runMutation(form, async () => {
    const response = await request(existing ? `/api/entries/${encodeURIComponent(existing.record.id)}` : legacy ? "/api/work/legacy" : "/api/entries", {
      method: existing ? "PATCH" : "POST", body: payload, editor: true, etag: existing?.etag,
    });
    await upsertEntry(response.entry);
    toast(existing ? "Work updated." : legacy ? "Legacy work added with unknown dates." : "Work added to Investigate.", "success");
    await closeLayer({ force: true });
  });
}

async function openWorkHistory(recordId) {
  if (!await acquireEditor()) return;
  const entry = entryById(recordId);
  if (!entry) return releaseEditor();
  state.sheet.mode = "history";
  state.sheet.dirty = false;
  setSheetHeading(entry.record.work_title, "Correct dates");
  elements.sheetBody.innerHTML = `<form id="history-form"><p class="form-note">Correct the dates for this ${statusInfo(entry.record.status).label.toLowerCase()} work. Status changes follow the normal progression.${entry.record.legacy_history ? " All dates may remain unknown for this backfilled work." : " Every milestone through the current status is required."}</p><div class="form-grid">${Object.entries(DATE_LABELS).map(([field, label]) => {
    const allowed = STATUS_DATE_FIELDS[entry.record.status].includes(field);
    return `<label class="field"><span>${label} on</span><input name="${field}" type="date" ${allowed ? "" : "disabled"} ${allowed && !entry.record.legacy_history ? "required" : ""} value="${escapeAttribute(entry.record.dates[field] || "")}"></label>`;
  }).join("")}</div><div class="form-actions"><button class="primary-button" type="submit">Save corrected dates</button><button class="secondary-button" type="button" data-action="back-to-detail">Back</button></div></form>`;
  bindDirtyForm();
  focusFirstInSheet();
}

async function submitWorkHistory(form) {
  if (!state.editor.owned && !await acquireEditor()) return;
  const entry = entryById(state.sheet.entryId);
  const data = new FormData(form);
  await runMutation(form, async () => {
    const response = await request(`/api/entries/${encodeURIComponent(entry.record.id)}/history`, {
      method: "PUT", body: { dates: Object.fromEntries(Object.keys(DATE_LABELS).map((f) => [f, data.get(f) || null])) }, editor: true, etag: entry.etag,
    });
    await upsertEntry(response.entry);
    renderWorkDetail(response.entry);
    await releaseEditor();
    toast("Lifecycle dates corrected.", "success");
  });
}

async function openCategories() {
  if (!await acquireEditor()) return;
  try {
    const payload = await request("/api/work/categories");
    state.categories = payload.categories;
    state.categoryEditing = null;
    openLayer("categories");
    renderCategories();
  } catch (error) {
    toast(error.message, "error");
    await releaseEditor();
  }
}

function renderCategories() {
  setSheetHeading("Work", "Categories");
  const editing = state.categories.find((c) => c.id === state.categoryEditing);
  state.sheet.dirty = false;
  elements.sheetBody.innerHTML = `<p class="form-note">Create and rename categories here. Deleting a category moves all its works, including Trash, to Uncategorized.</p>
    <form id="category-form" data-category-id="${escapeAttribute(editing?.id || "")}"><label class="field"><span>${editing ? "Rename category" : "New category"}</span><input name="name" required maxlength="100" autocomplete="off" value="${escapeAttribute(editing?.name || "")}"></label><div class="form-actions"><button class="primary-button" type="submit">${editing ? "Save name" : "Create category"}</button>${editing ? '<button class="text-button" type="button" data-action="category-cancel">Cancel</button>' : ""}</div></form>
    <div class="category-list">${state.categories.map((c) => `<div class="category-row"><div><strong>${escapeHTML(c.name)}</strong><small>${c.work_count} ${c.work_count === 1 ? "work" : "works"}${c.id === "uncategorized" ? " · Default" : ""}</small></div>${c.id !== "uncategorized" ? `<div class="category-actions"><button class="secondary-button" type="button" data-action="category-rename" data-category-id="${escapeAttribute(c.id)}" aria-label="Rename ${escapeAttribute(c.name)}">Rename</button><button class="danger-button" type="button" data-action="category-delete" data-category-id="${escapeAttribute(c.id)}" aria-label="Delete ${escapeAttribute(c.name)}">Delete</button></div>` : '<span class="detail-chip">Reserved</span>'}</div>`).join("")}</div>`;
  bindDirtyForm();
  focusFirstInSheet();
}

async function submitCategory(form) {
  if (state.categoryBusy) return;
  if (!state.editor.owned && !await acquireEditor()) return;
  const id = form.dataset.categoryId;
  const category = state.categories.find((c) => c.id === id);
  state.categoryBusy = true;
  try { await runMutation(form, async () => {
    await request(`/api/work/categories${id ? `/${encodeURIComponent(id)}` : ""}`, {
      method: id ? "PATCH" : "POST", body: { name: new FormData(form).get("name") }, editor: true, etag: category?.etag,
    });
    state.categoryEditing = null;
    await loadWorkCatalog({ quiet: true });
    renderCategories();
    toast(id ? "Category renamed." : "Category created.", "success");
  }); } finally { state.categoryBusy = false; }
}

async function handleCategoryAction(action, event) {
  if (state.categoryBusy) return;
  if (state.sheet.dirty && !window.confirm("Discard the unsaved category name?")) return;
  const id = event.target.closest("[data-category-id]")?.dataset.categoryId;
  if (action === "category-rename" || action === "category-cancel") {
    state.categoryEditing = action === "category-rename" ? id : null;
    renderCategories();
    return;
  }
  const category = state.categories.find((c) => c.id === id);
  if (!category || !window.confirm(`Delete “${category.name}”? Its ${category.work_count} works will move to Uncategorized, including any in Trash.`)) return;
  if (!state.editor.owned && !await acquireEditor()) return;
  state.categoryBusy = true;
  try {
    const payload = await request(`/api/work/categories/${encodeURIComponent(id)}`, { method: "DELETE", editor: true, etag: category.etag });
    state.categories = payload.categories;
    if (state.categoryFilter === id) state.categoryFilter = "all";
    state.categoryEditing = null;
    await loadWorkCatalog({ quiet: true });
    renderCategories();
    toast("Category deleted. Its works are now Uncategorized.", "success");
  } catch (error) {
    mutationError(error);
  } finally {
    state.categoryBusy = false;
  }
}
