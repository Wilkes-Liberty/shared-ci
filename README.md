# shared-ci

Reusable GitHub Actions workflows for Wilkes & Liberty repositories.

This repository is public because several consumers are public repositories,
and a public repository cannot call a reusable workflow hosted in a private
one. It deliberately contains nothing but CI logic: no organization profile,
no deployment configuration, no infrastructure detail. If a change would add
anything beyond a workflow, its scripts, or their tests, it belongs somewhere
else.

## Workflows

### attribution.yml — check `No AI attribution`

Wilkes & Liberty work is authored by its human operators, and this workflow
keeps authorship credit that way on every pull request:

1. **Strips** removable AI credit lines from commit messages on the PR branch
   (credit trailers naming an AI author, "Generated with …" footers, the
   robot-emoji marker line) **and rewrites AI author/committer identities
   to a human** (prefer a human Co-authored-by trailer already on the
   commit — hosted Cursor Cloud Agents stamp the session initiator this
   way — else the PR opener as `login@users.noreply.github.com`, else
   Jeremy Michael Cerda `<jmcerda@users.noreply.github.com>`). Trees and
   dates are preserved. `@wilkesliberty.com` is not the strip rewrite
   default (operator lock 2026-08-22 / DEV-414). Same-repo PRs only;
   then force-with-lease pushes the cleaned tip. A merge of the base
   branch into the feature branch does not disable the rewrite — the
   first-parent chain, including that merge, is replayed. Cursor cloud
   wrappers and Bugbot `CURSOR_SUMMARY` blocks in the PR body are removed
   when present.
2. **Fails** the check if any attribution credit remains: commit messages,
   commit author/committer identities, PR title, or PR body.

It runs three ways from this one file: as a reusable workflow called by each
repository, as the organization ruleset's required workflow (the enforcement
layer a pull request cannot delete), and on this repository's own pull
requests. The header comments in
[`.github/workflows/attribution.yml`](.github/workflows/attribution.yml)
document the run modes and the trust model.

Adopt it with this caller:

```yaml
# .github/workflows/attribution.yml
name: Attribution
on:
  pull_request:
    # `edited` is load-bearing: the check scans the PR title and body, and
    # both are editable after the check has gone green.
    types: [opened, synchronize, reopened, edited]
permissions:
  contents: write        # strip force-pushes the cleaned PR branch
  pull-requests: write   # courtesy comment when trailers were stripped
concurrency:
  # LOAD-BEARING here, not decoration: a called workflow's own concurrency
  # does not apply to this run, so this block is the only thing keeping a
  # superseded duplicate run from cancelling a live one. The head SHA keeps
  # the post-strip re-run out of this group. Do not simplify.
  group: attribution-${{ github.repository }}-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}-caller
  # FALSE on purpose (issue #2): a cancelled duplicate of the required-class
  # attribution check wears a red X until someone re-runs it. Duplicate
  # deliveries queue and both finish green instead.
  cancel-in-progress: false
  # max, not the single-slot default: with `queue: single` a third delivery in
  # the same group evicts the pending run as cancelled — the same red X by
  # another door. `queue: max` is only valid alongside cancel-in-progress: false.
  queue: max
jobs:
  attribution:
    uses: Wilkes-Liberty/shared-ci/.github/workflows/attribution.yml@v1
```

### changelog-fragments.yml — one fragment per pull request

Concurrent pull requests used to insert bullets under the same
`## [Unreleased]` list in `CHANGELOG.md`. Adjacent lines conflict, the
conflict fix is another push, and the push reruns CI. A pull request
labelled `changelog` adds one file instead:

```markdown
<!-- changelog.d/20-changelog-fragments.md -->
---
section: Added
---
**What changed, in the same voice as today's release notes.** The Keep a
Changelog section (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`,
`Security`) is the front matter. The body is the bullet.
```

Unlabelled pull requests pass. That is the org's opt-in rule: an entry is required only when the pull request carries the `changelog` label. Dependabot is exempt. The structure check is not opt-in. It rejects a fragment the compiler cannot read, a duplicate or out-of-order `###` heading, and a release heading that disappeared. It runs on push to the default branch as well as on pull requests, because the bad merge only exists after the second pull request lands.

The release-finalize commit compiles the fragments into the versioned heading `release.yml` already extracts, then deletes them. Notes already under `[Unreleased]` are carried into that version section ahead of the fragment groups, and `[Unreleased]` is left empty. An `-rc.*` tag whose unreleased section is empty already falls back to `## [X.Y.Z]`.

In this repository:

```sh
python3 .github/scripts/changelog-fragments.py compile \
  --version 1.4.0 --date 2026-10-07
```

In a caller, fetch the script from the same tag the workflow is pinned to so the checker and the compiler cannot drift:

```sh
gh api "repos/Wilkes-Liberty/shared-ci/contents/.github/scripts/changelog-fragments.py?ref=v1" \
  -H "Accept: application/vnd.github.raw" > /tmp/changelog-fragments.py
python3 /tmp/changelog-fragments.py compile --version 1.4.0 --date 2026-10-07
```

This repository is the reference caller (`.github/workflows/changelog.yml`), and it calls the workflow by relative path so a change here is what CI runs. Every other repository pins the tag:

```yaml
# .github/workflows/changelog.yml
name: Changelog
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
  push:
    branches: [master]
permissions:
  contents: read
jobs:
  changelog:
    uses: Wilkes-Liberty/shared-ci/.github/workflows/changelog-fragments.yml@v1
```

A caller that already allows headings beyond the Keep a Changelog six passes them in order, after `Security`:

```yaml
jobs:
  changelog:
    uses: Wilkes-Liberty/shared-ci/.github/workflows/changelog-fragments.yml@v1
    with:
      extra-sections: Docs,Documentation,Dependencies,Tests
```

Do not adopt this in a consuming repository until `v1` points at a release that contains it. The connector's opt-out (`no-changelog` on every pull request) is a deliberate exception and is not this workflow.

## Versioning

Consumers pin the floating major tag (`@v1`). Exact releases are tagged
`vX.Y.Z` and never move. Advancing `v1` to a new release is a deliberate
maintainer action:

```sh
git tag -f v1 vX.Y.Z^{commit}
git push --force origin refs/tags/v1
git cat-file -t v1   # MUST print "commit"
```

The `^{commit}` peel is load-bearing. `vX.Y.Z` is an annotated tag, and
`git tag -f v1 vX.Y.Z` without the peel points `v1` at the annotated **tag
object**, not the commit. The organization ruleset's required-workflow rule
resolves `attribution.yml@refs/tags/v1`, and with that annotated indirection
GitHub never auto-triggers the required evaluation run — every open pull
request in the organization sits "stuck on the attribution stage" with all of
its real checks green (issue #4; hit by the v1.1.0 release on 2026-08-11 and
again by the v1.1.1 release on 2026-08-14). The `alias-guard` workflow fails
loudly if a pushed alias is ever annotated, but do not rely on the alarm:
verify the `cat-file` output before walking away.

One atomic force push, never delete-then-push — a required workflow resolving
the tag in the gap between the two fails to start, and a run that never starts
blocks its pull request with nothing visible to explain why. A breaking change
gets `v2`; note that the organization ruleset pins its own ref and must be
repointed by hand when that happens.

## Constraints

- **No merge queues** on repositories targeted by the required-workflow
  ruleset. The workflow does not handle `merge_group` events; a queued merge
  would wait on a check that never runs.
- Changes land by pull request. The test suite
  (`python3 -m unittest discover -s tests`) must pass, and this repository's
  own pull requests are subject to the attribution check like everyone else's.
