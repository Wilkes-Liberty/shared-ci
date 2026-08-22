# Changelog

All notable changes to shared-ci. One entry per merged PR.

## [Unreleased]

- **Strip rewrites AI author/committer identities before the check.** Hosted
  Cursor Cloud Agents always commit as `Cursor Agent <cursoragent@cursor.com>`
  with the session initiator as `Co-authored-by`, and there is no dashboard
  setting to change the primary author. `strip-attribution.py` previously
  only removed AI *trailers* and preserved that author, so Cursor cloud PRs
  stayed permanently red (vault#183). When author and/or committer would
  fail `check-attribution.py`'s identity scan (same patterns), strip now
  restamps that slot to a human (operator lock 2026-08-22 / DEV-414):
  prefer a human `Co-authored-by` already on the commit, else
  `STRIP_AUTHOR_*` from the PR opener in `@users.noreply.github.com`
  shape, else `Jeremy Michael Cerda <jmcerda@users.noreply.github.com>`.
  `@wilkesliberty.com` is not the strip rewrite default — company mail
  is only for identities we stamp at commit time. Trees and
  author/committer dates are preserved; an AI committer is restamped
  to the same human. AI credit trailers are then removed as before, so
  `Co-authored-by: Cursor Agent` is not left behind. The workflow also
  PATCHes the PR body to drop Cursor cloud wrapper comments and the
  trailing `cursor.com/agents` footer when those markers are present,
  leaving the human-written summary intact. The strip suite pins a
  Copilot Autofix / `copilot-swe-agent[bot]` tip rewrite (authored and
  committed as that identity with a human Co-authored-by → restamp to
  that human, check exits 0). The same Autofix author with no human
  trailer and no safe `STRIP_AUTHOR_*` stays dirty and fails closed —
  the Cursor Jeremy-noreply default is not applied to unambiguous
  Autofix/copilot. A committer-only Autofix stamp on a human author
  is restamped to that human.
  Callers stay on `@v1`; the operator retags `v1` / cuts `v1.3.0` after
  this merges.

- **Same-group deliveries queue at depth, not in a single slot.** With the
  default `queue: single`, a third delivery for the same group evicts the
  pending run as cancelled — the same required-class red X that #2 removed
  for in-progress runs. The reusable workflow and the README caller template
  now set `queue: max` alongside `cancel-in-progress: false` (the two are
  mutually exclusive with cancellation by GitHub's own validation). Surfaced
  by automated review on the caller-sync sweep; verified against the
  workflow-syntax documentation. (#2 follow-up)

## v1.2.0 — 2026-08-14

- **The alias move peels to a commit, and an annotated alias now alarms.**
  The documented `git tag -f v1 vX.Y.Z` pointed the alias at the release's
  annotated tag object, which the org required-workflow rule cannot resolve —
  every open PR org-wide then stalls on the attribution stage with its real
  checks green. Both incidents (2026-08-11 after v1.1.0, 2026-08-14 after
  v1.1.1) were by-the-book releases. The README now peels
  (`vX.Y.Z^{commit}`) and verifies with `cat-file`, and the new
  `alias-guard.yml` fails loudly whenever a pushed `vN` alias tag is
  annotated. (#4, PR #8)
- **Duplicate attribution runs queue instead of cancelling.** Two event
  deliveries for the same PR + SHA + mode landed in one concurrency group and
  `cancel-in-progress` killed one at 0–1 s — a cancelled run of a
  required-class control wears a red X until a human re-runs it. Duplicates
  now queue and both finish green, in the reusable workflow and the README
  caller template alike; the 14 thin callers sync as a follow-up. (#2, PR #9)

## v1.1.1 — 2026-08-14

- **RC Release notes fall back to the versioned heading.** `-rc.*` tags
  still prefer `[Unreleased]`. If that heading is missing or empty (the
  changelog was relabelled before the candidate), the job now reads
  `## [X.Y.Z]` for the series instead of failing. webcms `v1.49.1-rc.1`
  hit the empty-Unreleased path on 2026-08-14 (DEV-343).

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
