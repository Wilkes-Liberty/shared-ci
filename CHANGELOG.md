# Changelog

All notable changes to shared-ci. One entry per merged PR.

## v1.0.0 — 2026-08-02

- Initial release: the attribution workflow (`attribution.yml`), its two
  scripts and their test suite, converted from thirteen per-repository
  vendored copies to a single reusable implementation. The workflow now runs
  three ways from one file — as a `workflow_call` callee behind a thin
  tag-pinned caller, as the organization ruleset's required workflow, and on
  this repository's own pull requests — with the run mode established
  fail-closed by the first step, and the trusted scripts fetched from this
  repository at a ref the pull request under review cannot influence.
