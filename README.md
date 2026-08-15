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
   robot-emoji marker line) and force-with-lease pushes the cleaned tip —
   same-repo PRs only.
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
