---
section: Added
---
- **Warn-only operator-terminology lint.** Reusable workflow
  `operator-terms.yml` and `check-operator-terms.py` annotate "human(s)" in
  Markdown, prompts and `.github/**` files. Standing rule: refer to people as
  operator(s), never human(s). Allowlisted: "human-readable", "Human
  Resources", humanize*, identifiers such as `needs-human` and
  `HUMAN-CHECKPOINTS`, third-party quotes, inline code, and lines marked
  `operator-terminology: allow`. `--strict` exists for a later switch to
  failing. (DEV-954)
