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

Wilkes & Liberty work is authored by its operators, and this workflow
keeps authorship credit that way on every pull request:

1. **Strips** removable AI credit lines from commit messages on the PR branch
   (credit trailers naming an AI author, "Generated with …" footers, the
   robot-emoji marker line) **and rewrites AI author/committer identities
   to a human** (prefer a human Co-authored-by trailer already on the <!-- operator-terminology: allow -->
   commit — hosted Cursor Cloud Agents stamp the session initiator this
   way — else the PR opener as `login@users.noreply.github.com`, else
   Jeremy Michael Cerda `<jmcerda@users.noreply.github.com>`). Trees and
   dates are preserved. `@wilkesliberty.com` is not the strip rewrite
   default (operator lock 2026-08-22 / DEV-414). Same-repo PRs only;
   then force-with-lease pushes the cleaned tip. A merge of the base
   branch into the feature branch does not disable the rewrite — the
   first-parent chain, including that merge, is replayed. Removable AI
   credit in the PR body (trailers, "Generated with" / robot-emoji lines,
   the `cursor.com/agents` footer, Bugbot `CURSOR_SUMMARY` blocks) is
   removed when present. `CURSOR_AGENT_PR_BODY_*` HTML comments stay so
   a later Cursor agent description update can replace its section
   instead of appending a second copy.
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

By default, unlabelled pull requests pass. That is the org's opt-in rule: an entry is required only when the pull request carries the `changelog` label. Dependabot is exempt. The structure check is not opt-in. It rejects a fragment the compiler cannot read, a duplicate or out-of-order `###` heading, and a release heading that disappeared. It runs on push to the default branch as well as on pull requests, because the bad merge only exists after the second pull request lands.

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
  pull-requests: read
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

Do not adopt new inputs in a consuming repository until `v1` points at a release that contains them. The connector's existing require-by-default policy is supported with `entry-policy: required` and `exemption-label: no-changelog`; it remains a deliberate exception to the default opt-in policy.

Set `enforce-fragments: true` after adoption to reject direct edits to the shared changelog even on an unlabelled pull request. An optional entry remains optional; this setting governs where notes go. Compilation is accepted only when the resulting changelog exactly matches the compiler output from the base changelog and every consumed fragment. An inherited handwritten-only backlog can also be compiled; an empty release is rejected. Dropping a note, changing an archive, or leaving fragments behind fails the gate.

| Input | Default | Purpose |
| --- | --- | --- |
| `entry-policy` | `opt-in` | `required` preserves a repository's existing require-by-default policy. |
| `exemption-label` | `no-changelog` | Entry exemption when policy is `required`. Dependabot stays exempt. |
| `enforce-fragments` | `false` | After adoption, disallow ordinary edits to the shared changelog. |
| `changelog-format` | `versioned` | `calendar` uses dated archives; `rolling` also accepts bare `## Unreleased`. |
| `allow-initial-release` | `false` | Permit a versioned project with no published release yet. |
| `preserve-history` | `false` | Validate pending sections without requiring old release bodies to follow current heading rules. |

`preserve-history` never permits a release compile to rewrite an archive. Existing handwritten unreleased notes are retained during adoption and included in the next compile. Correct malformed pending sections once, preserving their prose; do not reformat old releases.

Preview the exact compile without modifying either input:

```sh
python3 -I /tmp/changelog-fragments.py render --version 1.5.0 --date 2026-10-25
```

Use the same `--format`, `--extra-sections`, `--allow-initial-release`, and `--preserve-history` options for check, render, and compile. Calendar and rolling formats omit `--version` and archive by `--date`. One compile owns each archive heading; two release-finalize PRs for the same version or day are not independent.

#### Adoption and release protocol

1. Release the shared implementation and verify the published `v1` contains the required inputs and script.
2. Replace each local gate with the reusable caller, retaining its label policy and exact required status names. If branch protection requires an old job name, keep a small aggregation job with that name which fails unless the reusable workflow succeeded. Preserve label automation and add push validation on the actual default branch.
3. Add `changelog.d/.gitkeep`, contributor guidance, and `enforce-fragments: true`. Update automated note writers to create unique files. Migrate open PR notes to separate issue-keyed filenames against the adoption base.
4. Verify independent PRs merge in either order and compile all notes exactly once. Run the repository's existing checks and resolve review findings before declaring readiness.
5. At release, preview, then compile once from an up-to-date release branch. Review the generated diff and consumed files together. Existing release approval and publication gates still apply.

Fragment filenames must be unique across concurrent PRs, for example `1234-cache-expiry.md`; use distinct slugs when one issue has several PRs. Do not reuse an existing fragment filename. This removes ordinary changelog append collisions; changes to the same source file or competing release compilations can still conflict.

For callers with automatic labeling, run the reusable workflow after that job and propagate labeling failures. The shared gate reads current labels: a label written with `GITHUB_TOKEN` does not create a new `labeled` workflow run. On push or deliberately skipped labeling, allow the shared structure job to run with an explicit dependency condition.

The reusable workflow uses read-only repository and pull-request permissions and fetches its checker from the exact commit of the called workflow. Python runs in isolated mode. Consumers keep configuration and stable status aliases; parsing and compilation remain centralized.

### operator-terms.yml — warn-only terminology lint

Standing rule: refer to people as operator(s), never human(s). This workflow
annotates "human(s)" in Markdown, prompt files, files under `prompts/`,
`agents/`, `skills/` and `rules/`, and everything under `.github/`. It never
fails a build: hits, a failed script fetch and a crashed script are all
`::warning` annotations on a green check.

It skips source code (`*.py`, `*.sh` and similar, where the word is usually
an identifier), translation files (`*.po`, `*.pot`), `CHANGELOG.md`, and
vendored trees (`node_modules/`, `vendor/`, Drupal core and contrib). It
allows "human-readable", "Human Resources", humanize\*, identifiers such as
the `needs-human` label, `human_*` and `HUMAN-CHECKPOINTS`, product copy
"human editor", the rule statement "human(s)", and anything in inline code.
Mark any other keep case, such as a third-party quote, with an inline comment
on the same line:

```markdown
"Written by a human, for humans." <!-- operator-terminology: allow -->
```

In YAML, use `# operator-terminology: allow`. Run it locally from a
repository root with `python3 -I check-operator-terms.py`; `--strict` turns
hits into errors and a non-zero exit, for a later switch to enforcement.

This repository's reference caller is `.github/workflows/terminology.yml`.
Other repositories pin the tag:

```yaml
# .github/workflows/terminology.yml
name: Terminology
on:
  pull_request:
permissions:
  contents: read
jobs:
  operator-terms:
    uses: Wilkes-Liberty/shared-ci/.github/workflows/operator-terms.yml@v1
```

Do not adopt it in a consuming repository until `v1` points at a release that
contains it.

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
