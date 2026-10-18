"""Health monitor for the Earthcraft pipeline and services."""

import os
import json
import urllib.request
import urllib.error
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any


class MonitorReport:
    """Tracks health check results and generates issue reports."""

    def __init__(self):
        self.checks: Dict[str, Tuple[bool, str]] = {}

    def add_check(self, name: str, passed: bool, message: str) -> None:
        """Record a check result."""
        self.checks[name] = (passed, message)

    def is_healthy(self) -> bool:
        """Return True if all checks passed."""
        return all(passed for passed, _ in self.checks.values())

    def get_failures(self) -> List[str]:
        """Return list of failure reasons."""
        return [msg for passed, msg in self.checks.values() if not passed]

    def get_summary(self) -> str:
        """Generate a short summary of the results."""
        if self.is_healthy():
            return "All checks pass"
        failures = self.get_failures()
        return failures[0] if failures else "Unknown failure"

    def print_report(self) -> None:
        """Print detailed report to stdout."""
        print("\n=== Earthcraft Health Monitor Report ===\n")
        for name, (passed, message) in self.checks.items():
            status = "✓ PASS" if passed else "✗ FAIL"
            print(f"{status}: {name}")
            if message:
                print(f"  {message}")
        print()
        if self.is_healthy():
            print("Overall: HEALTHY")
        else:
            print(f"Overall: UNHEALTHY ({len(self.get_failures())} failures)")
            for failure in self.get_failures():
                print(f"  - {failure}")
        print()


def fetch_json(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """Fetch JSON from a URL, return None on failure."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()
            return json.loads(data)
    except (urllib.error.URLError, json.JSONDecodeError, Exception):
        return None


def fetch_url(url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[Optional[int], Optional[str]]:
    """Fetch a URL and return (status_code, body). Returns (None, None) on error."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()
            return response.status, data.decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, Exception):
        return None, None


def check_pipeline_freshness(report: MonitorReport) -> None:
    """Check if pipeline commits are fresh (within 3 hours)."""
    url = "https://huggingface.co/api/datasets/EricSpencer00/earthcraft/commits/main"
    data = fetch_json(url)

    if not data or "commits" not in data or not data["commits"]:
        report.add_check("Pipeline freshness", False, "Cannot fetch HF commits")
        return

    # Find newest commit with title starting with "Generate tiles/v2"
    newest_tile_commit = None
    for commit in data["commits"]:
        title = commit.get("title", "")
        if title.startswith("Generate tiles/v2"):
            newest_tile_commit = commit
            break

    if not newest_tile_commit:
        report.add_check("Pipeline freshness", False, "No tile-generation commits found")
        return

    commit_date_str = newest_tile_commit.get("created_at", "")
    if not commit_date_str:
        report.add_check("Pipeline freshness", False, "Cannot parse commit date")
        return

    try:
        # Parse ISO format date
        commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
        now = datetime.now(commit_date.tzinfo) if commit_date.tzinfo else datetime.utcnow()
        age = now - commit_date
        three_hours = timedelta(hours=3)

        if age > three_hours:
            report.add_check(
                "Pipeline freshness",
                False,
                f"Latest tile commit is {int(age.total_seconds() / 3600)} hours old"
            )
            return

        report.add_check(
            "Pipeline freshness",
            True,
            f"Latest tile commit: {age.total_seconds() / 3600:.1f} hours ago"
        )
    except Exception as e:
        report.add_check("Pipeline freshness", False, f"Date parsing error: {str(e)}")
        return

    # Check status/pipeline.json
    status_url = "https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/main/status/pipeline.json"
    status_data = fetch_json(status_url)

    if status_data:
        disk_free_gb = status_data.get("disk_free_gb", 0)
        tiles_24h = status_data.get("tiles_in_24h", 0)

        if disk_free_gb < 12:
            report.add_check(
                "Pipeline status",
                False,
                f"Disk free: {disk_free_gb:.1f} GiB (threshold: 12 GiB)"
            )
            return

        if tiles_24h == 0:
            report.add_check(
                "Pipeline status",
                False,
                "No tiles generated in the last 24 hours"
            )
            return

        report.add_check(
            "Pipeline status",
            True,
            f"Disk free: {disk_free_gb:.1f} GiB, Tiles/24h: {tiles_24h}"
        )


def check_hf_integrity(report: MonitorReport) -> None:
    """Check HuggingFace tile integrity by sampling the newest tile."""
    url = "https://huggingface.co/api/datasets/EricSpencer00/earthcraft/commits/main"
    data = fetch_json(url)

    if not data or "commits" not in data or not data["commits"]:
        report.add_check("HF integrity", False, "Cannot fetch HF commits")
        return

    newest_tile_commit = None
    for commit in data["commits"]:
        title = commit.get("title", "")
        if title.startswith("Generate tiles/v2"):
            newest_tile_commit = commit
            break

    if not newest_tile_commit:
        report.add_check("HF integrity", False, "No tile commits found")
        return

    commit_title = newest_tile_commit.get("title", "")
    # Extract tile name from commit title (e.g., "Generate tiles/v2 18_54, 18_55")
    # Try to extract a tile coordinate from the title
    tile_match = None
    import re
    matches = re.findall(r'(\d+_\d+)', commit_title)
    if matches:
        tile_match = matches[0]

    if not tile_match:
        report.add_check("HF integrity", False, "Cannot extract tile name from commit")
        return

    # Download tile.json and verify files
    tile_json_url = f"https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/main/tiles/v2/{tile_match}/tile.json"
    tile_data = fetch_json(tile_json_url)

    if not tile_data or "files" not in tile_data:
        report.add_check("HF integrity", False, f"Cannot fetch tile.json for {tile_match}")
        return

    # Spot check: verify one file's sha256
    files = tile_data.get("files", [])
    if not files:
        report.add_check("HF integrity", False, "No files in tile.json")
        return

    test_file = files[0]
    file_path = test_file.get("path")
    expected_sha256 = test_file.get("sha256")

    if not file_path or not expected_sha256:
        report.add_check("HF integrity", False, "Malformed tile.json entries")
        return

    file_url = f"https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/main/tiles/v2/{tile_match}/{file_path}"
    status, body = fetch_url(file_url)

    if status != 200 or body is None:
        report.add_check("HF integrity", False, f"Cannot download {file_path}")
        return

    actual_sha256 = hashlib.sha256(body.encode('utf-8') if isinstance(body, str) else body).hexdigest()

    if actual_sha256.lower() != expected_sha256.lower():
        report.add_check(
            "HF integrity",
            False,
            f"SHA256 mismatch for {file_path}"
        )
        return

    report.add_check(
        "HF integrity",
        True,
        f"Verified {file_path} in tile {tile_match}"
    )


def check_ci_health(report: MonitorReport, token: str) -> None:
    """Check that all workflows' latest runs are not failed."""
    headers = {"Authorization": f"token {token}"} if token else {}
    url = "https://api.github.com/repos/EricSpencer00/earthcraft/actions/runs?branch=main&per_page=50"
    data = fetch_json(url, headers)

    if not data or "workflow_runs" not in data:
        report.add_check("CI health", False, "Cannot fetch workflow runs")
        return

    runs = data.get("workflow_runs", [])
    if not runs:
        report.add_check("CI health", False, "No workflow runs found")
        return

    # Group runs by workflow and find latest completed
    latest_by_workflow = {}
    for run in runs:
        workflow_name = run.get("name", "unknown")
        conclusion = run.get("conclusion")
        status = run.get("status")

        if workflow_name not in latest_by_workflow:
            latest_by_workflow[workflow_name] = run
        elif status == "completed" and latest_by_workflow[workflow_name].get("status") != "completed":
            latest_by_workflow[workflow_name] = run

    # Check for failures
    failed_workflows = []
    for workflow, run in latest_by_workflow.items():
        conclusion = run.get("conclusion")
        if conclusion == "failure":
            failed_workflows.append(workflow)

    if failed_workflows:
        report.add_check(
            "CI health",
            False,
            f"Failed workflows: {', '.join(failed_workflows)}"
        )
        return

    report.add_check(
        "CI health",
        True,
        f"All {len(latest_by_workflow)} workflows healthy"
    )


def check_site_up(report: MonitorReport) -> None:
    """Check that the site is accessible."""
    urls = [
        ("https://ericspencer.us/earthcraft/", "main site"),
        ("https://ericspencer.us/earthcraft/blocks.html", "blocks page"),
    ]

    all_ok = True
    for url, name in urls:
        status, _ = fetch_url(url)
        if status != 200:
            report.add_check(f"Site up ({name})", False, f"HTTP {status}")
            all_ok = False

    if all_ok:
        report.add_check("Site up", True, "Both endpoints reachable")


def get_open_monitor_issue(token: str) -> Optional[Dict[str, Any]]:
    """Find the open monitor issue, if it exists."""
    headers = {"Authorization": f"token {token}"} if token else {}
    url = "https://api.github.com/repos/EricSpencer00/earthcraft/issues?labels=monitor&state=open&per_page=1"
    data = fetch_json(url, headers)

    if data and isinstance(data, list) and len(data) > 0:
        return data[0]
    return None


def ensure_monitor_label(token: str) -> None:
    """Ensure the 'monitor' label exists."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = "https://api.github.com/repos/EricSpencer00/earthcraft/labels/monitor"

    # Check if label exists
    status, _ = fetch_url(url, headers)
    if status == 200:
        return  # Label exists

    # Create label
    try:
        data = json.dumps({
            "name": "monitor",
            "color": "ff9999",
            "description": "Health monitor alerts"
        }).encode('utf-8')

        req = urllib.request.Request(
            "https://api.github.com/repos/EricSpencer00/earthcraft/labels",
            data=data,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            pass
    except Exception:
        pass  # Label creation failed, continue anyway


def open_issue(token: str, title: str, body: str) -> Optional[int]:
    """Open a new issue, return issue number or None."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    data = json.dumps({
        "title": title,
        "body": body,
        "labels": ["monitor"]
    }).encode('utf-8')

    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/EricSpencer00/earthcraft/issues",
            data=data,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read())
            return result.get("number")
    except Exception as e:
        print(f"Failed to open issue: {e}")
        return None


def comment_on_issue(token: str, issue_number: int, comment: str) -> None:
    """Add a comment to an issue."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    data = json.dumps({"body": comment}).encode('utf-8')

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/EricSpencer00/earthcraft/issues/{issue_number}/comments",
            data=data,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            pass
    except Exception as e:
        print(f"Failed to comment on issue: {e}")


def close_issue(token: str, issue_number: int) -> None:
    """Close an issue."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    data = json.dumps({"state": "closed"}).encode('utf-8')

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/EricSpencer00/earthcraft/issues/{issue_number}",
            data=data,
            headers=headers,
            method="PATCH"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            pass
    except Exception as e:
        print(f"Failed to close issue: {e}")


def manage_issue(token: str, report: MonitorReport) -> None:
    """Open, comment, or close issue based on health status."""
    existing_issue = get_open_monitor_issue(token)

    if report.is_healthy():
        if existing_issue:
            issue_num = existing_issue.get("number")
            comment_on_issue(token, issue_num, "All checks pass")
            close_issue(token, issue_num)
            print(f"Closed issue #{issue_num}")
    else:
        ensure_monitor_label(token)
        summary = report.get_summary()
        title = f"Monitor: {summary}"

        if existing_issue:
            issue_num = existing_issue.get("number")
            failures = "\n".join(f"- {f}" for f in report.get_failures())
            comment_on_issue(token, issue_num, f"Health check failed:\n{failures}")
            print(f"Commented on issue #{issue_num}")
        else:
            failures = "\n".join(f"- {f}" for f in report.get_failures())
            body = f"Health monitor detected issues:\n\n{failures}\n\nRun workflow to retry."
            issue_num = open_issue(token, title, body)
            if issue_num:
                print(f"Opened issue #{issue_num}")


def main():
    """Run all health checks."""
    report = MonitorReport()

    # Run checks
    check_pipeline_freshness(report)
    check_hf_integrity(report)

    token = os.environ.get("GITHUB_TOKEN", "")
    check_ci_health(report, token)
    check_site_up(report)

    # Print report
    report.print_report()

    # Manage issue
    if token:
        manage_issue(token, report)
    else:
        print("Warning: GITHUB_TOKEN not set, skipping issue management")

    # Exit with status
    exit(0 if report.is_healthy() else 1)


if __name__ == "__main__":
    main()
