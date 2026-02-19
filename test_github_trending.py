#!/usr/bin/env python3
"""Tests for github_trending.py and analyzer.py pure functions."""

import csv
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


# Import functions under test
from github_trending import (
    filter_repos,
    sanitize_repo_dir_name,
    get_cache_path,
    read_cache,
    write_cache,
    clear_cache,
    export_csv,
    export_json,
    output_json,
    get_dir_size,
)
from analyzer import (
    score_readme,
    score_issue_response,
    score_pr_merge_rate,
    score_recent_commits,
    score_repo_health,
    format_analysis_table,
    format_analysis_detail,
)


# =============================================================================
# Sample data
# =============================================================================

SAMPLE_REPOS = [
    {
        "title": "owner/repo-a",
        "description": "A great Python framework",
        "language": "Python",
        "stars": "12,345",
        "todayStars": "200",
        "link": "https://github.com/owner/repo-a",
    },
    {
        "title": "org/repo-b",
        "description": "Rust systems tool",
        "language": "Rust",
        "stars": "890",
        "todayStars": "50",
        "link": "https://github.com/org/repo-b",
    },
    {
        "title": "user/repo-c",
        "description": "JavaScript UI library",
        "language": "JavaScript",
        "stars": "45,000",
        "todayStars": "1,000",
        "link": "https://github.com/user/repo-c",
    },
    {
        "title": "dev/repo-d",
        "description": "",
        "language": "",
        "stars": "100",
        "todayStars": "5",
        "link": "https://github.com/dev/repo-d",
    },
]


# =============================================================================
# filter_repos
# =============================================================================


class TestFilterRepos(unittest.TestCase):
    def test_no_filters(self):
        result = filter_repos(SAMPLE_REPOS)
        self.assertEqual(len(result), 4)

    def test_min_stars(self):
        result = filter_repos(SAMPLE_REPOS, min_stars=1000)
        titles = [r["title"] for r in result]
        self.assertIn("owner/repo-a", titles)
        self.assertIn("user/repo-c", titles)
        self.assertNotIn("org/repo-b", titles)
        self.assertNotIn("dev/repo-d", titles)

    def test_max_stars(self):
        result = filter_repos(SAMPLE_REPOS, max_stars=1000)
        titles = [r["title"] for r in result]
        self.assertIn("org/repo-b", titles)
        self.assertIn("dev/repo-d", titles)
        self.assertNotIn("user/repo-c", titles)

    def test_min_and_max_stars(self):
        result = filter_repos(SAMPLE_REPOS, min_stars=500, max_stars=15000)
        titles = [r["title"] for r in result]
        self.assertIn("owner/repo-a", titles)
        self.assertIn("org/repo-b", titles)
        self.assertNotIn("user/repo-c", titles)

    def test_search_in_title(self):
        result = filter_repos(SAMPLE_REPOS, search="repo-a")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "owner/repo-a")

    def test_search_in_description(self):
        result = filter_repos(SAMPLE_REPOS, search="Rust")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "org/repo-b")

    def test_search_case_insensitive(self):
        result = filter_repos(SAMPLE_REPOS, search="PYTHON")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "owner/repo-a")

    def test_search_no_match(self):
        result = filter_repos(SAMPLE_REPOS, search="nonexistent-xyz")
        self.assertEqual(len(result), 0)

    def test_empty_input(self):
        result = filter_repos([])
        self.assertEqual(result, [])

    def test_adds_stars_int(self):
        result = filter_repos(SAMPLE_REPOS)
        self.assertEqual(result[0]["_stars_int"], 12345)
        self.assertEqual(result[2]["_stars_int"], 45000)

    def test_combined_filters(self):
        result = filter_repos(SAMPLE_REPOS, min_stars=500, search="framework")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "owner/repo-a")


# =============================================================================
# sanitize_repo_dir_name
# =============================================================================


class TestSanitizeRepoDirName(unittest.TestCase):
    def test_valid_name(self):
        self.assertEqual(sanitize_repo_dir_name("my-repo"), "my-repo")

    def test_strips_whitespace(self):
        self.assertEqual(sanitize_repo_dir_name("  my-repo  "), "my-repo")

    def test_rejects_empty(self):
        self.assertIsNone(sanitize_repo_dir_name(""))
        self.assertIsNone(sanitize_repo_dir_name("   "))

    def test_rejects_dot(self):
        self.assertIsNone(sanitize_repo_dir_name("."))
        self.assertIsNone(sanitize_repo_dir_name(".."))

    def test_rejects_forward_slash(self):
        self.assertIsNone(sanitize_repo_dir_name("../../etc/passwd"))
        self.assertIsNone(sanitize_repo_dir_name("foo/bar"))

    def test_rejects_backslash(self):
        self.assertIsNone(sanitize_repo_dir_name("foo\\bar"))

    def test_rejects_tilde(self):
        self.assertIsNone(sanitize_repo_dir_name("~"))
        self.assertIsNone(sanitize_repo_dir_name("~/.ssh"))

    def test_valid_names_with_special_chars(self):
        self.assertEqual(sanitize_repo_dir_name("repo-name"), "repo-name")
        self.assertEqual(sanitize_repo_dir_name("repo_name"), "repo_name")
        self.assertEqual(sanitize_repo_dir_name("repo.name"), "repo.name")


# =============================================================================
# Cache system
# =============================================================================


class TestCacheSystem(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_cache_dir = None

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self._orig_cache_dir is not None:
            import github_trending
            github_trending.CACHE_DIR = self._orig_cache_dir

    def _patch_cache_dir(self):
        import github_trending
        self._orig_cache_dir = github_trending.CACHE_DIR
        github_trending.CACHE_DIR = Path(self.tmpdir)

    def test_get_cache_path_deterministic(self):
        p1 = get_cache_path("trending", "python-daily")
        p2 = get_cache_path("trending", "python-daily")
        self.assertEqual(p1, p2)

    def test_get_cache_path_different_keys(self):
        p1 = get_cache_path("trending", "python-daily")
        p2 = get_cache_path("trending", "rust-weekly")
        self.assertNotEqual(p1, p2)

    def test_write_and_read_cache(self):
        self._patch_cache_dir()
        data = {"items": [{"title": "test/repo"}]}
        write_cache("trending", "test-key", data)
        result = read_cache("trending", "test-key")
        self.assertEqual(result, data)

    def test_read_nonexistent_cache(self):
        self._patch_cache_dir()
        result = read_cache("trending", "nonexistent-key")
        self.assertIsNone(result)

    def test_cache_expiry(self):
        self._patch_cache_dir()
        import github_trending

        # Write with a very short TTL
        old_ttl = github_trending.CACHE_TTL.get("trending")
        github_trending.CACHE_TTL["trending"] = 0  # expire immediately
        try:
            write_cache("trending", "expiry-key", {"test": True})
            time.sleep(0.1)
            result = read_cache("trending", "expiry-key")
            self.assertIsNone(result)
        finally:
            github_trending.CACHE_TTL["trending"] = old_ttl

    def test_clear_cache(self):
        self._patch_cache_dir()
        write_cache("trending", "clear-key", {"test": True})
        count = clear_cache("trending")
        self.assertGreaterEqual(count, 1)
        result = read_cache("trending", "clear-key")
        self.assertIsNone(result)


# =============================================================================
# Export functions
# =============================================================================


class TestExportCSV(unittest.TestCase):
    def test_export_csv_creates_file(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            fname = f.name
        try:
            export_csv(SAMPLE_REPOS, fname)
            with open(fname, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["title"], "owner/repo-a")
            self.assertEqual(rows[0]["rank"], "1")
            self.assertEqual(rows[0]["url"], "https://github.com/owner/repo-a")
        finally:
            os.unlink(fname)

    def test_export_csv_empty_repos(self):
        """Empty repo list should not create a file."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            fname = f.name
        os.unlink(fname)
        export_csv([], fname)
        self.assertFalse(os.path.exists(fname))

    def test_export_csv_fields(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            fname = f.name
        try:
            export_csv(SAMPLE_REPOS[:1], fname)
            with open(fname, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                row = next(reader)
            self.assertIn("rank", row)
            self.assertIn("title", row)
            self.assertIn("stars", row)
            self.assertIn("today_stars", row)
            self.assertIn("language", row)
            self.assertIn("description", row)
            self.assertIn("url", row)
        finally:
            os.unlink(fname)


class TestExportJSON(unittest.TestCase):
    def test_export_json_creates_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            fname = f.name
        try:
            export_json(SAMPLE_REPOS, fname)
            with open(fname, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(len(data), 4)
            self.assertEqual(data[0]["rank"], 1)
            self.assertEqual(data[0]["title"], "owner/repo-a")
            self.assertEqual(data[0]["url"], "https://github.com/owner/repo-a")
        finally:
            os.unlink(fname)

    def test_export_json_clean_schema(self):
        """Exported JSON should have clean keys, no raw API data."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            fname = f.name
        try:
            export_json(SAMPLE_REPOS, fname)
            with open(fname, "r", encoding="utf-8") as f:
                data = json.load(f)
            expected_keys = {"rank", "title", "url", "description", "language", "stars", "stars_today"}
            self.assertEqual(set(data[0].keys()), expected_keys)
        finally:
            os.unlink(fname)

    def test_export_json_empty_repos(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            fname = f.name
        os.unlink(fname)
        export_json([], fname)
        self.assertFalse(os.path.exists(fname))


# =============================================================================
# output_json
# =============================================================================


class TestOutputJSON(unittest.TestCase):
    def test_output_json_structure(self, ):
        """output_json prints valid JSON with expected structure."""
        data = {"pubDate": "Thu, 20 Feb 2026 12:00:00 GMT"}
        with patch("builtins.print") as mock_print:
            output_json(SAMPLE_REPOS, data)
        output = mock_print.call_args[0][0]
        parsed = json.loads(output)
        self.assertEqual(parsed["count"], 4)
        self.assertEqual(parsed["updated"], data["pubDate"])
        self.assertEqual(len(parsed["repositories"]), 4)

    def test_output_json_repo_fields(self):
        data = {"pubDate": ""}
        with patch("builtins.print") as mock_print:
            output_json(SAMPLE_REPOS[:1], data)
        parsed = json.loads(mock_print.call_args[0][0])
        repo = parsed["repositories"][0]
        self.assertEqual(repo["rank"], 1)
        self.assertEqual(repo["title"], "owner/repo-a")
        self.assertEqual(repo["author"], "owner")
        self.assertEqual(repo["name"], "repo-a")
        self.assertIn("url", repo)
        self.assertIn("clone_url", repo)
        self.assertEqual(repo["language"], "Python")
        self.assertEqual(repo["stars"], "12,345")
        self.assertEqual(repo["stars_today"], "200")


# =============================================================================
# get_dir_size
# =============================================================================


class TestGetDirSize(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            result = get_dir_size(d)
            self.assertEqual(result, "0.0 B")

    def test_dir_with_file(self):
        with tempfile.TemporaryDirectory() as d:
            fpath = os.path.join(d, "test.txt")
            with open(fpath, "w") as f:
                f.write("x" * 1024)
            result = get_dir_size(d)
            self.assertIn("KB", result)

    def test_nonexistent_dir(self):
        result = get_dir_size("/nonexistent/path/abc123")
        # os.walk silently yields nothing for missing paths on some platforms
        self.assertIn(result, ("?", "0.0 B"))


# =============================================================================
# Analyzer: score_readme
# =============================================================================


class TestScoreReadme(unittest.TestCase):
    def test_no_readme(self):
        score, note = score_readme(None)
        self.assertEqual(score, 0)
        self.assertIn("No README", note)

    def test_empty_readme(self):
        score, note = score_readme("")
        self.assertEqual(score, 0)

    def test_minimal_readme(self):
        score, _ = score_readme("Hello world")
        self.assertLessEqual(score, 15)

    def test_detailed_readme_with_install_and_usage(self):
        readme = "x" * 3000 + "\n## Installation\npip install foo\n## Usage\nexample code"
        score, note = score_readme(readme)
        self.assertGreaterEqual(score, 10)
        self.assertIn("install docs", note)
        self.assertIn("usage docs", note)

    def test_badges_add_score(self):
        readme = "x" * 600 + "\n![badge](url)\n## Install\npip install x"
        score_with, _ = score_readme(readme)
        readme_no_badge = "x" * 600 + "\n## Install\npip install x"
        score_without, _ = score_readme(readme_no_badge)
        self.assertGreater(score_with, score_without)

    def test_max_score_capped(self):
        readme = "x" * 5000 + "\n![badge](x)\n## Install\npip install x\n## Usage\nexample"
        score, _ = score_readme(readme)
        self.assertLessEqual(score, 15)


# =============================================================================
# Analyzer: score_issue_response
# =============================================================================


class TestScoreIssueResponse(unittest.TestCase):
    def test_no_issues(self):
        score, note = score_issue_response([])
        self.assertEqual(score, 15)

    def test_all_responded(self):
        issues = [{"comments": 2}, {"comments": 1}, {"comments": 5}]
        score, note = score_issue_response(issues)
        self.assertEqual(score, 15)
        self.assertIn("100%", note)

    def test_none_responded(self):
        issues = [{"comments": 0}, {"comments": 0}]
        score, _ = score_issue_response(issues)
        self.assertEqual(score, 0)

    def test_partial_response(self):
        issues = [{"comments": 1}, {"comments": 0}]
        score, _ = score_issue_response(issues)
        # 50% = 0.5, which is NOT > 0.5, so falls to next bracket (> 0.2 → 5)
        self.assertEqual(score, 5)


# =============================================================================
# Analyzer: score_pr_merge_rate
# =============================================================================


class TestScorePRMergeRate(unittest.TestCase):
    def test_no_prs(self):
        score, _ = score_pr_merge_rate([])
        self.assertEqual(score, 10)

    def test_high_merge_rate(self):
        prs = [
            {"merged_at": "2025-01-01", "state": "closed"},
            {"merged_at": "2025-01-02", "state": "closed"},
            {"merged_at": None, "state": "closed"},
        ]
        score, note = score_pr_merge_rate(prs)
        self.assertGreaterEqual(score, 5)

    def test_all_open(self):
        prs = [{"merged_at": None, "state": "open"}]
        score, _ = score_pr_merge_rate(prs)
        self.assertEqual(score, 10)

    def test_zero_merged(self):
        prs = [
            {"merged_at": None, "state": "closed"},
            {"merged_at": None, "state": "closed"},
        ]
        score, _ = score_pr_merge_rate(prs)
        self.assertEqual(score, 0)


# =============================================================================
# Analyzer: score_recent_commits
# =============================================================================


class TestScoreRecentCommits(unittest.TestCase):
    def test_no_commits(self):
        score, note = score_recent_commits([])
        self.assertEqual(score, 0)
        self.assertIn("No commits", note)

    def test_recent_commit(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        commits = [{"commit": {"author": {"date": now}}}]
        score, note = score_recent_commits(commits)
        self.assertEqual(score, 20)
        self.assertIn("Active", note)

    def test_old_commit(self):
        commits = [{"commit": {"author": {"date": "2020-01-01T00:00:00Z"}}}]
        score, note = score_recent_commits(commits)
        self.assertEqual(score, 0)
        self.assertIn("Inactive", note)

    def test_malformed_commit(self):
        commits = [{"commit": {}}]
        score, _ = score_recent_commits(commits)
        self.assertEqual(score, 10)  # Falls through to "Unknown activity"


# =============================================================================
# Analyzer: score_repo_health
# =============================================================================


class TestScoreRepoHealth(unittest.TestCase):
    def test_with_license(self):
        info = {"license": {"spdx_id": "MIT"}, "archived": False, "open_issues_count": 5}
        scores = score_repo_health(info)
        self.assertEqual(scores["has_license"][0], 5)

    def test_no_license(self):
        info = {"license": None, "archived": False, "open_issues_count": 5}
        scores = score_repo_health(info)
        self.assertEqual(scores["has_license"][0], 0)

    def test_archived(self):
        info = {"license": None, "archived": True, "open_issues_count": 0}
        scores = score_repo_health(info)
        self.assertEqual(scores["not_archived"][0], 0)
        self.assertIn("Archived", scores["not_archived"][1])

    def test_many_open_issues(self):
        info = {"license": None, "archived": False, "open_issues_count": 600}
        scores = score_repo_health(info)
        self.assertEqual(scores["low_open_issues"][0], 0)


# =============================================================================
# Analyzer: format functions
# =============================================================================


class TestFormatFunctions(unittest.TestCase):
    def test_format_analysis_table_with_results(self):
        results = [
            {"repo": "owner/repo", "total": 75, "grade": "B",
             "details": {"recent_commits": "Active", "not_archived": "Active", "readme_quality": "Good"},
             "trending": {"todayStars": "100"}},
        ]
        output = format_analysis_table(results)
        self.assertIn("owner/repo", output)
        self.assertIn("75", output)
        self.assertIn("+100", output)

    def test_format_analysis_table_with_error(self):
        results = [{"repo": "bad/repo", "error": "API failed"}]
        output = format_analysis_table(results)
        self.assertIn("bad/repo", output)
        self.assertIn("Error", output)

    def test_format_analysis_detail(self):
        result = {
            "repo": "owner/repo",
            "total": 80,
            "grade": "B",
            "meta": {"description": "A cool project"},
            "scores": {"recent_commits": 20, "readme_quality": 12},
            "details": {"recent_commits": "Active (1d ago)", "readme_quality": "detailed, install docs"},
        }
        output = format_analysis_detail(result)
        self.assertIn("owner/repo", output)
        self.assertIn("80", output)
        self.assertIn("Active", output)

    def test_format_analysis_detail_error(self):
        result = {"repo": "bad/repo", "error": "Not found"}
        output = format_analysis_detail(result)
        self.assertIn("Error", output)
        self.assertIn("bad/repo", output)


if __name__ == "__main__":
    unittest.main()
