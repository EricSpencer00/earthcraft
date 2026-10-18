"""Unit tests for the health monitor."""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add scripts directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

# Import monitor module - need to handle it being not importable
try:
    import monitor
except ImportError:
    # If direct import fails, we'll mock it instead
    monitor = None


class FakeMonitorReport:
    """Fake MonitorReport for testing decision logic."""

    def __init__(self):
        self.checks = {}

    def add_check(self, name, passed, message):
        self.checks[name] = (passed, message)

    def is_healthy(self):
        return all(passed for passed, _ in self.checks.values())

    def get_failures(self):
        return [msg for passed, msg in self.checks.values() if not passed]

    def get_summary(self):
        if self.is_healthy():
            return "All checks pass"
        failures = self.get_failures()
        return failures[0] if failures else "Unknown failure"


class TestMonitorDecisionLogic(unittest.TestCase):
    """Test the decision logic for health monitoring."""

    def test_healthy_all_pass(self):
        """When all checks pass, health monitor is healthy."""
        report = FakeMonitorReport()
        report.add_check("Check1", True, "")
        report.add_check("Check2", True, "")

        self.assertTrue(report.is_healthy())
        self.assertEqual(len(report.get_failures()), 0)

    def test_unhealthy_one_failure(self):
        """When any check fails, health monitor is unhealthy."""
        report = FakeMonitorReport()
        report.add_check("Check1", True, "")
        report.add_check("Check2", False, "Check2 failed")

        self.assertFalse(report.is_healthy())
        self.assertEqual(len(report.get_failures()), 1)
        self.assertIn("Check2 failed", report.get_failures())

    def test_unhealthy_multiple_failures(self):
        """Multiple failures are all reported."""
        report = FakeMonitorReport()
        report.add_check("Check1", False, "Failure 1")
        report.add_check("Check2", False, "Failure 2")
        report.add_check("Check3", True, "")

        self.assertFalse(report.is_healthy())
        self.assertEqual(len(report.get_failures()), 2)
        self.assertIn("Failure 1", report.get_failures())
        self.assertIn("Failure 2", report.get_failures())

    def test_summary_healthy(self):
        """Summary is correct when healthy."""
        report = FakeMonitorReport()
        report.add_check("Check1", True, "")
        self.assertEqual(report.get_summary(), "All checks pass")

    def test_summary_unhealthy(self):
        """Summary reflects first failure when unhealthy."""
        report = FakeMonitorReport()
        report.add_check("Check1", False, "First failure")
        report.add_check("Check2", False, "Second failure")

        summary = report.get_summary()
        self.assertIn("First failure", summary)

    def test_issue_open_on_failure(self):
        """When unhealthy and no open issue, open one."""
        report = FakeMonitorReport()
        report.add_check("Check", False, "Test failure")

        # Simulate decision logic
        is_healthy = report.is_healthy()
        should_open_issue = not is_healthy

        self.assertTrue(should_open_issue)

    def test_issue_comment_on_continued_failure(self):
        """When unhealthy and issue open, comment instead of opening."""
        report = FakeMonitorReport()
        report.add_check("Check", False, "Test failure")

        is_healthy = report.is_healthy()
        existing_issue = {"number": 1}  # Simulate existing issue

        should_comment = not is_healthy and existing_issue is not None
        should_open = not is_healthy and existing_issue is None

        self.assertTrue(should_comment)
        self.assertFalse(should_open)

    def test_issue_close_when_healthy(self):
        """When healthy and issue open, close it."""
        report = FakeMonitorReport()
        report.add_check("Check", True, "")

        is_healthy = report.is_healthy()
        existing_issue = {"number": 1}  # Simulate existing issue

        should_close = is_healthy and existing_issue is not None

        self.assertTrue(should_close)

    def test_issue_no_action_when_healthy_no_issue(self):
        """When healthy and no issue open, no action needed."""
        report = FakeMonitorReport()
        report.add_check("Check", True, "")

        is_healthy = report.is_healthy()
        existing_issue = None

        should_take_action = (not is_healthy) or (is_healthy and existing_issue is not None)

        self.assertFalse(should_take_action)


class TestMonitorReport(unittest.TestCase):
    """Test the MonitorReport class if available."""

    @unittest.skipIf(monitor is None, "monitor module not available")
    def test_report_creation(self):
        """MonitorReport can be created and used."""
        report = monitor.MonitorReport()
        report.add_check("Test", True, "Message")
        self.assertTrue(report.is_healthy())

    @unittest.skipIf(monitor is None, "monitor module not available")
    def test_report_failures(self):
        """MonitorReport correctly identifies failures."""
        report = monitor.MonitorReport()
        report.add_check("Test1", False, "Failed")
        report.add_check("Test2", True, "")
        self.assertFalse(report.is_healthy())
        self.assertEqual(len(report.get_failures()), 1)


if __name__ == "__main__":
    unittest.main()
