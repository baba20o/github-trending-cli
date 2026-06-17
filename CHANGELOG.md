# Changelog

## 1.1.0

- Add `-q`/`--query` for **general GitHub search** across all repos (not just trending), via `gh search repos`.
- Search results are normalized to the same repo schema as trending, so every evaluate/clone/export/analyze
  command works on them by number.
- Map `-l`/`--language`, `--min-stars`/`--max-stars`, and `--sort` (now also `forks`/`updated`) onto the search.
- Search results cached for 30 minutes. Clarified `--search` (filters the trending list) vs `--query`.

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
