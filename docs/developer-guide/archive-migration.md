# Archive Migration Queue

**English** · [Русский](archive-migration.ru.md)

This fork adds a controlled, one-time archive migration workflow on top of the existing Immich-Go GUI primitives. It is deliberately separate from the Backup Monitor.

## Goal

Migrate an old, manually organized photo archive where each **first-level folder** should become one Immich album, while preserving a durable per-folder lifecycle so the operator can stop, restart, and continue without guessing what has already been uploaded.

Example:

```text
Archive root/
├── Krasnaya Polyana/
│   ├── 2018/
│   └── Video/
├── Anapa/
└── root-file.jpg
```

Queue entries are only `Krasnaya Polyana` and `Anapa`. Nested folders are folded into their first-level parent. Files directly in the archive root are counted separately and must be handled explicitly rather than silently mixed into an album.

## Reuse upstream instead of rebuilding it

The feature must reuse existing upstream components wherever possible:

- profiles and OS-keyring credentials;
- connection preflight;
- `immich-go` binary management;
- the existing `upload-folder` flag registry and command builder;
- hidden/background folder runner where appropriate;
- log directory and process-safety conventions;
- theme, navigation, widgets, and packaging.

No second credential store, no second binary downloader, and no alternate command builder should be introduced.

## Persistent lifecycle

Each first-level folder has one of these states:

- `TODO` — discovered, not prepared;
- `READY` — selected/prepared for upload;
- `UPLOADING` — active run;
- `DONE` — immich-go completed and the target Immich album was verified server-side with exactly the expected asset count;
- `PARTIAL` — processing made progress, but server-side album verification did not fully match;
- `ERROR` — upload or server-side album verification failed;
- `SKIP` — intentionally excluded.

A rescan refreshes file counts and sizes while preserving the lifecycle state for the same folder path.

State is profile-scoped in `archive_migration_state.json`, next to the existing profile configuration files.

Archive Migration does not trust an immich-go exit code or its `added to album` event counter as proof of album membership. After every successful CLI run it independently queries Immich, resolves one exact-name destination album, and requires `assetCount` to exactly match the processed asset count before persisting `DONE`. Missing, ambiguous, unreadable, empty, short, or overfull target albums remain retryable as `ERROR` or `PARTIAL`.

State schema v2 introduced this completion rule. A `DONE` entry persisted by schema v1 is reopened as `PARTIAL` on load because that older state predates server-side completion verification.

## UX target

The eventual page is a workbench, not a wizard:

1. choose archive root;
2. scan immediate child folders;
3. search/sort/filter the table;
4. hide `DONE` rows when desired;
5. select multiple folders;
6. set a shared tag and queue options;
7. run sequential uploads, one folder at a time;
8. map each selected first-level folder to an Immich album with that folder's name;
9. verify the destination album on the Immich server after a successful CLI run;
10. persist `DONE/ERROR/PARTIAL` after every folder, not only at the end of the batch.

The UI should expose current folder, queue position, progress, and the relevant log without making the user hunt through unrelated tabs.

## Initial implementation phases

### Phase A: state + scanner

- Qt-free models and persistence;
- immediate-child archive scanner;
- recursive count/size totals under each child;
- separate root-file totals;
- cancellation and progress callbacks;
- tests for lifecycle preservation across rescans.

### Phase B: page skeleton

- new Archive Migration navigation/page;
- source picker and scan action;
- sortable/filterable folder table;
- hide-DONE toggle;
- selection summary.

### Phase C: queue runner

- build one `upload-folder` plan per selected folder;
- force one-album-per-first-level-folder mapping;
- optional shared tag;
- sequential execution by default;
- stop/continue-on-error policy;
- persist state after each folder;
- live log and queue progress.

### Phase D: legacy import

Optionally import statuses from the frozen `d-global/immich-archive-manager` SQLite database. This is a migration aid only; the fork must not depend on the old application's runtime or storage format.

## Non-goals

- Replacing the existing Upload tabs.
- Replacing Backup Monitor.
- Deleting source files.
- Reimplementing profiles, API-key storage, binary management, or CLI parity.
