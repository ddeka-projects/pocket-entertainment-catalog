(async () => {
  const initial = { entries: [], categories: [{ id: "uncategorized", name: "Uncategorized", work_count: 0 }],
    statuses: Object.keys(STATUS), counts: {}, total: 0, page: 1, pages: 1, catalog_revision: "1" };
  check(pendingRequests[0].path === "/api/controller/claim", "Startup claims before reading data");
  mockRespond(pendingRequests[0], { token: "initial-token" });
  await flushMicrotasks();
  mockRespond(pendingRequests[1], initial);
  await flushMicrotasks();
  check(!state.loading && state.total === 0, "Empty profile loads");

  // A slow earlier search must never replace a newer filter response.
  state.search = "old";
  const older = loadWorkCatalog({ quiet: true });
  const oldRequest = pendingRequests[pendingRequests.length - 1];
  state.search = "new";
  const newer = loadWorkCatalog({ quiet: true });
  const newRequest = pendingRequests[pendingRequests.length - 1];
  check(newRequest.path.includes("search=new"), "Filters are sent to the server");
  mockRespond(newRequest, { ...initial, total: 7, catalog_revision: "3" });
  await newer;
  mockRespond(oldRequest, { ...initial, total: 99, catalog_revision: "2" });
  await older;
  check(state.total === 7 && state.catalogRevision === "3", "Stale search result ignored");

  state.page = 7;
  document.querySelector("#work-source").listeners.change[0]({ target: { value: "true" } });
  const sourceRequest = pendingRequests[pendingRequests.length - 1];
  check(sourceRequest.path.includes("source_ongoing=true") && sourceRequest.path.includes("page=1&"), "Source filter is sent to the server and resets pagination");
  check(document.querySelector("#work-source").value === "true", "Source filter keeps its selected value");
  mockRespond(sourceRequest, initial);
  await flushMicrotasks();
  const cleared = elements.clearFilters.listeners.click[0]();
  const clearRequest = pendingRequests[pendingRequests.length - 1];
  check(clearRequest.path.includes("source_ongoing=all") && state.sourceOngoingFilter === "all", "Clear filters also resets Source Ongoing");
  mockRespond(clearRequest, initial);
  await cleared;

  // Keep the opened version while a form is active; use the latest list version
  // when the user opens that record again after closing the sheet.
  const opened = { record: { id: "work-a" }, etag: "opened" };
  const latest = { record: { id: "work-a" }, etag: "latest" };
  state.entries = [latest];
  state.detailEntry = opened;
  state.sheet.entryId = "work-a";
  check(entryById("work-a").etag === "opened", "Form keeps its original version");
  state.sheet.entryId = null;
  check(entryById("work-a").etag === "latest", "Reopening uses the current version");

  // Explicit release must finish before a fresh ownership claim. Closing an
  // editing sheet keeps whole-app ownership.
  state.editor.owned = true;
  const released = releaseControl();
  const releaseRequest = pendingRequests[pendingRequests.length - 1];
  const beforeClaim = pendingRequests.length;
  const acquired = acquireEditor();
  await flushMicrotasks();
  check(pendingRequests.length === beforeClaim, "New claim waits for release");
  mockRespond(releaseRequest, { ok: true });
  await released;
  await flushMicrotasks();
  const claimRequest = pendingRequests[pendingRequests.length - 1];
  check(claimRequest.path === "/api/controller/claim", "Claim follows completed release");
  mockRespond(claimRequest, { ok: true, token: "new-token" });
  check(await acquired, "Next editor is acquired");
  check(state.editor.owned && state.editor.token === "new-token", "Late release does not clear new ownership");
  state.sheet.dirty = false;
  await closeLayer({ force: true });
  check(state.editor.owned, "Closing a sheet retains app control");

  const routed = request("/api/entries", { method: "POST", body: {}, editor: true });
  const routedRequest = pendingRequests[pendingRequests.length - 1];
  check(routedRequest.path === "/api/work/entries", "Work writes use their own API");
  check(routedRequest.options.headers["X-Page-Id"] === pageId && routedRequest.options.headers["X-Controller-Token"] === "new-token", "Reads and writes carry controller grant");
  mockRespond(routedRequest, {});
  await routed;

  const beforeUncertain = pendingRequests.length;
  const uncertain = request("/api/entries", { method: "POST", body: {} }).catch(error => error);
  pendingRequests[pendingRequests.length - 1].reject(new Error("Connection reset"));
  const uncertainError = await uncertain;
  check(uncertainError.code === "save_uncertain" && uncertainError.message.includes("may already be saved"), "Connection loss does not imply the save failed");
  check(pendingRequests.length === beforeUncertain + 1, "Uncertain saves are never retried automatically");

  const form = workFormTemplate(null);
  check(form.includes('<select name="category_id" required>'), "Category selection uses existing options");
  check(!form.includes('name="rating"') && !form.includes('name="priority"'), "Investigate has no rating or priority");
  check(form.includes('name="source_ongoing" type="checkbox">'), "New projects default Source Ongoing to unchecked");
  const priority = workPrioritySelect(null);
  check(priority.includes("required") && priority.includes('value="" disabled selected'), "Planning has no default priority");

  const legacyForm = workFormTemplate(null, { legacy: true });
  check(legacyForm.includes('data-legacy="true"') && legacyForm.includes('name="status"'), "Legacy creation explicitly selects an existing status");
  check(legacyForm.includes('name="priority"') && !legacyForm.includes('type="date"'), "Legacy form requires priority without dates");
  check(form.includes('data-action="add-legacy-work"') && !form.includes('name="status"'), "Ordinary creation offers a separate legacy flow");
  const legacyNode = mockNode('#entry-form[data-legacy="true"]');
  legacyNode.elements = { status: { value: "investigate" }, priority: { value: "high" } };
  const savedQuery = elements.sheetBody.querySelector;
  elements.sheetBody.querySelector = () => legacyNode;
  applyLegacyStatusRules();
  check(legacyNode.elements.priority.disabled && !legacyNode.elements.priority.required && legacyNode.elements.priority.value === "", "Investigate clears and disables legacy priority");
  legacyNode.elements.status.value = "completed";
  applyLegacyStatusRules();
  check(!legacyNode.elements.priority.disabled && legacyNode.elements.priority.required, "Other legacy statuses require a priority");
  elements.sheetBody.querySelector = savedQuery;

  const sourceProject = { id: "source-project", work_title: "Still releasing", kind: "project", category_id: "uncategorized",
    status: "completed", priority: "high", source_ongoing: true, dates: Object.fromEntries(Object.keys(DATE_LABELS).map(f => [f, null])) };
  check(workCardTemplate({ record: sourceProject }).includes('class="source-ongoing-badge">Source Ongoing'), "Enabled projects have a subtle card indicator");
  check(!workCardTemplate({ record: { ...sourceProject, source_ongoing: false } }).includes("Source Ongoing"), "Unflagged projects omit the indicator");
  check(!workCardTemplate({ record: { ...sourceProject, kind: "task" } }).includes("Source Ongoing"), "Tasks omit the source indicator");
  for (const status of ["completed", "discontinued"]) {
    const terminalForm = workFormTemplate({ ...sourceProject, status });
    check(terminalForm.includes('name="source_ongoing" type="checkbox" checked>'), "Terminal projects keep an editable source checkbox");
    renderWorkDetail({ record: { ...sourceProject, status }, etag: "source-version" });
    check(elements.sheetBody.innerHTML.includes("Source Ongoing") && elements.sheetBody.innerHTML.includes('data-action="edit"'), "Terminal details show Source Ongoing and allow editing");
  }
  const taskForm = workFormTemplate({ ...sourceProject, kind: "task", source_ongoing: false });
  check(taskForm.includes('source-field" hidden') && taskForm.includes('name="source_ongoing" type="checkbox" disabled>'), "Task forms hide and disable the source field");
  const sourceField = { hidden: false };
  const kindForm = { elements: { kind: { value: "task" }, source_ongoing: { checked: true, closest() { return sourceField; } } } };
  const kindTarget = { form: kindForm, matches(selector) { return selector === '#entry-form [name=kind]'; } };
  elements.sheetBody.listeners.change[0]({ target: kindTarget });
  check(sourceField.hidden && kindForm.elements.source_ongoing.disabled && !kindForm.elements.source_ongoing.checked, "Changing kind to Task hides and clears the source flag");
  kindForm.elements.kind.value = "project";
  elements.sheetBody.listeners.change[0]({ target: kindTarget });
  check(!sourceField.hidden && !kindForm.elements.source_ongoing.disabled && !kindForm.elements.source_ongoing.checked, "Changing back to Project starts with the flag off");

  // Checkbox omission means false; checked values must be sent as JSON booleans.
  const originalFormData = globalThis.FormData;
  globalThis.FormData = class {
    constructor(form) { this.values = form.values; }
    get(name) { return this.values[name] ?? null; }
  };
  for (const enabled of [false, true]) {
    state.sheet.mode = "edit";
    state.sheet.entryId = sourceProject.id;
    state.detailEntry = { record: sourceProject, etag: "source-version" };
    const editForm = mockNode("source-edit-form");
    editForm.dataset.legacy = "false";
    editForm.values = { ...sourceProject, source_ongoing: enabled ? "on" : null };
    const submitted = submitWorkEntry(editForm);
    const saveRequest = pendingRequests[pendingRequests.length - 1];
    const payload = JSON.parse(saveRequest.options.body);
    check(saveRequest.options.method === "PATCH" && saveRequest.path === "/api/work/entries/source-project", "Source changes use the normal detail endpoint");
    check(payload.source_ongoing === enabled && !Object.hasOwn(payload, "status"), "Saving a source flag sends a boolean without changing lifecycle status");
    mockRespond(saveRequest, { entry: { record: { ...sourceProject, source_ongoing: enabled }, etag: "updated-version" } });
    await flushMicrotasks();
    const refresh = pendingRequests[pendingRequests.length - 1];
    check(refresh.path.startsWith("/api/work/catalog?"), "Source changes refresh the filtered results");
    mockRespond(refresh, initial);
    await submitted;
  }
  globalThis.FormData = originalFormData;

  const denied = request("/api/work/catalog").catch(error => error);
  const deniedRequest = pendingRequests[pendingRequests.length - 1];
  mockRespond(deniedRequest, { ok: false, error: { code: "controller_transferred", message: "Taken elsewhere" } }, false);
  await denied;
  check(!state.editor.owned && !state.editor.token && !elements.controlGate.hidden && elements.application.inert, "A transferred grant blocks the full app");
  check(state.entries.length === 0, "Old page clears its visible records");

  if (typeof print === "function") print("Frontend checks passed");
  else console.log("Frontend checks passed");
})().catch(error => {
  if (typeof print === "function") { print(String(error)); quit(1); }
  else { console.error(error); process.exitCode = 1; }
});
