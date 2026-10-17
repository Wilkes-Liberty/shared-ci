---
section: Fixed
---
**Attribution no longer strips `CURSOR_AGENT_PR_BODY_*` section markers
from pull request descriptions.** Those comments are structural: the
Cursor agent uses them to replace its section in place. Removing them
made the next description update append a full second copy
(Wilkes-Liberty/agency#28 held three copies on 2026-10-17). Strip still
drops attribution trailers, "Generated with" / robot-emoji lines, the
`cursor.com/agents` footer, and Bugbot `CURSOR_SUMMARY` blocks. The
body scan ignores the exact marker comments so they cannot trip the
check; real credit in the rest of the body still fails. Callers stay
on `@v1`; the operator retags `v1` after this merges. (DEV-1009)
