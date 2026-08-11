# Changelog

All notable changes to shared-ci. One entry per merged PR.

## v1.1.0 — 2026-08-11

- **Adds a reusable `release.yml`.** Publishes a GitHub Release for a `v*` tag
  with notes extracted from the caller's `CHANGELOG.md`, marking `-rc.*` tags
  as prereleases so `releases/latest` keeps resolving to the newest final.
  `infra` had proved this workflow and was the only repository carrying it: on
  2026-08-11 `webcms` had 6 Releases against 82 final tags and `ui` had 3
  against 58, leaving `releases/latest` for both pointing months into the past
  and the NIST 800-171 3.4.3 change-evidence control resting on `git log`
  alone. The CHANGELOG extraction is inlined rather than shelling out to
  `scripts/changelog-section.sh`, which only `infra` has — a reusable workflow
  runs against the caller's checkout, so a script dependency would put three
  copies of it in the organization. Behaviour is byte-identical to that script,
  verified across every documented version in all three repositories.
  Refuses any ref that is not a `vMAJOR.MINOR.PATCH[-suffix]` tag — a reusable
  workflow can be invoked from any trigger, so a branch push would otherwise
  have reached `gh release create` with the branch name as the tag.

## v1.0.1 — 2026-08-02

- **The required run no longer cancels itself against the caller run.** The
  workflow-level `concurrency` group carried a static `-direct` suffix on the
  assumption that a called workflow's own concurrency is ignored. It is not:
  the block registers for `workflow_call` runs too, so the caller-invoked run
  and the ruleset-required run of the same PR and head SHA shared one group,
  and `cancel-in-progress` killed the required run — a cancelled required
  check reports neither pass nor fail, which left every clean pull request
  unmergeable. The suffix is now derived per run mode (`-called`,
  `-required`, `-self`), so the three legitimate flavors of the same PR + SHA
  never share a group. Caught by the ruleset verification probes before any
  real pull request hit it.

## v1.0.0 — 2026-08-02

- Initial release: the attribution workflow (`attribution.yml`), its two
  scripts and their test suite, converted from thirteen per-repository
  vendored copies to a single reusable implementation. The workflow now runs
  three ways from one file — as a `workflow_call` callee behind a thin
  tag-pinned caller, as the organization ruleset's required workflow, and on
  this repository's own pull requests — with the run mode established
  fail-closed by the first step, and the trusted scripts fetched from this
  repository at a ref the pull request under review cannot influence.
