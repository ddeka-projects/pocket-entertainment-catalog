# Initial application design

## Product shape

This is a personal pocket catalog, not a general-purpose media database. The primary object is a **journey unit** chosen by the user: for example, a television season, a split cour, a special finale, a game, or a meaningful live-service segment. Unit titles stay deliberately flexible and editable.

The initial interface is mobile-first and uses the same application on a phone and desktop. It emphasizes quick browsing and lifecycle progress while keeping database mechanics out of the normal flow.

## Information architecture

The catalog screen has four layers:

1. **At-a-glance summary** — total active journeys plus ongoing, planned, and investigate counts.
2. **Find and narrow** — full-text search, lifecycle filters, media filters, and sorting.
3. **Journey cards** — work title, optional unit, classification, status, relevant date, year, and rating.
4. **Detail sheet** — notes/review, full lifecycle timeline, progress actions, corrections, and technical record information.

On a phone, details open as a bottom sheet. On a larger screen, the same sheet becomes a right-side panel, preserving a single interaction model across devices.

## Editing model

Normal browsing is always read-only and does not participate in lease ownership.

```text
Any readers ────────────────> GET catalog / details

Page A ── claim lease ─────> add, edit, transition, delete, restore
Page B ── claim while held ─> 423: keep browsing, try again shortly
Page A ── close / timeout ──> lease becomes available
```

The lease is intentionally short-lived and in memory:

- heartbeat every 5 seconds;
- expiry after 15 seconds;
- best-effort release when the page closes;
- identity consists of a session client ID and a unique page ID, so two tabs still contend correctly.

The lease controls the user experience, while three lower-level safeguards protect the data itself:

- **ETag / `If-Match`** rejects stale record edits;
- **cross-process file lock** serializes writers;
- **disk digest check + atomic replace** detects external edits and prevents partial JSONL writes.

## Lifecycle behavior

The normal path is intentionally strict:

```text
Investigate → Planned → Ongoing ┬→ Completed
                                └→ Discontinued
```

Each transition accepts a calendar date, not a timestamp. Terminal entries may receive an integer rating from 1 to 10. Historical imports may have unknown dates and ratings, so those fields remain nullable.

“Correct history” is a separate, less prominent action for fixing old or mistaken records without weakening the everyday sequential workflow.

## Creation defaults

Only `work_title` must be typed when adding an entry. The application supplies:

- a collision-resistant ID made from a readable title slug and a ULID derived from creation time;
- initial `investigate` status;
- the local calendar date as `investigated_on`;
- exact UTC technical timestamps;
- null optional fields;
- media type from the active media filter, the last-used value, or Anime on a first visit.

All supplied values except server-owned technical fields remain editable.

## Persistence and Git

The JSONL is the source of truth. Records retain stable file order, which also preserves the old-to-new ordering of the historical import; newly created records append to the end.

After each safe local save, the server commits just the catalog file and queues a background push. Readers can see the new local state immediately and are not held up by the network. Failed pushes remain explicit and retryable. The server never automatically merges or force-pushes divergent history.

## Trust boundary

The server is designed for one trusted private network:

- private or loopback client addresses only;
- validated `Host` header;
- same-origin state-changing requests;
- strict JSON request bodies and field allowlists;
- no public-internet exposure.

There is intentionally no account or password layer in this version. Adding remote hosting later would require real authentication, TLS, authorization, and a different persistence strategy.

## Deliberately deferred

- A finer model for live-service game segments such as Wuthering Waves, Goddess of Victory: Nikke, and Neverness to Everness.
- Cover art and third-party metadata providers.
- Rewatch/replay history, because it is not part of the stated use case.
- Manual list ranking for Planned and Investigate.
- Remote multi-writer merging. The chosen model is one canonical running server with many browser clients.

The visual design is an initial baseline: warm dark surfaces, subtle category colors, large readable titles, and restrained controls. It is meant to be iterated from actual phone and desktop use rather than treated as final.
