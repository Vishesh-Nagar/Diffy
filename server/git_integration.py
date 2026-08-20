"""
Diffy — Git Integration
Extracts commits, diffs, and file contents from local git repos (via subprocess)
and from GitHub (via REST API using urllib).
"""

import subprocess
import json
import re
import os
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

import config as cfg


# ---------------------------------------------------------------------------
# Local Git (subprocess)
# ---------------------------------------------------------------------------

def _validate_repo_path(repo_path):
    """Validate that the given path is a directory containing a .git folder."""
    if not repo_path:
        return False
    try:
        abs_path = os.path.abspath(repo_path)
        git_dir = os.path.join(abs_path, ".git")
        return os.path.isdir(git_dir)
    except Exception:
        return False


def _run_git(repo_path, *args):
    """Run a git command and return stdout."""
    if not _validate_repo_path(repo_path):
        return None
    cmd = ["git", "-C", os.path.abspath(repo_path)] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_commits(repo_path, limit=None):
    """
    Return list of commits: [{hash, short_hash, author, date, message}]
    Most recent first.
    """
    if limit is None:
        limit = cfg.get("max_commits", 200)

    fmt = "%H%n%h%n%an%n%aI%n%s"
    raw = _run_git(
        repo_path, "log", f"--max-count={limit}",
        f"--format={fmt}", "--no-merges"
    )
    if not raw:
        return []

    lines = raw.strip().split("\n")
    commits = []
    for i in range(0, len(lines) - 4, 5):
        commits.append({
            "hash": lines[i],
            "short_hash": lines[i + 1],
            "author": lines[i + 2],
            "date": lines[i + 3],
            "message": lines[i + 4],
        })
    return commits


def get_diff(repo_path, commit_hash):
    """Return the full unified diff for a single commit."""
    raw = _run_git(repo_path, "diff-tree", "-p", "--no-commit-id", commit_hash)
    return raw or ""


def get_diff_between(repo_path, hash_old, hash_new):
    """Return diff between two commits."""
    raw = _run_git(repo_path, "diff", hash_old, hash_new)
    return raw or ""


def get_changed_files(repo_path, commit_hash):
    """Return list of files changed in a commit."""
    raw = _run_git(
        repo_path, "diff-tree", "--no-commit-id", "-r",
        "--name-status", commit_hash
    )
    if not raw:
        return []

    files = []
    for line in raw.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            files.append({"status": parts[0], "path": parts[1]})
    return files


def get_file_content(repo_path, commit_hash, file_path):
    """Return file content at a specific commit."""
    raw = _run_git(repo_path, "show", f"{commit_hash}:{file_path}")
    return raw


def get_repo_info(repo_path):
    """Return basic repo info: name, current branch, remote URL."""
    branch = (_run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    remote = (_run_git(repo_path, "remote", "get-url", "origin") or "").strip()
    name = os.path.basename(os.path.abspath(repo_path))
    return {"name": name, "branch": branch, "remote": remote, "path": repo_path}


def get_latest_hash(repo_path):
    """Return the latest commit hash."""
    raw = _run_git(repo_path, "rev-parse", "HEAD")
    return (raw or "").strip()


def get_recent_modifications(repo_path, file_path, limit=10):
    """Return list of line numbers (1-based) modified in the last N commits.
    Lines from uncommitted/staged changes are excluded.
    """
    commits = get_commits(repo_path, limit=limit)
    if not commits:
        return []
    recent_hashes = {c["hash"] for c in commits}

    raw = _run_git(repo_path, "blame", "-l", "--", file_path)
    if not raw:
        return []

    modified_lines = []
    for line_num, line in enumerate(raw.split("\n"), 1):
        if not line:
            continue
        # git blame -l output: <40-char-hash> (<author>...) <line content>
        commit_hash = line.split(" ")[0].lstrip("^")
        # Skip uncommitted/staged changes (all-zeros hash)
        if commit_hash.startswith("0" * 8):
            continue
        if commit_hash in recent_hashes:
            modified_lines.append(line_num)

    return modified_lines


# ---------------------------------------------------------------------------
# Diff Parser
# ---------------------------------------------------------------------------

_DIFF_HEADER = re.compile(r"^diff --git a/(.*) b/(.*)")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)")


def parse_diff(raw_diff):
    """
    Parse unified diff text into structured chunks.
    Returns: [{file, hunks: [{header, added, removed, context}]}]
    """
    if not raw_diff:
        return []

    files = []
    current_file = None
    current_hunk = None

    for line in raw_diff.split("\n"):
        m = _DIFF_HEADER.match(line)
        if m:
            current_file = {"file": m.group(2), "hunks": []}
            files.append(current_file)
            current_hunk = None
            continue

        m = _HUNK_HEADER.match(line)
        if m and current_file is not None:
            current_hunk = {
                "header": line,
                "old_start": int(m.group(1)),
                "new_start": int(m.group(2)),
                "context_label": m.group(3).strip(),
                "added": [],
                "removed": [],
                "context": [],
            }
            current_file["hunks"].append(current_hunk)
            continue

        if current_hunk is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current_hunk["added"].append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                current_hunk["removed"].append(line[1:])
            elif line.startswith(" "):
                current_hunk["context"].append(line[1:])

    return files


# ---------------------------------------------------------------------------
# GitHub REST API (urllib only)
# ---------------------------------------------------------------------------

def _github_request(endpoint, token=None):
    """Make a GET request to GitHub API."""
    if token is None:
        token = cfg.get("github_token", "")

    url = f"https://api.github.com{endpoint}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Diffy/1.0",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        return {"error": str(e)}


def parse_github_remote(remote_url):
    """
    Extract owner/repo from a GitHub remote URL.
    Handles HTTPS and SSH formats.
    """
    if not remote_url:
        return None, None

    # SSH: git@github.com:owner/repo.git
    m = re.match(r"git@github\.com[:/](.+?)/(.+?)(?:\.git)?$", remote_url)
    if m:
        return m.group(1), m.group(2)

    # HTTPS: https://github.com/owner/repo.git
    m = re.match(r"https?://github\.com/(.+?)/(.+?)(?:\.git)?$", remote_url)
    if m:
        return m.group(1), m.group(2)

    return None, None


def fetch_remote_commits(owner, repo, since=None, token=None):
    """
    Fetch commits from GitHub API.
    `since` is ISO 8601 timestamp string.
    """
    endpoint = f"/repos/{owner}/{repo}/commits?per_page=100"
    if since:
        endpoint += f"&since={urllib.parse.quote(since)}"

    data = _github_request(endpoint, token)
    if isinstance(data, dict) and "error" in data:
        return []

    commits = []
    for c in data:
        commits.append({
            "hash": c.get("sha", ""),
            "short_hash": c.get("sha", "")[:7],
            "author": (c.get("commit", {}).get("author", {}).get("name", "")),
            "date": (c.get("commit", {}).get("author", {}).get("date", "")),
            "message": (c.get("commit", {}).get("message", "").split("\n")[0]),
        })
    return commits


def fetch_remote_diff(owner, repo, commit_sha, token=None):
    """Fetch the diff for a single commit from GitHub API."""
    endpoint = f"/repos/{owner}/{repo}/commits/{commit_sha}"
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "Diffy/1.0",
    }
    if token is None:
        token = cfg.get("github_token", "")
    if token:
        headers["Authorization"] = f"token {token}"

    url = f"https://api.github.com{endpoint}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return ""


def fetch_remote_compare(owner, repo, base, head, token=None):
    """Compare two refs on GitHub, return the diff."""
    endpoint = f"/repos/{owner}/{repo}/compare/{base}...{head}"
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "Diffy/1.0",
    }
    if token is None:
        token = cfg.get("github_token", "")
    if token:
        headers["Authorization"] = f"token {token}"

    url = f"https://api.github.com{endpoint}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "."
    info = get_repo_info(path)
    print(f"Repo: {info['name']}  Branch: {info['branch']}")
    print(f"Remote: {info['remote']}")

    commits = get_commits(path, limit=5)
    for c in commits:
        print(f"  {c['short_hash']} {c['date'][:10]} {c['message'][:60]}")

    if commits:
        diff = get_diff(path, commits[0]["hash"])
        parsed = parse_diff(diff)
        for f in parsed:
            added = sum(len(h["added"]) for h in f["hunks"])
            removed = sum(len(h["removed"]) for h in f["hunks"])
            print(f"  File: {f['file']}  +{added} -{removed}")
