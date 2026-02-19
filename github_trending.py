#!/usr/bin/env python3
"""
GitHub Trending CLI
Fetches trending repositories from GitHub using isboyjc/github-trending-api

Usage:
    python github_trending.py                     # Daily trending, all languages
    python github_trending.py -s weekly -l python # Weekly Python repos
    python github_trending.py --min-stars 5000    # Filter by minimum stars
    python github_trending.py --csv output.csv    # Export to CSV
    python github_trending.py --analyze           # Analyze repos with health scores
"""

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


# =============================================================================
# Configuration
# =============================================================================

BASE_URL = "https://raw.githubusercontent.com/isboyjc/github-trending-api/main/data"
GITHUB_TRENDING_URL = "https://github.com/trending"
CLONE_DIR = os.path.expanduser("~/github-trending-clones")

# Cache settings
def get_cache_dir() -> Path:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "github-trending-cli" / "cache"
        return Path.home() / "AppData" / "Local" / "github-trending-cli" / "cache"

    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home) / "github-trending-cli"

    return Path.home() / ".cache" / "github-trending-cli"


CACHE_DIR = get_cache_dir()
CACHE_TTL = {
    "trending": 3600,      # 1 hour for trending data
    "repo_info": 86400,    # 24 hours for repo info
    "readme": 86400,       # 24 hours for README
    "tree": 86400,         # 24 hours for file tree
    "deps": 86400,         # 24 hours for dependencies
    "issues": 1800,        # 30 minutes for issues
}

# Rate limiting
RATE_LIMIT_DELAY = 0.5  # seconds between API calls
_last_api_call = 0

USE_COLOR = True

LANGUAGES = [
    "all", "python", "javascript", "typescript", "rust", "go", "java", 
    "c++", "c", "c#", "ruby", "php", "swift", "kotlin", "scala", 
    "r", "julia", "dart", "lua", "shell", "powershell", "html", "css"
]

# Common dependency files
DEPENDENCY_FILES = [
    # Python
    "requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "setup.cfg",
    # JavaScript/TypeScript
    "package.json",
    # Rust
    "Cargo.toml",
    # Go
    "go.mod",
    # Ruby
    "Gemfile",
    # Java/Kotlin
    "pom.xml", "build.gradle", "build.gradle.kts",
    # .NET
    "*.csproj", "*.fsproj", "packages.config",
    # PHP
    "composer.json",
    # Swift
    "Package.swift",
]


# =============================================================================
# Caching System
# =============================================================================

def get_cache_path(cache_type: str, key: str) -> Path:
    """Get the cache file path for a given type and key."""
    # Create a safe filename from the key
    safe_key = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / cache_type / f"{safe_key}.json"


def read_cache(cache_type: str, key: str) -> Optional[dict]:
    """Read from cache if not expired."""
    cache_path = get_cache_path(cache_type, key)
    
    if not cache_path.exists():
        return None
    
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Check TTL
        cached_at = data.get("_cached_at", 0)
        ttl = CACHE_TTL.get(cache_type, 3600)
        
        if time.time() - cached_at > ttl:
            return None  # Cache expired
        
        return data.get("_data")
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def write_cache(cache_type: str, key: str, data) -> None:
    """Write data to cache."""
    cache_path = get_cache_path(cache_type, key)
    
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        
        cache_data = {
            "_cached_at": time.time(),
            "_key": key,
            "_data": data
        }
        
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False)
    except (OSError, TypeError):
        pass  # Cache write failure is not critical


def clear_cache(cache_type: str = None) -> int:
    """Clear cache. If cache_type is None, clear all caches."""
    count = 0
    
    if cache_type:
        cache_dir = CACHE_DIR / cache_type
        if cache_dir.exists():
            for f in cache_dir.glob("*.json"):
                f.unlink()
                count += 1
    else:
        if CACHE_DIR.exists():
            for subdir in CACHE_DIR.iterdir():
                if subdir.is_dir():
                    for f in subdir.glob("*.json"):
                        f.unlink()
                        count += 1
    
    return count


# =============================================================================
# Rate Limiting
# =============================================================================

def rate_limit():
    """Apply rate limiting between API calls."""
    global _last_api_call
    
    elapsed = time.time() - _last_api_call
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)
    
    _last_api_call = time.time()


# =============================================================================
# Fallback Scraper
# =============================================================================

def scrape_trending(language: str = "", since: str = "daily") -> list:
    """Fallback: Scrape trending directly from GitHub if API fails."""
    url = f"{GITHUB_TRENDING_URL}/{language}?since={since}"
    
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as response:
            html = response.read().decode('utf-8')
    except Exception as e:
        print(f"❌ Scraping failed: {e}")
        return []
    
    repos = []
    
    # Parse repo entries using regex (simple parser, no dependencies)
    repo_pattern = r'<article class="Box-row".*?</article>'
    matches = re.findall(repo_pattern, html, re.DOTALL)
    
    for match in matches:
        try:
            # Extract repo path
            path_match = re.search(r'href="/([^/]+/[^"]+)"', match)
            if not path_match:
                continue
            
            full_path = path_match.group(1).strip()
            if '/' not in full_path:
                continue
            
            # Extract description
            desc_match = re.search(r'<p class="[^"]*col-9[^"]*"[^>]*>([^<]+)</p>', match)
            description = desc_match.group(1).strip() if desc_match else ""
            
            # Extract stars
            stars_match = re.search(r'(\d[\d,]*)\s*stars', match, re.IGNORECASE)
            if not stars_match:
                stars_match = re.search(r'stargazers[^>]*>[\s\S]*?(\d[\d,]*)', match)
            stars = stars_match.group(1) if stars_match else "0"
            
            # Extract language
            lang_match = re.search(r'itemprop="programmingLanguage">([^<]+)<', match)
            language_name = lang_match.group(1).strip() if lang_match else ""
            
            # Extract today's stars
            today_match = re.search(r'(\d[\d,]*)\s*stars?\s*today', match, re.IGNORECASE)
            today_stars = today_match.group(1) if today_match else ""
            
            repos.append({
                "title": full_path,
                "description": description,
                "stars": stars,
                "language": language_name,
                "todayStars": today_stars,
                "link": f"https://github.com/{full_path}"
            })
        except (AttributeError, IndexError, ValueError):
            continue
    
    return repos


def fetch_trending(since: str, language: str, use_cache: bool = True) -> dict:
    """Fetch trending data from the API with caching and fallback."""
    cache_key = f"{since}_{language}"
    
    # Check cache first
    if use_cache:
        cached = read_cache("trending", cache_key)
        if cached:
            return cached
    
    url = f"{BASE_URL}/{since}/{language.lower()}.json"
    
    try:
        rate_limit()
        with urlopen(url, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))

            # Normalize API field names to internal schema
            if 'items' in data:
                for item in data['items']:
                    if 'url' in item and 'link' not in item:
                        item['link'] = item['url']
                    if 'addStars' in item and 'todayStars' not in item:
                        item['todayStars'] = item['addStars']

            # Cache the result
            write_cache("trending", cache_key, data)
            return data
            
    except HTTPError as e:
        if e.code == 404:
            print(f"⚠️  Language '{language}' not found in API, trying scraper...")
        else:
            print(f"⚠️  API error ({e.code}), falling back to scraper...")
            
    except URLError as e:
        print(f"⚠️  Network error, falling back to scraper...")
    
    # Fallback to direct scraping
    print(f"🔄 Scraping GitHub directly...")
    repos = scrape_trending(language if language != "all" else "", since)
    
    if repos:
        data = {
            "items": repos,
            "pubDate": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        }
        write_cache("trending", cache_key, data)
        return data
    
    print(f"❌ Failed to fetch trending data")
    sys.exit(1)


def filter_repos(repos: list, min_stars: int = 0, max_stars: int = None, 
                 search: str = None) -> list:
    """Filter repositories by various criteria."""
    filtered = []
    
    for repo in repos:
        # Parse stars (remove commas)
        stars = int(repo.get('stars', '0').replace(',', ''))
        
        # Apply filters
        if stars < min_stars:
            continue
        if max_stars and stars > max_stars:
            continue
        if search:
            search_lower = search.lower()
            title = repo.get('title', '').lower()
            desc = repo.get('description', '').lower()
            if search_lower not in title and search_lower not in desc:
                continue
        
        # Add parsed stars for sorting
        repo['_stars_int'] = stars
        filtered.append(repo)
    
    return filtered


def export_csv(repos: list, filename: str):
    """Export repositories to CSV file."""
    if not repos:
        print("❌ No repositories to export.")
        return
    
    fieldnames = ['rank', 'title', 'stars', 'today_stars', 'language', 'description', 'url']
    
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for i, repo in enumerate(repos, 1):
            writer.writerow({
                'rank': i,
                'title': repo.get('title', ''),
                'stars': repo.get('stars', ''),
                'today_stars': repo.get('todayStars', ''),
                'language': repo.get('language', ''),
                'description': repo.get('description', ''),
                'url': repo.get('link', '')
            })
    
    print(f"✅ Exported {len(repos)} repositories to {filename}")


def export_json(repos: list, filename: str):
    """Export repositories to JSON file."""
    if not repos:
        print("❌ No repositories to export.")
        return

    clean_repos = []
    for i, repo in enumerate(repos, 1):
        title = repo.get('title', '')
        clean_repos.append({
            'rank': i,
            'title': title,
            'url': repo.get('link', f"https://github.com/{title}"),
            'description': repo.get('description', ''),
            'language': repo.get('language', ''),
            'stars': repo.get('stars', '0'),
            'stars_today': repo.get('todayStars', ''),
        })

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(clean_repos, f, indent=2, ensure_ascii=False)

    print(f"✅ Exported {len(repos)} repositories to {filename}")


def clone_repo(repo: dict, target_dir: str = None, shallow: bool = False) -> bool:
    """Clone a repository using git."""
    title = repo.get('title', '')
    if not title:
        print("❌ Invalid repository")
        return False
    
    # Build clone URL
    clone_url = f"https://github.com/{title}.git"
    
    # Determine target directory
    if target_dir:
        clone_path = os.path.join(target_dir, title.split('/')[-1])
    else:
        clone_path = title.split('/')[-1]
    
    # Check if already exists
    if os.path.exists(clone_path):
        print(f"⚠️  Directory already exists: {clone_path}")
        response = input("   Overwrite? (y/N): ").strip().lower()
        if response != 'y':
            return False
        import shutil
        shutil.rmtree(clone_path)
    
    # Build git command
    cmd = ['git', 'clone']
    if shallow:
        cmd.extend(['--depth', '1'])
    cmd.extend([clone_url, clone_path])
    
    print(f"📦 Cloning {title}...")
    print(f"   → {clone_path}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ Successfully cloned {title}")
            return True
        else:
            print(f"❌ Failed to clone: {result.stderr.strip()}")
            return False
    except FileNotFoundError:
        print("❌ Git is not installed or not in PATH")
        return False


def interactive_clone(repos: list, target_dir: str = None, shallow: bool = False):
    """Interactive mode to select and clone repositories."""
    if not repos:
        print("No repositories available to clone.")
        return
    
    print("\n" + "=" * 60)
    print("🔧 CLONE MODE - Select repositories to clone")
    print("=" * 60)
    print("Enter numbers separated by spaces (e.g., '1 3 5')")
    print("Enter 'all' to clone all, or 'q' to quit")
    print("-" * 60)
    
    # Show numbered list
    for i, repo in enumerate(repos, 1):
        title = repo.get('title', 'Unknown')
        stars = repo.get('stars', '0')
        lang = repo.get('language', '')
        lang_str = f" [{lang}]" if lang else ""
        print(f"  {i:2}. {title} ⭐{stars}{lang_str}")
    
    print("-" * 60)
    
    while True:
        try:
            selection = input("\n🎯 Select repos to clone: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Cancelled.")
            return
        
        if selection == 'q':
            print("👋 Cancelled.")
            return
        
        if selection == 'all':
            indices = list(range(len(repos)))
            break
        
        try:
            indices = [int(x) - 1 for x in selection.split()]
            if all(0 <= i < len(repos) for i in indices):
                break
            print(f"❌ Please enter numbers between 1 and {len(repos)}")
        except ValueError:
            print("❌ Invalid input. Enter numbers separated by spaces.")
    
    # Create target directory if specified
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    
    # Clone selected repos
    print(f"\n📥 Cloning {len(indices)} repositories...\n")
    success = 0
    for i in indices:
        if clone_repo(repos[i], target_dir, shallow):
            success += 1
        print()
    
    print("=" * 60)
    print(f"✅ Cloned {success}/{len(indices)} repositories")
    if target_dir:
        print(f"📁 Location: {os.path.abspath(target_dir)}")


def clone_by_number(repos: list, numbers: list, target_dir: str = None, shallow: bool = False):
    """Clone specific repositories by their number in the list."""
    if not repos:
        print("No repositories available.")
        return
    
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    
    success = 0
    for num in numbers:
        idx = num - 1
        if 0 <= idx < len(repos):
            if clone_repo(repos[idx], target_dir, shallow):
                success += 1
            print()
        else:
            print(f"❌ Invalid number: {num} (valid: 1-{len(repos)})")
    
    print(f"✅ Cloned {success}/{len(numbers)} repositories")


def list_cloned_repos(clone_dir: str = None) -> list:
    """List all cloned repositories in the clone directory."""
    target = clone_dir or "."
    
    if not os.path.exists(target):
        return []
    
    clones = []
    for item in os.listdir(target):
        item_path = os.path.join(target, item)
        git_path = os.path.join(item_path, ".git")
        if os.path.isdir(item_path) and os.path.exists(git_path):
            # Get remote URL to identify the repo
            try:
                result = subprocess.run(
                    ['git', '-C', item_path, 'remote', 'get-url', 'origin'],
                    capture_output=True, text=True
                )
                remote = result.stdout.strip() if result.returncode == 0 else "unknown"
            except OSError:
                remote = "unknown"
            
            # Get size
            size = get_dir_size(item_path)
            
            clones.append({
                'name': item,
                'path': item_path,
                'remote': remote,
                'size': size
            })
    
    return clones


def get_dir_size(path: str) -> str:
    """Get human-readable directory size."""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
    except OSError:
        return "?"
    
    # Convert to human readable
    for unit in ['B', 'KB', 'MB', 'GB']:
        if total < 1024:
            return f"{total:.1f} {unit}"
        total /= 1024
    return f"{total:.1f} TB"


def cleanup_repo(repo_path: str) -> bool:
    """Remove a cloned repository."""
    import shutil
    
    if not os.path.exists(repo_path):
        print(f"❌ Path not found: {repo_path}")
        return False
    
    repo_name = os.path.basename(repo_path)
    
    try:
        # On Windows, sometimes need to handle read-only files
        def remove_readonly(func, path, excinfo):
            os.chmod(path, 0o777)
            func(path)
        
        shutil.rmtree(repo_path, onerror=remove_readonly)
        print(f"🗑️  Removed: {repo_name}")
        return True
    except Exception as e:
        print(f"❌ Failed to remove {repo_name}: {e}")
        return False


def sanitize_repo_dir_name(name: str) -> Optional[str]:
    name = name.strip()
    if not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name:
        return None
    if name.startswith("~"):
        return None
    return name


def interactive_cleanup(clone_dir: str = None):
    """Interactive mode to select and remove cloned repositories."""
    clones = list_cloned_repos(clone_dir)
    
    if not clones:
        print("📁 No cloned repositories found.")
        if clone_dir:
            print(f"   Searched in: {os.path.abspath(clone_dir)}")
        return
    
    print("\n" + "=" * 60)
    print("🗑️  CLEANUP MODE - Select repositories to remove")
    print("=" * 60)
    print("Enter numbers separated by spaces (e.g., '1 3 5')")
    print("Enter 'all' to remove all, or 'q' to quit")
    print("-" * 60)
    
    # Show numbered list
    total_size = 0
    for i, clone in enumerate(clones, 1):
        name = clone['name']
        size = clone['size']
        remote = clone['remote'].replace('https://github.com/', '').replace('.git', '')
        print(f"  {i:2}. {name:<30} ({size}) - {remote}")
    
    print("-" * 60)
    
    while True:
        try:
            selection = input("\n🎯 Select repos to remove: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Cancelled.")
            return
        
        if selection == 'q':
            print("👋 Cancelled.")
            return
        
        if selection == 'all':
            confirm = input(f"⚠️  Remove ALL {len(clones)} repositories? (yes/N): ").strip().lower()
            if confirm != 'yes':
                print("👋 Cancelled.")
                return
            indices = list(range(len(clones)))
            break
        
        try:
            indices = [int(x) - 1 for x in selection.split()]
            if all(0 <= i < len(clones) for i in indices):
                break
            print(f"❌ Please enter numbers between 1 and {len(clones)}")
        except ValueError:
            print("❌ Invalid input. Enter numbers separated by spaces.")
    
    # Confirm removal
    if len(indices) > 1:
        confirm = input(f"⚠️  Remove {len(indices)} repositories? (y/N): ").strip().lower()
        if confirm != 'y':
            print("👋 Cancelled.")
            return
    
    # Remove selected repos
    print(f"\n🗑️  Removing {len(indices)} repositories...\n")
    success = 0
    for i in indices:
        if cleanup_repo(clones[i]['path']):
            success += 1
    
    print("\n" + "=" * 60)
    print(f"✅ Removed {success}/{len(indices)} repositories")


def cleanup_by_name(names: list, clone_dir: str = None):
    """Remove specific repositories by name."""
    target = clone_dir or "."
    target_abs = os.path.abspath(target)
    
    success = 0
    for raw_name in names:
        safe_name = sanitize_repo_dir_name(raw_name)
        if not safe_name:
            print(f"⚠️  Skipping unsafe name: {raw_name!r}")
            continue

        repo_path = os.path.join(target, safe_name)
        repo_abs = os.path.abspath(repo_path)

        if not (repo_abs == target_abs or repo_abs.startswith(target_abs + os.sep)):
            print(f"⚠️  Skipping outside target directory: {safe_name}")
            continue

        if not os.path.isdir(os.path.join(repo_abs, ".git")):
            print(f"⚠️  Skipping non-git directory: {safe_name}")
            continue

        if cleanup_repo(repo_abs):
            success += 1
    
    print(f"\n✅ Removed {success}/{len(names)} repositories")


def explore_repo(repo: dict, clone_dir: str = None, shallow: bool = False, 
                 auto_cleanup: bool = False, editor: str = "code"):
    """Clone a repo, open in editor, optionally cleanup after."""
    title = repo.get('title', '')
    if not title:
        print("❌ Invalid repository")
        return False
    
    # Clone first
    if not clone_repo(repo, clone_dir, shallow):
        return False
    
    # Determine path
    repo_name = title.split('/')[-1]
    if clone_dir:
        repo_path = os.path.join(clone_dir, repo_name)
    else:
        repo_path = repo_name
    
    repo_path = os.path.abspath(repo_path)
    
    # Open in editor
    print(f"\n🚀 Opening in {editor}...")
    try:
        if editor == "code":
            # VS Code - use -n for new window, -w to wait if auto_cleanup
            cmd = [editor, "-n"]
            if auto_cleanup:
                cmd.append("-w")  # Wait for window to close
            cmd.append(repo_path)
        else:
            cmd = [editor, repo_path]
        
        if auto_cleanup:
            print(f"⏳ Waiting for {editor} window to close...")
            print(f"   (Repo will be cleaned up automatically)")
            result = subprocess.run(cmd)
        else:
            # Don't wait, just open
            subprocess.Popen(cmd, start_new_session=True)
            print(f"✅ Opened {title} in {editor}")
            print(f"📁 Location: {repo_path}")
            return True
    except FileNotFoundError:
        print(f"❌ Editor '{editor}' not found in PATH")
        return False
    
    # Auto cleanup if requested
    if auto_cleanup:
        print(f"\n🗑️  Cleaning up...")
        cleanup_repo(repo_path)
    
    return True


def explore_by_number(repos: list, number: int, clone_dir: str = None, 
                      shallow: bool = True, auto_cleanup: bool = False, editor: str = "code"):
    """Explore a specific repository by its number."""
    if not repos:
        print("No repositories available.")
        return
    
    idx = number - 1
    if 0 <= idx < len(repos):
        explore_repo(repos[idx], clone_dir, shallow, auto_cleanup, editor)
    else:
        print(f"❌ Invalid number: {number} (valid: 1-{len(repos)})")


def output_json(repos: list, data: dict):
    """Output repos as JSON for agent consumption."""
    output = {
        "updated": data.get("pubDate", ""),
        "count": len(repos),
        "repositories": []
    }
    
    for i, repo in enumerate(repos, 1):
        output["repositories"].append({
            "rank": i,
            "title": repo.get("title", ""),
            "author": repo.get("title", "").split("/")[0] if "/" in repo.get("title", "") else "",
            "name": repo.get("title", "").split("/")[-1] if "/" in repo.get("title", "") else repo.get("title", ""),
            "url": f"https://github.com/{repo.get('title', '')}",
            "clone_url": f"https://github.com/{repo.get('title', '')}.git",
            "description": repo.get("description", ""),
            "language": repo.get("language", ""),
            "stars": repo.get("stars", ""),
            "stars_today": repo.get("todayStars", "")
        })
    
    print(json.dumps(output, indent=2, ensure_ascii=False))


def fetch_readme(repo_title: str) -> str:
    """Fetch README from GitHub using gh CLI without cloning."""
    try:
        # Try to get README using gh api
        result = subprocess.run(
            ['gh', 'api', f'repos/{repo_title}/readme', '--jq', '.content'],
            capture_output=True, text=True
        )
        
        if result.returncode == 0 and result.stdout.strip():
            # README is base64 encoded
            import base64
            content = base64.b64decode(result.stdout.strip()).decode('utf-8')
            return content
        else:
            # Fallback: try raw URL
            readme_url = f"https://raw.githubusercontent.com/{repo_title}/main/README.md"
            try:
                with urlopen(readme_url, timeout=10) as response:
                    return response.read().decode('utf-8')
            except (URLError, HTTPError):
                # Try master branch
                readme_url = f"https://raw.githubusercontent.com/{repo_title}/master/README.md"
                with urlopen(readme_url, timeout=10) as response:
                    return response.read().decode('utf-8')
    except FileNotFoundError:
        print("❌ GitHub CLI (gh) not found. Install from https://cli.github.com/")
        return None
    except Exception as e:
        print(f"❌ Failed to fetch README: {e}")
        return None


def show_readme(repo_title: str, max_lines: int = None, raw: bool = False):
    """Display README for a repository."""
    print(f"\n📄 Fetching README for {repo_title}...")
    
    readme = fetch_readme(repo_title)
    if not readme:
        return
    
    if raw:
        # Output raw markdown (for agents)
        print(readme)
        return
    
    lines = readme.split('\n')
    
    # Truncate if needed
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    else:
        truncated = False
    
    print("\n" + "=" * 60)
    print(f"📖 README.md - {repo_title}")
    print("=" * 60 + "\n")
    
    for line in lines:
        print(line)
    
    if truncated:
        print(f"\n... (truncated, showing first {max_lines} lines)")
    
    print("\n" + "=" * 60)


def show_local_readme(repo_path: str, max_lines: int = 50):
    """Display README from a cloned repository."""
    readme_names = ['README.md', 'readme.md', 'README.rst', 'README.txt', 'README']
    
    readme_path = None
    for name in readme_names:
        path = os.path.join(repo_path, name)
        if os.path.exists(path):
            readme_path = path
            break
    
    if not readme_path:
        print("📄 No README found in repository")
        return
    
    try:
        with open(readme_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"❌ Failed to read README: {e}")
        return
    
    lines = content.split('\n')
    
    # Truncate if needed
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    else:
        truncated = False
    
    print("\n" + "=" * 60)
    print(f"📖 {os.path.basename(readme_path)}")
    print("=" * 60 + "\n")
    
    for line in lines:
        print(line)
    
    if truncated:
        print(f"\n... (truncated, showing first {max_lines} lines)")
    
    print("\n" + "=" * 60)


def readme_by_number(repos: list, number: int, max_lines: int = None, raw: bool = False):
    """Fetch and display README for a repo by its number in the list."""
    if not repos:
        print("No repositories available.")
        return
    
    idx = number - 1
    if 0 <= idx < len(repos):
        title = repos[idx].get('title', '')
        if title:
            show_readme(title, max_lines, raw)
        else:
            print("❌ Invalid repository")
    else:
        print(f"❌ Invalid number: {number} (valid: 1-{len(repos)})")


def fetch_repo_info(repo_title: str) -> dict:
    """Fetch repository information using gh CLI."""
    try:
        result = subprocess.run(
            ['gh', 'api', f'repos/{repo_title}'],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            print(f"❌ Failed to fetch repo info: {result.stderr.strip()}")
            return None
    except FileNotFoundError:
        print("❌ GitHub CLI (gh) not found. Install from https://cli.github.com/")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def show_repo_info(repo_title: str, raw: bool = False):
    """Display repository information."""
    info = fetch_repo_info(repo_title)
    if not info:
        return
    
    if raw:
        # Output as JSON for agents
        output = {
            "name": info.get("full_name", ""),
            "description": info.get("description", ""),
            "url": info.get("html_url", ""),
            "clone_url": info.get("clone_url", ""),
            "stars": info.get("stargazers_count", 0),
            "forks": info.get("forks_count", 0),
            "watchers": info.get("subscribers_count", 0),
            "open_issues": info.get("open_issues_count", 0),
            "language": info.get("language", ""),
            "license": info.get("license", {}).get("spdx_id", "") if info.get("license") else "",
            "topics": info.get("topics", []),
            "created_at": info.get("created_at", ""),
            "updated_at": info.get("updated_at", ""),
            "pushed_at": info.get("pushed_at", ""),
            "default_branch": info.get("default_branch", ""),
            "is_fork": info.get("fork", False),
            "is_archived": info.get("archived", False),
            "homepage": info.get("homepage", "")
        }
        print(json.dumps(output, indent=2))
        return
    
    # Human-readable format
    stars = info.get("stargazers_count", 0)
    forks = info.get("forks_count", 0)
    issues = info.get("open_issues_count", 0)
    language = info.get("language", "Unknown")
    license_info = info.get("license", {})
    license_name = license_info.get("spdx_id", "None") if license_info else "None"
    topics = info.get("topics", [])
    pushed_at = info.get("pushed_at", "")
    default_branch = info.get("default_branch", "main")
    archived = info.get("archived", False)
    homepage = info.get("homepage", "")
    
    # Parse pushed_at to relative time
    if pushed_at:
        try:
            from datetime import datetime, timezone
            pushed = datetime.fromisoformat(pushed_at.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            diff = now - pushed
            if diff.days > 0:
                last_push = f"{diff.days}d ago"
            elif diff.seconds > 3600:
                last_push = f"{diff.seconds // 3600}h ago"
            else:
                last_push = f"{diff.seconds // 60}m ago"
        except (ValueError, TypeError):
            last_push = pushed_at[:10]
    else:
        last_push = "Unknown"
    
    print(f"\n{'=' * 60}")
    print(f"📊 Repository Info: {repo_title}")
    print(f"{'=' * 60}")
    print(f"  ⭐ Stars:       {stars:,}")
    print(f"  🍴 Forks:       {forks:,}")
    print(f"  🐛 Open Issues: {issues:,}")
    print(f"  💻 Language:    {language}")
    print(f"  📜 License:     {license_name}")
    print(f"  🌿 Branch:      {default_branch}")
    print(f"  📅 Last Push:   {last_push}")
    
    if archived:
        print(f"  ⚠️  Status:      ARCHIVED")
    
    if homepage:
        print(f"  🌐 Homepage:    {homepage}")
    
    if topics:
        print(f"  🏷️  Topics:      {', '.join(topics[:8])}")
    
    print(f"  🔗 URL:         https://github.com/{repo_title}")
    print(f"{'=' * 60}")


def fetch_repo_tree(repo_title: str, branch: str = None) -> list:
    """Fetch repository file tree using gh CLI."""
    try:
        # First get default branch if not specified
        if not branch:
            info = fetch_repo_info(repo_title)
            if info:
                branch = info.get("default_branch", "main")
            else:
                branch = "main"
        
        result = subprocess.run(
            ['gh', 'api', f'repos/{repo_title}/git/trees/{branch}?recursive=1'],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("tree", [])
        else:
            # Try 'master' if 'main' failed
            if branch == "main":
                result = subprocess.run(
                    ['gh', 'api', f'repos/{repo_title}/git/trees/master?recursive=1'],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    return data.get("tree", [])
            
            print(f"❌ Failed to fetch tree: {result.stderr.strip()}")
            return None
    except FileNotFoundError:
        print("❌ GitHub CLI (gh) not found. Install from https://cli.github.com/")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def show_repo_tree(repo_title: str, max_depth: int = 2, max_items: int = 50, raw: bool = False):
    """Display repository file tree."""
    tree = fetch_repo_tree(repo_title)
    if tree is None:
        return
    
    if not tree:
        print("📁 Repository is empty")
        return
    
    if raw:
        # Output as JSON for agents
        output = {
            "repo": repo_title,
            "total_items": len(tree),
            "tree": []
        }
        for item in tree[:max_items * 2]:  # Give agents more items
            output["tree"].append({
                "path": item.get("path", ""),
                "type": item.get("type", ""),  # "blob" = file, "tree" = dir
                "size": item.get("size", 0) if item.get("type") == "blob" else None
            })
        print(json.dumps(output, indent=2))
        return
    
    # Build tree structure for display
    print(f"\n{'=' * 60}")
    print(f"📁 File Tree: {repo_title}")
    print(f"{'=' * 60}")
    
    # Filter and sort
    items = []
    for item in tree:
        path = item.get("path", "")
        item_type = item.get("type", "")
        depth = path.count('/')
        
        if depth < max_depth:
            items.append({
                "path": path,
                "type": item_type,
                "depth": depth,
                "size": item.get("size", 0)
            })
    
    # Sort: directories first, then files, alphabetically
    items.sort(key=lambda x: (x["depth"], x["type"] != "tree", x["path"]))
    
    shown = 0
    for item in items:
        if shown >= max_items:
            remaining = len(items) - shown
            print(f"  ... and {remaining} more items")
            break
        
        path = item["path"]
        depth = item["depth"]
        indent = "  " * (depth + 1)
        name = path.split('/')[-1]
        
        if item["type"] == "tree":
            print(f"{indent}📁 {name}/")
        else:
            # Add file size for larger files
            size = item.get("size", 0)
            if size > 100000:
                size_str = f" ({size // 1024}KB)"
            else:
                size_str = ""
            print(f"{indent}📄 {name}{size_str}")
        
        shown += 1
    
    print(f"\n{'=' * 60}")
    print(f"📊 Total: {len(tree)} files/folders")
    print(f"{'=' * 60}")


def fetch_deps(repo_title: str) -> dict:
    """Fetch dependency files from a repository without cloning."""
    rate_limit()
    
    # Check cache first
    cache_key = f"deps_{repo_title.replace('/', '_')}"
    cached = read_cache("deps", cache_key)
    if cached:
        return cached
    
    deps = {}
    
    for dep_file in DEPENDENCY_FILES:
        try:
            # Try to fetch each dependency file
            result = subprocess.run(
                ['gh', 'api', f'repos/{repo_title}/contents/{dep_file}',
                 '-H', 'Accept: application/vnd.github.raw+json'],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                deps[dep_file] = result.stdout
        except Exception:
            continue
    
    # Cache the results
    if deps:
        write_cache("deps", cache_key, deps)
    
    return deps


def show_deps(repo_title: str, raw: bool = False):
    """Display dependency files for a repository."""
    deps = fetch_deps(repo_title)
    
    if not deps:
        if raw:
            print(json.dumps({"repo": repo_title, "deps": {}, "error": "No dependency files found"}))
        else:
            print(f"📦 No dependency files found in {repo_title}")
            print(f"   Checked: {', '.join(DEPENDENCY_FILES[:5])}...")
        return
    
    if raw:
        output = {
            "repo": repo_title,
            "deps": deps,
            "files_found": list(deps.keys())
        }
        print(json.dumps(output, indent=2))
        return
    
    print(f"\n{'=' * 60}")
    print(f"📦 Dependencies: {repo_title}")
    print(f"{'=' * 60}")
    
    for filename, content in deps.items():
        print(f"\n📄 {filename}")
        print("-" * 40)
        
        # Truncate if very long
        lines = content.strip().split('\n')
        if len(lines) > 50:
            for line in lines[:40]:
                print(f"  {line}")
            print(f"  ... ({len(lines) - 40} more lines)")
        else:
            for line in lines:
                print(f"  {line}")
    
    print(f"\n{'=' * 60}")
    print(f"📊 Found {len(deps)} dependency file(s)")
    print(f"{'=' * 60}")


def fetch_issues(repo_title: str, limit: int = 10) -> list:
    """Fetch recent issues and PRs from a repository."""
    rate_limit()
    
    # Check cache first
    cache_key = f"issues_{repo_title.replace('/', '_')}"
    cached = read_cache("issues", cache_key)
    if cached:
        return cached[:limit]
    
    try:
        # Use gh issue list which works reliably
        result = subprocess.run(
            ['gh', 'issue', 'list', '-R', repo_title, '--limit', '20', '--json', 
             'number,title,state,author,labels,createdAt,url'],
            capture_output=True, text=True
        )
        
        if result.returncode == 0 and result.stdout.strip():
            issues = json.loads(result.stdout)
            write_cache("issues", cache_key, issues)
            return issues[:limit]
        
        return []
    except Exception as e:
        print(f"❌ Error fetching issues: {e}")
        return []


def show_issues(repo_title: str, limit: int = 10, raw: bool = False):
    """Display recent issues and PRs for a repository."""
    issues = fetch_issues(repo_title, limit)
    
    if not issues:
        if raw:
            print(json.dumps({"repo": repo_title, "issues": [], "error": "No issues found"}))
        else:
            print(f"📋 No issues found in {repo_title}")
        return
    
    if raw:
        output = {
            "repo": repo_title,
            "total": len(issues),
            "issues": issues
        }
        print(json.dumps(output, indent=2))
        return
    
    print(f"\n{'=' * 60}")
    print(f"📋 Recent Issues: {repo_title}")
    print(f"{'=' * 60}")
    
    for issue in issues:
        number = issue.get('number', '?')
        title = issue.get('title', 'Unknown')
        state = issue.get('state', 'unknown')
        author = issue.get('author', {})
        if isinstance(author, dict):
            author = author.get('login', 'unknown')
        
        # Check if it's a PR
        is_pr = issue.get('pull_request') is not None or 'pullRequest' in str(issue)
        icon = "🔀" if is_pr else ("🟢" if state == "open" else "🔴")
        
        labels = issue.get('labels', [])
        if labels and isinstance(labels[0], dict):
            label_names = [l.get('name', '') for l in labels]
        else:
            label_names = labels
        label_str = f" [{', '.join(label_names[:3])}]" if label_names else ""
        
        print(f"\n{icon} #{number}: {title[:60]}{'...' if len(title) > 60 else ''}")
        print(f"   Author: {author} | State: {state}{label_str}")
    
    print(f"\n{'=' * 60}")
    print(f"📊 Showing {len(issues)} issue(s)")
    print(f"{'=' * 60}")


def fetch_issue_detail(repo_title: str, issue_number: int) -> dict:
    """Fetch full details of a single issue including body."""
    rate_limit()
    
    try:
        result = subprocess.run(
            ['gh', 'issue', 'view', str(issue_number), '-R', repo_title, '--json',
             'number,title,state,author,body,labels,createdAt,url,comments'],
            capture_output=True, text=True
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        
        # Try as PR if issue not found
        result = subprocess.run(
            ['gh', 'pr', 'view', str(issue_number), '-R', repo_title, '--json',
             'number,title,state,author,body,labels,createdAt,url,comments'],
            capture_output=True, text=True
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        
        return {}
    except Exception as e:
        print(f"❌ Error fetching issue: {e}")
        return {}


def show_issue_detail(repo_title: str, issue_number: int, raw: bool = False):
    """Display full details of a single issue."""
    issue = fetch_issue_detail(repo_title, issue_number)
    
    if not issue:
        if raw:
            print(json.dumps({"repo": repo_title, "issue": issue_number, "error": "Issue not found"}))
        else:
            print(f"❌ Issue #{issue_number} not found in {repo_title}")
            print(f"   Try: gh issue view {issue_number} -R {repo_title}")
        return
    
    if raw:
        print(json.dumps(issue, indent=2))
        return
    
    title = issue.get('title', 'Unknown')
    number = issue.get('number', issue_number)
    state = issue.get('state', 'unknown')
    author = issue.get('author', {})
    if isinstance(author, dict):
        author = author.get('login', 'unknown')
    body = issue.get('body', 'No description provided.')
    url = issue.get('url', '')
    created = issue.get('createdAt', '')[:10] if issue.get('createdAt') else ''
    
    labels = issue.get('labels', [])
    if labels and isinstance(labels[0], dict):
        label_names = [l.get('name', '') for l in labels]
    else:
        label_names = labels
    
    comments = issue.get('comments', [])
    
    print(f"\n{'=' * 70}")
    print(f"📋 Issue #{number}: {title}")
    print(f"{'=' * 70}")
    print(f"   Repo:    {repo_title}")
    print(f"   State:   {state.upper()}")
    print(f"   Author:  {author}")
    print(f"   Created: {created}")
    if label_names:
        print(f"   Labels:  {', '.join(label_names)}")
    print(f"   URL:     {url}")
    print(f"\n{'─' * 70}")
    print("📝 Description:")
    print(f"{'─' * 70}")
    
    # Print body with some formatting
    if body:
        # Limit very long bodies
        if len(body) > 5000:
            print(body[:5000])
            print(f"\n... (truncated, {len(body) - 5000} more characters)")
        else:
            print(body)
    else:
        print("(No description)")
    
    # Show comments summary
    if comments:
        print(f"\n{'─' * 70}")
        print(f"💬 Comments ({len(comments)}):")
        print(f"{'─' * 70}")
        for i, comment in enumerate(comments[:5]):  # Show first 5 comments
            c_author = comment.get('author', {})
            if isinstance(c_author, dict):
                c_author = c_author.get('login', 'unknown')
            c_body = comment.get('body', '')[:200]
            if len(comment.get('body', '')) > 200:
                c_body += '...'
            print(f"\n  [{c_author}]:")
            print(f"  {c_body}")
        if len(comments) > 5:
            print(f"\n  ... and {len(comments) - 5} more comments")
    
    print(f"\n{'=' * 70}")


def print_repos(repos: list, verbose: bool = False):
    """Print repositories to console."""
    if not repos:
        print("No repositories found matching your criteria.")
        return
    
    for i, repo in enumerate(repos, 1):
        title = repo.get('title', 'Unknown')
        stars = repo.get('stars', '0')
        today_stars = repo.get('todayStars', '')
        language = repo.get('language', '')
        description = repo.get('description', '')
        url = repo.get('link', '')
        
        # Format today's stars
        today_str = f" (+{today_stars} today)" if today_stars else ""
        lang_str = f" [{language}]" if language else ""
        
        if USE_COLOR:
            print(f"\n\033[93m{i}.\033[0m \033[97m{title}\033[0m")
            print(f"   ⭐ {stars}{today_str}\033[95m{lang_str}\033[0m")
        else:
            print(f"\n{i}. {title}")
            print(f"   ⭐ {stars}{today_str}{lang_str}")
        
        if description:
            desc = description[:100] + "..." if len(description) > 100 else description
            if USE_COLOR:
                print(f"   \033[90m{desc}\033[0m")
            else:
                print(f"   {desc}")
        
        if verbose and url:
            if USE_COLOR:
                print(f"   \033[36m🔗 {url}\033[0m")
            else:
                print(f"   🔗 {url}")


def main():
    parser = argparse.ArgumentParser(
        description="🚀 GitHub Trending CLI - Fetch trending repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                              # Daily trending, all languages
  %(prog)s -s weekly -l python          # Weekly Python repos
  %(prog)s --min-stars 10000            # Only repos with 10k+ stars
  %(prog)s --search "llm" --top 20      # Search for LLM-related repos
  %(prog)s --csv trending.csv           # Export to CSV
  %(prog)s -l rust --json rust.json     # Export Rust repos to JSON
  
Clone examples:
  %(prog)s -c                           # Interactive clone mode
  %(prog)s --clone-nums 1,3,5           # Clone repos #1, #3, and #5
  %(prog)s --clone-nums 1-5             # Clone repos #1 through #5
  %(prog)s -c --shallow                 # Shallow clone (faster)
  %(prog)s -c --clone-dir ./repos       # Clone to specific directory

Cleanup examples:
  %(prog)s --list-clones                # List cloned repos
  %(prog)s --cleanup                    # Interactive cleanup mode
  %(prog)s --cleanup-names goose,remotion  # Remove specific repos by name
  %(prog)s --cleanup --clone-dir ./repos   # Cleanup from specific directory

Explore mode (clone + open in editor):
  %(prog)s -e 1                         # Explore repo #1 in VS Code
  %(prog)s -e 3 --auto-cleanup          # Explore #3, delete when done
  %(prog)s -e 1 --editor cursor         # Use Cursor instead of VS Code
  %(prog)s -l rust -e 1 --shallow       # Explore top Rust repo

Agent mode (JSON output for scripts):
  %(prog)s --output-json                # Output as JSON
  %(prog)s -l python -t 5 --output-json # Top 5 Python repos as JSON

Analysis mode (health scoring):
  %(prog)s --analyze                    # Analyze top 10 with health scores
  %(prog)s -a -t 5 -l rust              # Analyze top 5 Rust repos
  %(prog)s --analyze-detail 1           # Detailed breakdown for #1
  
  Note: Set GITHUB_TOKEN env var for higher API rate limits (5000/hr vs 60/hr)

README (no clone needed):
  %(prog)s -r 1                         # Show README for repo #1
  %(prog)s -r 3 --readme-lines 50       # First 50 lines of README
  %(prog)s -r 1 --readme-raw            # Raw markdown (for agents)
  %(prog)s --readme-repo block/goose    # README for specific repo
  %(prog)s -e 1 --show-readme           # Clone + show README

Repo info & tree (no clone needed):
  %(prog)s -i 1                         # Quick stats for repo #1
  %(prog)s --info-repo block/goose      # Info for specific repo
  %(prog)s --tree 1                     # File tree for repo #1
  %(prog)s --tree-repo block/goose      # Tree for specific repo
  %(prog)s --tree 1 --tree-depth 3      # Deeper tree view
  %(prog)s -i 1 --raw                   # JSON output (for agents)

Dependencies & issues (no clone needed):
  %(prog)s --deps 1                     # Show dependencies for repo #1
  %(prog)s --deps-repo block/goose      # Dependencies for specific repo
  %(prog)s --issues 1                   # Show recent issues for repo #1
  %(prog)s --issues-repo block/goose    # Issues for specific repo
  %(prog)s --issues 1 --issues-limit 5  # Show only 5 issues
  %(prog)s --deps 1 --raw               # JSON output (for agents)

Cache management:
  %(prog)s --clear-cache                # Clear all cached data
        """
    )
    
    # Time range and language
    parser.add_argument(
        '-s', '--since',
        choices=['daily', 'weekly', 'monthly'],
        default='daily',
        help='Time range for trending (default: daily)'
    )
    parser.add_argument(
        '-l', '--language',
        default='all',
        help=f'Programming language (default: all). Examples: {", ".join(LANGUAGES[1:8])}'
    )
    
    # Filtering
    parser.add_argument(
        '-t', '--top',
        type=int,
        default=10,
        help='Number of repos to show (default: 10)'
    )
    parser.add_argument(
        '--min-stars',
        type=int,
        default=0,
        help='Minimum star count filter'
    )
    parser.add_argument(
        '--max-stars',
        type=int,
        help='Maximum star count filter'
    )
    parser.add_argument(
        '--search',
        help='Search in title and description'
    )
    
    # Sorting
    parser.add_argument(
        '--sort',
        choices=['stars', 'name'],
        help='Sort results (default: trending order)'
    )
    parser.add_argument(
        '--reverse',
        action='store_true',
        help='Reverse sort order'
    )
    
    # Output options
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Show repository URLs'
    )
    parser.add_argument(
        '--csv',
        metavar='FILE',
        help='Export to CSV file'
    )
    parser.add_argument(
        '--json',
        metavar='FILE',
        help='Export to JSON file'
    )
    parser.add_argument(
        '--no-color',
        action='store_true',
        help='Disable colored output'
    )
    
    # List languages
    parser.add_argument(
        '--list-languages',
        action='store_true',
        help='List available languages'
    )
    
    # Clone options
    parser.add_argument(
        '-c', '--clone',
        action='store_true',
        help='Interactive mode to select and clone repositories'
    )
    parser.add_argument(
        '--clone-nums',
        type=str,
        metavar='NUMS',
        help='Clone specific repos by number (e.g., "1,3,5" or "1-5")'
    )
    parser.add_argument(
        '--clone-dir',
        type=str,
        metavar='DIR',
        help='Directory to clone repositories into (default: current dir)'
    )
    parser.add_argument(
        '--shallow',
        action='store_true',
        help='Shallow clone (--depth 1) for faster downloads'
    )
    
    # Cleanup options
    parser.add_argument(
        '--cleanup',
        action='store_true',
        help='Interactive mode to remove cloned repositories'
    )
    parser.add_argument(
        '--cleanup-names',
        type=str,
        metavar='NAMES',
        help='Remove specific repos by folder name (comma-separated)'
    )
    parser.add_argument(
        '--list-clones',
        action='store_true',
        help='List all cloned repositories in clone directory'
    )
    
    # Explore options
    parser.add_argument(
        '-e', '--explore',
        type=int,
        metavar='NUM',
        help='Clone repo #NUM and open in VS Code'
    )
    parser.add_argument(
        '--auto-cleanup',
        action='store_true',
        help='With --explore: remove repo after closing editor'
    )
    parser.add_argument(
        '--editor',
        type=str,
        default='code',
        help='Editor command to use (default: code for VS Code)'
    )
    
    # Agent-friendly output
    parser.add_argument(
        '--output-json',
        action='store_true',
        help='Output results as JSON (for agent/script consumption)'
    )
    
    # README options
    parser.add_argument(
        '-r', '--readme',
        type=int,
        metavar='NUM',
        help='Fetch and display README for repo #NUM (no clone needed)'
    )
    parser.add_argument(
        '--readme-repo',
        type=str,
        metavar='OWNER/REPO',
        help='Fetch README for a specific repo (e.g., "block/goose")'
    )
    parser.add_argument(
        '--readme-lines',
        type=int,
        metavar='N',
        help='Limit README output to N lines'
    )
    parser.add_argument(
        '--readme-raw',
        action='store_true',
        help='Output raw README markdown (for agent parsing)'
    )
    parser.add_argument(
        '--show-readme',
        action='store_true',
        help='With --explore: show README after cloning'
    )
    
    # Repo info options
    parser.add_argument(
        '-i', '--info',
        type=int,
        metavar='NUM',
        help='Show repo info for trending repo #NUM (no clone needed)'
    )
    parser.add_argument(
        '--info-repo',
        type=str,
        metavar='OWNER/REPO',
        help='Show info for a specific repo (e.g., "block/goose")'
    )
    
    # Tree options
    parser.add_argument(
        '--tree',
        type=int,
        metavar='NUM',
        help='Show file tree for trending repo #NUM (no clone needed)'
    )
    parser.add_argument(
        '--tree-repo',
        type=str,
        metavar='OWNER/REPO',
        help='Show file tree for a specific repo (e.g., "block/goose")'
    )
    parser.add_argument(
        '--tree-depth',
        type=int,
        default=2,
        help='Max depth for tree display (default: 2)'
    )
    parser.add_argument(
        '--raw',
        action='store_true',
        help='Output info/tree/readme as JSON for agent parsing'
    )
    
    # Dependencies
    parser.add_argument(
        '--deps',
        type=int,
        metavar='NUM',
        help='Show dependency files for trending repo #NUM (no clone needed)'
    )
    parser.add_argument(
        '--deps-repo',
        type=str,
        metavar='OWNER/REPO',
        help='Show dependencies for a specific repo (e.g., "block/goose")'
    )
    
    # Issues
    parser.add_argument(
        '--issues',
        type=int,
        metavar='NUM',
        help='Show recent issues for trending repo #NUM'
    )
    parser.add_argument(
        '--issues-repo',
        type=str,
        metavar='OWNER/REPO',
        help='Show recent issues for a specific repo (e.g., "block/goose")'
    )
    parser.add_argument(
        '--issues-limit',
        type=int,
        default=10,
        help='Number of issues to show (default: 10)'
    )
    parser.add_argument(
        '--issue',
        type=int,
        metavar='NUM',
        help='Show full details of issue #NUM (use with --issue-repo)'
    )
    parser.add_argument(
        '--issue-repo',
        type=str,
        metavar='OWNER/REPO',
        help='Repository for --issue (e.g., "VectifyAI/PageIndex")'
    )
    
    # Cache management
    parser.add_argument(
        '--clear-cache',
        action='store_true',
        help='Clear all cached data'
    )
    
    # Analysis mode
    parser.add_argument(
        '-a', '--analyze',
        action='store_true',
        help='Analyze trending repos and show health scores (uses GitHub API)'
    )
    parser.add_argument(
        '--analyze-detail',
        type=int,
        metavar='NUM',
        help='Show detailed analysis for repo #NUM'
    )
    
    args = parser.parse_args()
    
    # Handle --list-languages
    if args.list_languages:
        print("Available languages:")
        print(", ".join(LANGUAGES))
        print("\nNote: Use lowercase, replace spaces with '-' (e.g., 'c++', 'jupyter-notebook')")
        return
    
    # Handle --list-clones
    if args.list_clones:
        clones = list_cloned_repos(args.clone_dir)
        if not clones:
            print("📁 No cloned repositories found.")
            if args.clone_dir:
                print(f"   Directory: {os.path.abspath(args.clone_dir)}")
        else:
            print(f"\n📁 Cloned repositories ({len(clones)}):")
            print("=" * 60)
            for clone in clones:
                remote = clone['remote'].replace('https://github.com/', '').replace('.git', '')
                print(f"  📦 {clone['name']:<25} ({clone['size']:<10}) {remote}")
            print("=" * 60)
        return
    
    # Handle --cleanup (interactive)
    if args.cleanup:
        interactive_cleanup(args.clone_dir)
        return
    
    # Handle --cleanup-names
    if args.cleanup_names:
        names = [n.strip() for n in args.cleanup_names.split(',')]
        cleanup_by_name(names, args.clone_dir)
        return
    
    # Handle --readme-repo (direct repo README, no trending fetch needed)
    if args.readme_repo:
        show_readme(args.readme_repo, args.readme_lines, args.readme_raw or args.raw)
        return
    
    # Handle --info-repo (direct repo info, no trending fetch needed)
    if args.info_repo:
        show_repo_info(args.info_repo, args.raw)
        return
    
    # Handle --tree-repo (direct repo tree, no trending fetch needed)
    if args.tree_repo:
        show_repo_tree(args.tree_repo, args.tree_depth, raw=args.raw)
        return
    
    # Handle --deps-repo (direct repo dependencies, no trending fetch needed)
    if args.deps_repo:
        show_deps(args.deps_repo, raw=args.raw)
        return
    
    # Handle --issues-repo (direct repo issues, no trending fetch needed)
    if args.issues_repo:
        show_issues(args.issues_repo, limit=args.issues_limit, raw=args.raw)
        return
    
    # Handle --issue (single issue detail)
    if args.issue:
        if not args.issue_repo:
            print("❌ --issue requires --issue-repo (e.g., --issue-repo VectifyAI/PageIndex --issue 79)")
            return
        show_issue_detail(args.issue_repo, args.issue, raw=args.raw)
        return
    
    # Handle --clear-cache
    if args.clear_cache:
        count = clear_cache()
        print(f"🗑️  Cleared {count} cached file(s) from {CACHE_DIR}")
        return
    
    global USE_COLOR
    USE_COLOR = not args.no_color
    
    # Print header (skip for JSON output or raw mode)
    if not args.output_json and not args.raw:
        print(f"\n📈 GitHub Trending - {args.since} ({args.language})")
        print("=" * 60)
    
    # Fetch data
    data = fetch_trending(args.since, args.language)
    repos = data.get('items', [])
    
    if not repos:
        print("No trending repositories found.")
        return
    
    # Apply filters
    repos = filter_repos(
        repos,
        min_stars=args.min_stars,
        max_stars=args.max_stars,
        search=args.search
    )
    
    # Apply sorting
    if args.sort == 'stars':
        repos.sort(key=lambda x: x.get('_stars_int', 0), reverse=not args.reverse)
    elif args.sort == 'name':
        repos.sort(key=lambda x: x.get('title', '').lower(), reverse=args.reverse)
    
    # Limit results
    repos = repos[:args.top]
    
    # Export or print
    if args.csv:
        export_csv(repos, args.csv)
    elif args.json:
        export_json(repos, args.json)
    elif args.output_json:
        output_json(repos, data)
        return  # Skip footer for clean JSON
    elif args.analyze or args.analyze_detail:
        # Import analyzer module
        try:
            from analyzer import analyze_repos, analyze_repo, format_analysis_table, format_analysis_detail
        except ImportError:
            # Try relative import
            import importlib.util
            spec = importlib.util.spec_from_file_location("analyzer", 
                os.path.join(os.path.dirname(__file__), "analyzer.py"))
            analyzer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(analyzer)
            analyze_repos = analyzer.analyze_repos
            analyze_repo = analyzer.analyze_repo
            format_analysis_table = analyzer.format_analysis_table
            format_analysis_detail = analyzer.format_analysis_detail
        
        if args.analyze_detail:
            # Show detailed analysis for specific repo
            idx = args.analyze_detail - 1
            if 0 <= idx < len(repos):
                title = repos[idx].get('title', '')
                if title:
                    if not args.raw:
                        print(f"\n🔍 Analyzing {title}...")
                    result = analyze_repo(title)
                    if args.raw:
                        print(json.dumps(result, indent=2))
                    else:
                        print(format_analysis_detail(result))
            else:
                print(f"❌ Invalid number: {args.analyze_detail} (valid: 1-{len(repos)})")
        else:
            # Analyze all repos
            if not args.raw:
                print(f"\n🔍 Analyzing {len(repos)} trending repositories...")
                print("   (This may take a moment - fetching from GitHub API)\n")
            results = analyze_repos(repos, verbose=not args.raw)
            if args.raw:
                print(json.dumps(results, indent=2))
            else:
                print()
                print(format_analysis_table(results))
                print(f"\n💡 Use --analyze-detail N for breakdown of repo #N")
        return
    elif args.readme:
        if not args.output_json:
            print_repos(repos, verbose=False)
        readme_by_number(repos, args.readme, args.readme_lines, args.readme_raw or args.raw)
        return
    elif args.info:
        print_repos(repos, verbose=False)
        idx = args.info - 1
        if 0 <= idx < len(repos):
            title = repos[idx].get('title', '')
            if title:
                show_repo_info(title, args.raw)
        else:
            print(f"❌ Invalid number: {args.info} (valid: 1-{len(repos)})")
        return
    elif args.tree:
        print_repos(repos, verbose=False)
        idx = args.tree - 1
        if 0 <= idx < len(repos):
            title = repos[idx].get('title', '')
            if title:
                show_repo_tree(title, args.tree_depth, raw=args.raw)
        else:
            print(f"❌ Invalid number: {args.tree} (valid: 1-{len(repos)})")
        return
    elif args.deps:
        print_repos(repos, verbose=False)
        idx = args.deps - 1
        if 0 <= idx < len(repos):
            title = repos[idx].get('title', '')
            if title:
                show_deps(title, raw=args.raw)
        else:
            print(f"❌ Invalid number: {args.deps} (valid: 1-{len(repos)})")
        return
    elif args.issues:
        print_repos(repos, verbose=False)
        idx = args.issues - 1
        if 0 <= idx < len(repos):
            title = repos[idx].get('title', '')
            if title:
                show_issues(title, limit=args.issues_limit, raw=args.raw)
        else:
            print(f"❌ Invalid number: {args.issues} (valid: 1-{len(repos)})")
        return
    elif args.explore:
        print_repos(repos, verbose=True)
        # Clone the repo
        idx = args.explore - 1
        if 0 <= idx < len(repos):
            repo = repos[idx]
            title = repo.get('title', '')
            repo_name = title.split('/')[-1]
            
            if args.clone_dir:
                repo_path = os.path.join(args.clone_dir, repo_name)
            else:
                repo_path = repo_name
            
            explore_repo(repo, args.clone_dir, args.shallow or True, 
                        args.auto_cleanup, args.editor)
            
            # Show README after cloning if requested
            if args.show_readme and os.path.exists(repo_path):
                show_local_readme(repo_path, args.readme_lines or 50)
        else:
            print(f"❌ Invalid number: {args.explore} (valid: 1-{len(repos)})")
    elif args.clone:
        print_repos(repos, verbose=True)
        interactive_clone(repos, args.clone_dir, args.shallow)
    elif args.clone_nums:
        # Parse clone numbers (supports "1,3,5" or "1-5" or "1,3-5,7")
        numbers = []
        for part in args.clone_nums.split(','):
            part = part.strip()
            if '-' in part:
                start, end = part.split('-', 1)
                numbers.extend(range(int(start), int(end) + 1))
            else:
                numbers.append(int(part))
        print_repos(repos, verbose=True)
        clone_by_number(repos, numbers, args.clone_dir, args.shallow)
    else:
        print_repos(repos, verbose=args.verbose)
    
    # Print footer
    print("\n" + "=" * 60)
    pub_date = data.get('pubDate', 'Unknown')
    print(f"📅 Updated: {pub_date}")
    print(f"📊 Showing {len(repos)} repositories")


if __name__ == '__main__':
    main()
