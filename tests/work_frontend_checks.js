(async () => {
  const initial = { entries: [], categories: [{ id: "uncategorized", name: "Uncategorized", work_count: 0 }],
    statuses: Object.keys(STATUS), counts: {}, total: 0, page: 1, pages: 1, catalog_revision: "1" };
  mockRespond(pendingRequests[0], initial);
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

  // A release from the previous sheet must finish before claiming the next one.
  state.editor.owned = true;
  const released = releaseEditor();
  const releaseRequest = pendingRequests[pendingRequests.length - 1];
  const beforeClaim = pendingRequests.length;
  const acquired = acquireEditor();
  await flushMicrotasks();
  check(pendingRequests.length === beforeClaim, "New claim waits for release");
  mockRespond(releaseRequest, { ok: true });
  await released;
  await flushMicrotasks();
  const claimRequest = pendingRequests[pendingRequests.length - 1];
  check(claimRequest.path === "/api/editor/claim", "Claim follows completed release");
  mockRespond(claimRequest, { ok: true });
  check(await acquired, "Next editor is acquired");
  check(state.editor.owned, "Late release does not clear new ownership");

  const routed = request("/api/entries", { method: "POST", body: {}, editor: true });
  const routedRequest = pendingRequests[pendingRequests.length - 1];
  check(routedRequest.path === "/api/work/entries", "Work writes use their own API");
  mockRespond(routedRequest, {});
  await routed;

  const form = workFormTemplate(null);
  check(form.includes('<select name="category_id" required>'), "Category selection uses existing options");
  check(!form.includes('name="rating"') && !form.includes('name="priority"'), "Investigate has no rating or priority");
  const priority = workPrioritySelect(null);
  check(priority.includes("required") && priority.includes('value="" disabled selected'), "Planning has no default priority");

  if (typeof print === "function") print("Frontend checks passed");
  else console.log("Frontend checks passed");
})().catch(error => {
  if (typeof print === "function") { print(String(error)); quit(1); }
  else { console.error(error); process.exitCode = 1; }
});
