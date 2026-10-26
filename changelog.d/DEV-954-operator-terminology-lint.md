---
section: Added
---
- **Warn-only operator-terminology lint.** Reusable workflow
  `operator-terms.yml` and `check-operator-terms.py` annotate "human(s)" in
  Markdown, prompts and `.github/**` files, with `terminology.yml` as this
  repository's reference caller. The job never fails: hits, a failed script
  fetch and a crashed script are warnings. Source code, `*.po`/`*.pot` files
  and `CHANGELOG.md` are skipped. Keep cases such as "human-readable",
  "Human Resources", the `needs-human` label, `HUMAN-CHECKPOINTS` and
  "human editor" are allowed, and any line can opt out with an
  `operator-terminology: allow` comment. `--strict` exists for a later
  switch to failing. (DEV-954)
