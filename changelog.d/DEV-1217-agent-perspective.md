---
section: Changed
---
**Agent-perspective lint (warn-only).** `operator-terms.yml` and
`check-operator-terms.py` now flag bot-perspective language in pull request
titles, bodies, and PR, issue and review comments. `operator` and `agent`
stay allowed. File scanning of Markdown, prompts and `.github/**` remains.
The displayed job name is "Agent perspective (warn-only)"; the workflow
path and job id stay the same so existing callers keep working. (DEV-1217)
