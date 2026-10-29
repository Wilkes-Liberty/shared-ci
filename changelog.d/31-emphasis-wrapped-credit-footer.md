---
section: Fixed
---
**Attribution strip now removes emphasis-wrapped credit footers and the
orphaned `---` above them.** Hosted agents wrap the credit line in
`_…_`; strip's `^\s*` anchor missed that while the check still flagged
it, so vault PRs stayed red after a cleanup that could have worked
(shared-ci#31). The ruleset-required run still does not receive
`pull_request: edited`; that GitHub platform limit is documented in the
workflow header. (shared-ci#31)
