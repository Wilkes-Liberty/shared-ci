---
section: Fixed
---
**Changelog comparison follows the regenerated merge commit's first parent when the pull_request event still carries an older base SHA.** Concurrent merges into the target no longer make a one-fragment pull request look like two. The one-fragment rule and trusted-script fetch are unchanged. (shared-ci#40)
