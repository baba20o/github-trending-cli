# Changelog

## 1.0.2

- Add `--issue` and `--issue-repo` flags to fetch full details of a single issue.
- Displays issue body, metadata, labels, and first 5 comments.
- Supports both issues and PRs (falls back to PR if issue not found).

## 1.0.1

- Fix `--no-color` so output is actually uncolored.
- Harden `--cleanup-names` to reject path traversal and skip non-git directories.
- Use OS-appropriate cache directory (supports `XDG_CACHE_HOME` and Windows `LOCALAPPDATA`).

## 1.0.0

- Initial release.
