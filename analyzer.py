#!/usr/bin/env python3
"""
GitHub Repository Analyzer

Scores trending repositories based on health metrics:
- Commit activity
- Issue/PR response times
- README quality
- Maintenance status

No cloning required - uses GitHub API only.
"""

import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


# =============================================================================
# Configuration
# =============================================================================

def load_env_file():
    """Load .env file from project root or current directory."""
    # Try multiple locations
    locations = [
        Path(__file__).parent.parent.parent / ".env",  # TestingGround/.env
        Path(__file__).parent / ".env",  # github-trending-cli/.env
        Path.cwd() / ".env",  # current directory
    ]
    
    for env_path in locations:
        if env_path.exists():
            try:
                with open(env_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip().strip('"').strip("'")
                            if key and value and key not in os.environ:
                                os.environ[key] = value
                return True
            except (OSError, ValueError):
                pass
    return False

# Load .env on import
load_env_file()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
API_BASE = "https://api.github.com"

# Scoring weights
WEIGHTS = {
    "recent_commits": 20,      # Active development
    "readme_quality": 15,      # Good documentation
    "issue_response": 15,      # Maintainer responsiveness
    "pr_merge_rate": 15,       # PR acceptance rate
    "has_license": 5,          # Proper licensing
    "low_open_issues": 10,     # Not overwhelmed with issues
    "stars_velocity": 10,      # Growing popularity
    "not_archived": 10,        # Still maintained
}


# =============================================================================
# API Helpers
# =============================================================================

def api_request(endpoint: str) -> Optional[dict]:
    """Make a GitHub API request with optional auth."""
    url = f"{API_BASE}{endpoint}" if endpoint.startswith("/") else endpoint
    
    headers = {
        "User-Agent": "github-trending-cli",
        "Accept": "application/vnd.github.v3+json",
    }
    
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as e:
        if e.code == 403:
            # Rate limited
            return {"_error": "rate_limited"}
        elif e.code == 404:
            return {"_error": "not_found"}
        return {"_error": str(e)}
    except Exception as e:
        return {"_error": str(e)}


def get_rate_limit() -> dict:
    """Check current rate limit status."""
    result = api_request("/rate_limit")
    if result and "_error" not in result:
        core = result.get("resources", {}).get("core", {})
        return {
            "remaining": core.get("remaining", 0),
            "limit": core.get("limit", 60),
            "reset": core.get("reset", 0),
        }
    return {"remaining": 0, "limit": 60, "reset": 0}


# =============================================================================
# Data Fetchers
# =============================================================================

def get_repo_info(owner: str, repo: str) -> dict:
    """Fetch basic repo info."""
    return api_request(f"/repos/{owner}/{repo}") or {}


def get_commits(owner: str, repo: str, limit: int = 10) -> list:
    """Fetch recent commits."""
    result = api_request(f"/repos/{owner}/{repo}/commits?per_page={limit}")
    if isinstance(result, list):
        return result
    return []


def get_open_issues(owner: str, repo: str) -> dict:
    """Fetch open issues count and recent issues."""
    result = api_request(f"/repos/{owner}/{repo}/issues?state=open&per_page=10")
    if isinstance(result, list):
        return {"items": result, "count": len(result)}
    return {"items": [], "count": 0}


def get_pull_requests(owner: str, repo: str, state: str = "all", limit: int = 20) -> list:
    """Fetch pull requests."""
    result = api_request(f"/repos/{owner}/{repo}/pulls?state={state}&per_page={limit}")
    if isinstance(result, list):
        return result
    return []


def get_readme(owner: str, repo: str) -> Optional[str]:
    """Fetch README content."""
    result = api_request(f"/repos/{owner}/{repo}/readme")
    if result and "_error" not in result:
        # README API returns base64 encoded content
        import base64
        content = result.get("content", "")
        try:
            return base64.b64decode(content).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return None
    return None


# =============================================================================
# Scoring Functions
# =============================================================================

def score_recent_commits(commits: list) -> tuple[int, str]:
    """Score based on commit recency and frequency."""
    if not commits:
        return 0, "No commits found"
    
    try:
        latest = commits[0].get("commit", {}).get("author", {}).get("date", "")
        if latest:
            latest_date = datetime.fromisoformat(latest.replace("Z", "+00:00"))
            days_ago = (datetime.now(latest_date.tzinfo) - latest_date).days
            
            if days_ago < 7:
                return 20, f"Active ({days_ago}d ago)"
            elif days_ago < 30:
                return 15, f"Recent ({days_ago}d ago)"
            elif days_ago < 90:
                return 10, f"Moderate ({days_ago}d ago)"
            elif days_ago < 180:
                return 5, f"Stale ({days_ago}d ago)"
            else:
                return 0, f"Inactive ({days_ago}d ago)"
    except (ValueError, TypeError, KeyError):
        pass
    
    return 10, "Unknown activity"


def score_readme(readme: Optional[str]) -> tuple[int, str]:
    """Score README quality."""
    if not readme:
        return 0, "No README"
    
    length = len(readme)
    has_install = any(x in readme.lower() for x in ["install", "npm", "pip", "cargo", "setup"])
    has_usage = any(x in readme.lower() for x in ["usage", "example", "getting started", "quick start"])
    has_badges = "![" in readme or "[![" in readme
    
    score = 0
    notes = []
    
    if length > 2000:
        score += 5
        notes.append("detailed")
    elif length > 500:
        score += 3
        notes.append("basic")
    else:
        notes.append("minimal")
    
    if has_install:
        score += 4
        notes.append("install docs")
    if has_usage:
        score += 4
        notes.append("usage docs")
    if has_badges:
        score += 2
    
    return min(score, 15), ", ".join(notes) if notes else "Present"


def score_issue_response(issues: list) -> tuple[int, str]:
    """Score based on issue response time."""
    if not issues:
        return 15, "No open issues"
    
    # Check if issues have comments (indicates response)
    responded = sum(1 for i in issues if i.get("comments", 0) > 0)
    rate = responded / len(issues) if issues else 0
    
    if rate > 0.8:
        return 15, f"{int(rate*100)}% responded"
    elif rate > 0.5:
        return 10, f"{int(rate*100)}% responded"
    elif rate > 0.2:
        return 5, f"{int(rate*100)}% responded"
    else:
        return 0, f"Low response rate"


def score_pr_merge_rate(prs: list) -> tuple[int, str]:
    """Score based on PR merge rate."""
    if not prs:
        return 10, "No PRs"
    
    merged = sum(1 for pr in prs if pr.get("merged_at"))
    closed = sum(1 for pr in prs if pr.get("state") == "closed")
    
    if closed == 0:
        return 10, "All PRs open"
    
    rate = merged / closed
    
    if rate > 0.7:
        return 15, f"{int(rate*100)}% merged"
    elif rate > 0.5:
        return 10, f"{int(rate*100)}% merged"
    elif rate > 0.3:
        return 5, f"{int(rate*100)}% merged"
    else:
        return 0, f"Low merge rate"


def score_repo_health(info: dict) -> dict:
    """Score various repo health metrics."""
    scores = {}
    
    # License
    if info.get("license"):
        scores["has_license"] = (5, info["license"].get("spdx_id", "Yes"))
    else:
        scores["has_license"] = (0, "None")
    
    # Archived status
    if info.get("archived"):
        scores["not_archived"] = (0, "Archived!")
    else:
        scores["not_archived"] = (10, "Active")
    
    # Open issues ratio
    open_issues = info.get("open_issues_count", 0)
    if open_issues < 20:
        scores["low_open_issues"] = (10, f"{open_issues} open")
    elif open_issues < 100:
        scores["low_open_issues"] = (7, f"{open_issues} open")
    elif open_issues < 500:
        scores["low_open_issues"] = (3, f"{open_issues} open")
    else:
        scores["low_open_issues"] = (0, f"{open_issues} open (overloaded)")
    
    # Stars velocity (using watchers as proxy for recent interest)
    stars = info.get("stargazers_count", 0)
    if stars > 10000:
        scores["stars_velocity"] = (10, f"{stars:,}⭐")
    elif stars > 1000:
        scores["stars_velocity"] = (7, f"{stars:,}⭐")
    elif stars > 100:
        scores["stars_velocity"] = (4, f"{stars:,}⭐")
    else:
        scores["stars_velocity"] = (2, f"{stars:,}⭐")
    
    return scores


# =============================================================================
# Main Analysis
# =============================================================================

def analyze_repo(owner_repo: str, verbose: bool = False) -> dict:
    """Analyze a single repository and return scores."""
    parts = owner_repo.split("/")
    if len(parts) != 2:
        return {"error": f"Invalid repo format: {owner_repo}"}
    
    owner, repo = parts
    
    result = {
        "repo": owner_repo,
        "scores": {},
        "total": 0,
        "grade": "?",
        "details": {},
    }
    
    # Fetch data
    info = get_repo_info(owner, repo)
    if "_error" in info:
        return {"error": info["_error"], "repo": owner_repo}
    
    commits = get_commits(owner, repo)
    issues_data = get_open_issues(owner, repo)
    prs = get_pull_requests(owner, repo)
    readme = get_readme(owner, repo)
    
    # Calculate scores
    score, note = score_recent_commits(commits)
    result["scores"]["recent_commits"] = score
    result["details"]["recent_commits"] = note
    
    score, note = score_readme(readme)
    result["scores"]["readme_quality"] = score
    result["details"]["readme_quality"] = note
    
    score, note = score_issue_response(issues_data.get("items", []))
    result["scores"]["issue_response"] = score
    result["details"]["issue_response"] = note
    
    score, note = score_pr_merge_rate(prs)
    result["scores"]["pr_merge_rate"] = score
    result["details"]["pr_merge_rate"] = note
    
    # Health scores
    health_scores = score_repo_health(info)
    for key, (score, note) in health_scores.items():
        result["scores"][key] = score
        result["details"][key] = note
    
    # Calculate total
    result["total"] = sum(result["scores"].values())
    
    # Assign grade
    total = result["total"]
    if total >= 85:
        result["grade"] = "A"
    elif total >= 70:
        result["grade"] = "B"
    elif total >= 55:
        result["grade"] = "C"
    elif total >= 40:
        result["grade"] = "D"
    else:
        result["grade"] = "F"
    
    # Add repo metadata
    result["meta"] = {
        "description": info.get("description", ""),
        "stars": info.get("stargazers_count", 0),
        "language": info.get("language", ""),
        "url": info.get("html_url", ""),
    }
    
    return result


def analyze_repos(repos: list, verbose: bool = False) -> list:
    """Analyze multiple repositories."""
    results = []
    
    # Check rate limit first
    rate = get_rate_limit()
    remaining = rate.get("remaining", 0)
    
    # Each repo needs ~5 API calls
    needed = len(repos) * 5
    
    if remaining < needed and not GITHUB_TOKEN:
        print(f"⚠️  Rate limit: {remaining} remaining, need ~{needed}")
        print("   Set GITHUB_TOKEN env var for 5000/hr limit")
        print(f"   Analyzing first {remaining // 5} repos only...\n")
        repos = repos[:max(1, remaining // 5)]
    
    for i, repo_info in enumerate(repos):
        # Extract owner/repo from trending data
        title = repo_info.get("title", "")
        if not title or "/" not in title:
            continue
        
        if verbose:
            print(f"  Analyzing {i+1}/{len(repos)}: {title}...")
        
        result = analyze_repo(title)
        results.append(result)
        
        # Small delay to be nice to API
        time.sleep(0.2)
    
    # Sort by total score
    results.sort(key=lambda x: x.get("total", 0), reverse=True)
    
    return results


# =============================================================================
# Output Formatting
# =============================================================================

def format_analysis_table(results: list) -> str:
    """Format analysis results as a table."""
    lines = []
    
    # Header
    lines.append("┌" + "─" * 78 + "┐")
    lines.append("│" + " TRENDING DIGEST ".center(78) + "│")
    lines.append("├" + "─" * 35 + "┬" + "─" * 7 + "┬" + "─" * 6 + "┬" + "─" * 27 + "┤")
    lines.append("│" + " Repo".ljust(35) + "│" + " Score ".center(7) + "│" + " Grade ".center(6) + "│" + " Notes".ljust(27) + "│")
    lines.append("├" + "─" * 35 + "┼" + "─" * 7 + "┼" + "─" * 6 + "┼" + "─" * 27 + "┤")
    
    for r in results:
        if "error" in r:
            repo = r.get("repo", "?")[:33]
            lines.append(f"│ {repo:<33} │   -   │   ?   │ {'Error: ' + r['error'][:20]:<25} │")
            continue
        
        repo = r.get("repo", "?")[:33]
        score = r.get("total", 0)
        grade = r.get("grade", "?")
        
        # Pick most interesting note
        details = r.get("details", {})
        note = details.get("recent_commits", "")
        if "Archived" in details.get("not_archived", ""):
            note = "⚠️ Archived"
        elif "No README" in details.get("readme_quality", ""):
            note = "Missing docs"
        
        note = note[:25]
        
        # Color grade
        grade_colors = {"A": "\033[92m", "B": "\033[93m", "C": "\033[33m", "D": "\033[91m", "F": "\033[91m"}
        reset = "\033[0m"
        colored_grade = f"{grade_colors.get(grade, '')}{grade}{reset}"
        
        lines.append(f"│ {repo:<33} │ {score:>5} │   {colored_grade}   │ {note:<25} │")
    
    lines.append("└" + "─" * 35 + "┴" + "─" * 7 + "┴" + "─" * 6 + "┴" + "─" * 27 + "┘")
    
    return "\n".join(lines)


def format_analysis_detail(result: dict) -> str:
    """Format detailed analysis for a single repo."""
    if "error" in result:
        return f"Error analyzing {result.get('repo', '?')}: {result['error']}"
    
    lines = []
    repo = result.get("repo", "?")
    meta = result.get("meta", {})
    
    lines.append(f"\n{'=' * 60}")
    lines.append(f"📊 {repo}")
    lines.append(f"{'=' * 60}")
    
    if meta.get("description"):
        lines.append(f"   {meta['description'][:70]}")
    
    lines.append(f"\n   Grade: {result.get('grade', '?')} ({result.get('total', 0)}/100)")
    lines.append("")
    
    # Score breakdown
    for key, score in result.get("scores", {}).items():
        detail = result.get("details", {}).get(key, "")
        label = key.replace("_", " ").title()
        max_score = WEIGHTS.get(key, 10)
        bar = "█" * (score * 10 // max_score) + "░" * (10 - score * 10 // max_score)
        lines.append(f"   {label:<20} [{bar}] {score:>2}/{max_score:<2}  {detail}")
    
    lines.append("")
    
    return "\n".join(lines)


# =============================================================================
# CLI Entry (for standalone use)
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python analyzer.py <owner/repo> [owner/repo ...]")
        print("       Set GITHUB_TOKEN env var for higher rate limits")
        sys.exit(1)
    
    repos = sys.argv[1:]
    
    print(f"\n🔍 Analyzing {len(repos)} repository(ies)...\n")
    
    for repo in repos:
        result = analyze_repo(repo, verbose=True)
        print(format_analysis_detail(result))
