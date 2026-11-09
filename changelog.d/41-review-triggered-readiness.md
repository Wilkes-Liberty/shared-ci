---
section: Fixed
---
**PR readiness diagnostics now inspect check-suite conclusions as well as
check runs.** A Copilot review or review-comment event can leave a
completed `action_required` suite with no check-run entry, so a
check-run-only view looked successful or expected-skip while GitHub still
reported an unstable merge state. The diagnostic names the
review-triggered run that needs operator attention, and the reference
caller re-checks when that workflow completes `action_required`. A
suite that is still running or has failed is reported even when its
check runs look successful. A `pending` or `requested` state stays
pending. An operator rerun keeps existing invocation guards; the job
does not approve waiting workflow runs. (shared-ci#41)
