# GitHub Trending CLI

A CLI tool to discover, evaluate, and explore GitHub trending repositories. Zero dependencies. Designed for AI agents and humans.

[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![No Dependencies](https://img.shields.io/badge/dependencies-none-green.svg)]()

## Installation

```bash
pip install github-trending-cli

# Now available as:
github-trending
gt  # Short alias
```

Or from source:

```bash
git clone https://github.com/baba20o/github-trending-cli.git
cd github-trending-cli
pip install -e .
```

### Requirements

- **Python 3.7+** — No external packages needed
- **GitHub CLI (`gh`)** — Required for `--info`, `--tree`, `--readme`, `--deps`, `--issues`
  ```bash
  # macOS
  brew install gh
  # Windows
  winget install GitHub.cli
  # Linux
  sudo apt install gh

  gh auth login
  ```

## Quick Start

```bash
# See what's trending today
gt

# Filter by language and time range
gt -l python
gt -l rust -s weekly

# Evaluate a repo without cloning
gt -i 1          # Stats
gt --tree 1      # File structure
gt -r 1          # README
gt --deps 1      # Dependencies
gt --issues 1    # Recent issues

# Clone and explore
gt -e 1              # Clone & open in VS Code
gt --clone-nums 1,3  # Clone specific repos

# Cleanup
gt --cleanup
```

## Usage

### Browsing

```bash
gt                              # Daily trending (default)
gt -s weekly                    # Weekly trending
gt -s monthly                   # Monthly trending
gt -l python                    # Filter by language
gt -t 5                         # Top 5 only
gt --min-stars 5000             # 5k+ stars only
gt --search "llm"               # Search title/description
gt --sort stars --reverse       # Sort by stars, ascending
gt -v                           # Show repo URLs
```

### Evaluating (no clone needed)

```bash
gt -i 1                         # Info for trending repo #1
gt --info-repo owner/repo       # Info for any repo
gt --tree 1                     # File tree for #1
gt --tree 1 --tree-depth 3      # Deeper tree
gt -r 1                         # README for #1
gt -r 1 --readme-lines 50       # First 50 lines only
gt --deps 1                     # Dependencies for #1
gt --issues 1                   # Recent issues for #1
gt --issues 1 --issues-limit 20 # More issues
gt --issue 42 --issue-repo owner/repo  # Single issue detail
```

### Analysis

Score repos on health metrics (commit activity, README quality, issue response, PR merge rate):

```bash
gt --analyze                    # Health scores for top 10
gt --analyze-detail 1           # Detailed breakdown for #1
```

### Cloning

```bash
gt -c                           # Interactive clone mode
gt --clone-nums 1               # Clone #1
gt --clone-nums 1,3,5           # Clone multiple
gt --clone-nums 1-5             # Clone range
gt --clone-nums 1 --shallow     # Shallow clone (faster)
gt --clone-nums 1 --clone-dir ./repos  # Custom directory
```

### Explore Mode

```bash
gt -e 1                         # Clone & open in VS Code
gt -e 1 --editor cursor         # Use Cursor instead
gt -e 1 --show-readme           # Also display README
gt -e 1 --auto-cleanup          # Delete when editor closes
```

### Cleanup

```bash
gt --list-clones                # List cloned repos
gt --cleanup                    # Interactive cleanup
gt --cleanup-names repo1,repo2  # Remove specific repos
```

### Export

```bash
gt --csv trending.csv           # Export to CSV
gt --json trending.json         # Export to JSON
gt -l python --csv python.csv   # Filtered export
```

## Agent Mode

All evaluation commands support `--raw` for JSON output and `--output-json` for structured trending data:

```bash
# Structured JSON trending data
gt --output-json
gt -l python -t 10 --output-json

# JSON output for repo evaluation
gt --info-repo owner/repo --raw
gt --tree-repo owner/repo --raw
gt --deps-repo owner/repo --raw
gt --issues-repo owner/repo --raw

# Raw markdown README
gt --readme-repo owner/repo --readme-raw
```

### Agent Workflow Example

```bash
# 1. Discover
gt -l python -t 5 --output-json > trending.json

# 2. Evaluate (no clone needed)
gt --info-repo owner/repo --raw
gt --tree-repo owner/repo --raw
gt --readme-repo owner/repo --readme-raw
gt --deps-repo owner/repo --raw

# 3. Clone if interesting
gt --clone-nums 1 --shallow --clone-dir ./workspace

# 4. Cleanup
gt --cleanup-names repo-name --clone-dir ./workspace
```

## All Options

| Option | Description |
|--------|-------------|
| `-s`, `--since` | Time range: `daily`, `weekly`, `monthly` |
| `-l`, `--language` | Programming language filter |
| `-t`, `--top` | Number of repos to show (default: 10) |
| `--min-stars` | Minimum star count |
| `--max-stars` | Maximum star count |
| `--search` | Search in title/description |
| `--sort` | Sort by: `stars`, `name` |
| `--reverse` | Reverse sort order |
| `-v`, `--verbose` | Show repository URLs |
| `-i`, `--info` | Show stats for repo #N |
| `--info-repo` | Show stats for `owner/repo` |
| `--tree` | Show file tree for repo #N |
| `--tree-repo` | Show file tree for `owner/repo` |
| `--tree-depth` | Max depth for tree (default: 2) |
| `-r`, `--readme` | Show README for repo #N |
| `--readme-repo` | Show README for `owner/repo` |
| `--readme-lines` | Limit README to N lines |
| `--readme-raw` | Raw README markdown output |
| `--deps` | Show dependencies for repo #N |
| `--deps-repo` | Show dependencies for `owner/repo` |
| `--issues` | Show issues for repo #N |
| `--issues-repo` | Show issues for `owner/repo` |
| `--issues-limit` | Number of issues to show (default: 10) |
| `--issue` | Show single issue detail by number |
| `--issue-repo` | Repo for `--issue` |
| `-a`, `--analyze` | Health scores for trending repos |
| `--analyze-detail` | Detailed score breakdown for repo #N |
| `-c`, `--clone` | Interactive clone mode |
| `--clone-nums` | Clone by number (`"1,3,5"` or `"1-5"`) |
| `--clone-dir` | Clone directory |
| `--shallow` | Shallow clone (`--depth 1`) |
| `-e`, `--explore` | Clone and open in editor |
| `--editor` | Editor command (default: `code`) |
| `--auto-cleanup` | Remove repo when editor closes |
| `--show-readme` | Show README after cloning |
| `--cleanup` | Interactive cleanup mode |
| `--cleanup-names` | Remove repos by folder name |
| `--list-clones` | List cloned repositories |
| `--csv` | Export to CSV file |
| `--json` | Export to JSON file |
| `--output-json` | Output as JSON (for agents) |
| `--raw` | Raw JSON output for info/tree/deps/issues |
| `--no-color` | Disable colored output |
| `--list-languages` | Show available languages |
| `--clear-cache` | Clear all cached data |

## Cache

Responses are cached locally to reduce API calls.

| Data | TTL |
|------|-----|
| Trending | 1 hour |
| Repo info | 24 hours |
| README | 24 hours |
| Dependencies | 24 hours |
| Issues | 30 minutes |

Locations: `~/.cache/github-trending-cli/` (Linux/macOS) or `%LOCALAPPDATA%\github-trending-cli\cache\` (Windows).

Clear with `gt --clear-cache`.

## How It Works

1. **Trending data** from [isboyjc/github-trending-api](https://github.com/isboyjc/github-trending-api), with fallback to direct scraping of github.com/trending
2. **Repo evaluation** via [GitHub CLI](https://cli.github.com/) (`gh api`)
3. **TTL-based caching** with rate limiting to respect API limits
4. **Cloning** via `git clone` with optional `--depth 1`

## License

MIT — see [LICENSE](LICENSE).
