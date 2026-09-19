# Fork Maintenance Policy

[English](fork-maintenance.md) · [Русский](fork-maintenance.ru.md)

This document defines how `d-global/immich-go-gui` is maintained as a public fork of `shitan198u/immich-go-gui`.

## Principles

1. **Keep upstream history intact.** Do not rewrite upstream commits or remove upstream authorship/license notices.
2. **Keep `master` close to upstream.** Fork-specific work is developed in feature branches and merged only after tests pass.
3. **Reuse upstream components.** New features should integrate with existing profiles, keyring, command builder, runner, logging, theme, packaging, and CI instead of duplicating them.
4. **No secrets in Git.** Never commit API keys, private URLs, local credentials, personal archive contents, or machine-specific secrets.
5. **Bilingual fork documentation.** New user-facing fork documentation must be available in English and Russian. English remains the canonical language for code, identifiers, commit messages, and upstream-facing pull requests.
6. **Small reviewable changes.** Prefer focused commits and draft PRs over large unreviewed rewrites.

## Branches

- `master` — stable fork baseline, periodically synchronized with upstream `master`.
- `feature/*` — active fork features.
- `fix/*` — focused bug fixes.
- `docs/*` — documentation-only work when a dedicated branch is useful.

Current Archive Migration development branch:

`feature/archive-migration-queue`

## Upstream synchronization

The fork should periodically sync from:

`shitan198u/immich-go-gui:master`

Recommended sequence:

1. review upstream changes and release notes;
2. update fork `master` from upstream;
3. run the upstream test suite unchanged;
4. update active feature branches from the refreshed fork `master`;
5. resolve conflicts in the fork feature code, not by modifying unrelated upstream behavior;
6. rerun tests on Linux/macOS/Windows where CI supports it.

## Pull requests

Fork feature PRs should:

- target `master`;
- start as Draft while incomplete;
- use Conventional Commit style for commits and titles;
- describe user-visible changes in English and Russian when the change is fork-specific;
- include tests for new core behavior;
- update paired English/Russian docs for user-visible features.

Upstream contribution PRs to `shitan198u/immich-go-gui` should follow upstream contribution rules and be written primarily in English.

## Releases

A fork release should only be published after:

- feature PR merged into fork `master`;
- CI green;
- Windows package tested on a real machine;
- migration workflow tested on a non-critical sample archive;
- English and Russian release notes prepared;
- upstream attribution and MIT license preserved.

Release notes should clearly separate:

- upstream version/base;
- fork-specific features;
- known limitations;
- compatibility with tested `immich-go` and Immich versions.

## Documentation language policy

For fork-specific user-facing content:

- English file: `name.md`
- Russian file: `name.ru.md`
- both files should link to each other near the top;
- functionality, warnings, and limitations must match between languages;
- screenshots may be shared when the UI is language-neutral, otherwise provide localized screenshots later when UI localization exists.

Developer-only internal notes may remain English when they are purely implementation detail, but public feature design and release documentation should have a Russian companion.

## Security and privacy

Never commit:

- Immich API keys or admin keys;
- private server tokens;
- local archive filenames when they expose private information unnecessarily;
- personal photos or exported metadata;
- database files containing user state;
- logs that may contain secrets or private paths unless sanitized.

Use upstream keyring/env secret handling rather than inventing a fork-specific secret mechanism.

## Ownership

- Upstream project and original authorship remain credited to `shitan198u/immich-go-gui` and its contributors.
- Fork-specific maintenance and additions are handled under `d-global`.
- The MIT license remains in force for the fork.
