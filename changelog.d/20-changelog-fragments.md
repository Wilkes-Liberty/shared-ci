---
section: Added
---
**Changelog entries are fragment files, so concurrent pull requests no
longer conflict on `CHANGELOG.md`.** A pull request labelled `changelog`
adds `changelog.d/<key>-<slug>.md` instead of a bullet under
`[Unreleased]`. `changelog-fragments.py compile` folds those fragments
into a `## [X.Y.Z] - YYYY-MM-DD` section at release finalize and deletes
them. The shared release workflow still reads that versioned heading.
(shared-ci#20)
