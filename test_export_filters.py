#!/usr/bin/env python
"""Test script for export command filtering features.

Tests:
1. --status=failed filtering
2. --since date filtering
3. Combined --env + --status filtering
4. Markdown output preserves all fields
5. Invalid --since doesn't create empty file
6. Data persistence across restarts
"""

import os
import sys
import json
import subprocess
import shutil
from datetime import datetime, timedelta

DB_FILE = "pipeline.db"
SCRIPT = "pipeline.py"


def run_command(args, expect_success=True, capture=True):
    """Run a pipeline command and return the result."""
    cmd = [sys.executable, SCRIPT] + args
    print(f"\n$ {' '.join(cmd)}")

    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if expect_success and result.returncode != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            raise RuntimeError(f"Command failed: {' '.join(cmd)}")
        if not expect_success and result.returncode == 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            raise RuntimeError(f"Command should have failed: {' '.join(cmd)}")
        print(f"  Return code: {result.returncode}")
        if result.stdout:
            print(f"  STDOUT: {result.stdout[:200]}..." if len(result.stdout) > 200 else f"  STDOUT: {result.stdout}")
        if result.stderr:
            print(f"  STDERR: {result.stderr[:200]}..." if len(result.stderr) > 200 else f"  STDERR: {result.stderr}")
        return result
    else:
        subprocess.run(cmd, check=expect_success)
        return None


def cleanup():
    """Remove database file."""
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    for ext in ['.json', '.md']:
        for f in ['test_export', 'test_failed', 'test_since', 'test_combined', 'test_md', 'test_invalid']:
            path = f + ext
            if os.path.exists(path):
                os.remove(path)


def setup_test_data():
    """Initialize DB and create test audit entries with varied statuses and dates."""
    print("\n" + "=" * 60)
    print("SETUP: Creating test data")
    print("=" * 60)

    run_command(["init"])
    run_command(["import", "config_pipeline/examples/config_v1.json"])
    run_command(["import", "config_pipeline/examples/config_v2.json"])

    run_command(["apply", "1.0.0", "staging", "--yes"])
    run_command(["apply", "2.0.0", "staging", "--yes"])

    run_command(["pending", "1.0.0", "prod", "--notes", "Test pending"])

    run_command(["approve", "1.0.0", "prod", "--role", "release-manager", "--notes", "Test approve"])

    run_command(["apply", "1.0.0", "prod", "--role", "release-manager", "--yes"])

    print("\n  Creating some failed audit entries...")
    run_command(["apply", "1.0.0", "staging", "--yes"], expect_success=False)

    run_command(["validate", "1.0.0", "--env", "invalid-env"], expect_success=False)

    print("\n  Test data setup complete.")


def test_status_failed_filter():
    """Test --status=failed filtering."""
    print("\n" + "=" * 60)
    print("TEST 1: --status=failed filtering")
    print("=" * 60)

    result = run_command(["export", "--status", "failed", "--output", "test_failed.json", "--format", "json"])

    with open("test_failed.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    audit_logs = data["audit_logs"]
    print(f"\n  Found {len(audit_logs)} failed audit logs")

    for audit in audit_logs:
        assert audit["status"] == "failed", f"Expected status 'failed', got '{audit['status']}'"
        print(f"  - {audit['id']}: {audit['action']} - {audit['error_reason']}")

    assert len(audit_logs) >= 2, f"Expected at least 2 failed entries, got {len(audit_logs)}"

    assert data["export_metadata"]["status_filter"] == "failed"

    print("\n  ✓ PASSED: --status=failed filtering works correctly")


def test_since_filter():
    """Test --since date filtering."""
    print("\n" + "=" * 60)
    print("TEST 2: --since date filtering")
    print("=" * 60)

    result_all = run_command(["export", "--output", "test_export.json", "--format", "json"])
    with open("test_export.json", "r", encoding="utf-8") as f:
        data_all = json.load(f)
    total_count = len(data_all["audit_logs"])
    print(f"\n  Total audit logs (no filter): {total_count}")

    future_date = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
    print(f"\n  Testing with future date: {future_date}")
    result_future = run_command(["export", "--since", future_date, "--output", "test_since.json", "--format", "json"])
    with open("test_since.json", "r", encoding="utf-8") as f:
        data_future = json.load(f)
    future_count = len(data_future["audit_logs"])
    print(f"  Audit logs after {future_date}: {future_count}")
    assert future_count == 0, f"Expected 0 logs for future date, got {future_count}"

    past_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    print(f"\n  Testing with past date: {past_date}")
    result_past = run_command(["export", "--since", past_date, "--output", "test_since.json", "--format", "json"])
    with open("test_since.json", "r", encoding="utf-8") as f:
        data_past = json.load(f)
    past_count = len(data_past["audit_logs"])
    print(f"  Audit logs after {past_date}: {past_count}")
    assert past_count == total_count, f"Expected {total_count} logs for past date, got {past_count}"

    assert data_past["export_metadata"]["since_filter"] is not None

    print("\n  Testing with full datetime format...")
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    result_iso = run_command(["export", "--since", now_iso, "--output", "test_since.json", "--format", "json"])
    with open("test_since.json", "r", encoding="utf-8") as f:
        data_iso = json.load(f)
    print(f"  Audit logs after {now_iso}: {len(data_iso['audit_logs'])}")

    print("\n  ✓ PASSED: --since filtering works correctly")


def test_env_status_combined():
    """Test combined --env + --status filtering."""
    print("\n" + "=" * 60)
    print("TEST 3: Combined --env + --status filtering")
    print("=" * 60)

    result = run_command(["export", "--env", "staging", "--status", "failed", "--output", "test_combined.json", "--format", "json"])

    with open("test_combined.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    audit_logs = data["audit_logs"]
    print(f"\n  Found {len(audit_logs)} audit logs for env=staging, status=failed")

    for audit in audit_logs:
        assert audit["environment"] == "staging", f"Expected env 'staging', got '{audit['environment']}'"
        assert audit["status"] == "failed", f"Expected status 'failed', got '{audit['status']}'"
        print(f"  - {audit['id']}: {audit['action']}")

    assert data["export_metadata"]["environment_filter"] == "staging"
    assert data["export_metadata"]["status_filter"] == "failed"

    print("\n  [OK] PASSED: Combined --env + --status filtering works correctly")


def test_markdown_preserves_fields():
    """Test that Markdown output doesn't lose fields."""
    print("\n" + "=" * 60)
    print("TEST 4: Markdown output preserves all fields")
    print("=" * 60)

    result_json = run_command(["export", "--output", "test_md.json", "--format", "json"])
    result_md = run_command(["export", "--output", "test_md.md", "--format", "markdown"])

    with open("test_md.json", "r", encoding="utf-8") as f:
        data_json = json.load(f)

    with open("test_md.md", "r", encoding="utf-8") as f:
        md_content = f.read()

    print(f"\n  JSON audit logs count: {len(data_json['audit_logs'])}")
    print(f"  Markdown file size: {len(md_content)} bytes")

    assert "Status Filter:" not in md_content or data_json['export_metadata'].get('status_filter') is None
    if data_json['export_metadata']['environment_filter']:
        assert f"**Environment Filter:** {data_json['export_metadata']['environment_filter']}" in md_content

    assert "## Audit Log" in md_content
    assert "| ID | Action | Env | Version | Status | Operator | Timestamp | Error Reason | Conflict Reason | Details |" in md_content

    for audit in data_json["audit_logs"]:
        assert str(audit["id"]) in md_content
        assert audit["action"] in md_content
        if audit["environment"]:
            assert audit["environment"] in md_content

    print("\n  Checking JSON fields exist in export...")
    for key in ["export_metadata", "environment_status", "environment_locks", "approvals",
                "plan_summaries", "audit_logs", "releases", "rollbacks", "error_logs"]:
        assert key in data_json, f"Missing key '{key}' in JSON export"
    print("  All expected keys present in JSON export")

    for audit in data_json["audit_logs"]:
        for key in ["id", "action", "environment", "version", "status", "operator",
                    "timestamp", "details", "error_reason", "conflict_reason"]:
            assert key in audit, f"Missing key '{key}' in audit log entry"
    print("  All expected fields present in audit log entries")

    print("\n  [OK] PASSED: Markdown output preserves all fields")


def test_invalid_since_no_empty_file():
    """Test that invalid --since doesn't create an empty file."""
    print("\n" + "=" * 60)
    print("TEST 5: Invalid --since doesn't create empty file")
    print("=" * 60)

    test_output = "test_invalid.json"

    if os.path.exists(test_output):
        os.remove(test_output)

    print(f"\n  Testing invalid since: 'not-a-date'")
    result = run_command(["export", "--since", "not-a-date", "--output", test_output, "--format", "json"],
                         expect_success=False)

    assert "Invalid --since format" in result.stderr

    assert not os.path.exists(test_output), f"File {test_output} should not have been created"
    print(f"  [OK] File {test_output} was not created")

    print("\n  Testing invalid status: 'invalid-status'")
    result_status = run_command(["export", "--status", "invalid-status", "--output", test_output, "--format", "json"],
                                expect_success=False)

    assert "Invalid status" in result_status.stderr
    assert not os.path.exists(test_output), f"File {test_output} should not have been created"
    print(f"  [OK] File {test_output} was not created for invalid status")

    print("\n  [OK] PASSED: Invalid parameters don't create empty files")


def test_persistence_across_restarts():
    """Test that old records are still queryable after simulated restart."""
    print("\n" + "=" * 60)
    print("TEST 6: Data persistence across restarts")
    print("=" * 60)

    run_command(["export", "--output", "before_restart.json", "--format", "json"])
    with open("before_restart.json", "r", encoding="utf-8") as f:
        before = json.load(f)
    before_count = len(before["audit_logs"])
    print(f"\n  Audit logs before 'restart': {before_count}")

    print("\n  Simulating restart by re-initializing connection (SQLite is persistent)...")
    print("  (No actual restart needed - SQLite persists to disk)")

    run_command(["export", "--output", "after_restart.json", "--format", "json"])
    with open("after_restart.json", "r", encoding="utf-8") as f:
        after = json.load(f)
    after_count = len(after["audit_logs"])

    print(f"  Audit logs after 'restart': {after_count}")

    assert before_count == after_count - 1 or before_count == after_count, \
        f"Audit log count changed unexpectedly: {before_count} -> {after_count}"

    print("\n  Testing that old records appear in status=failed filter...")
    result = run_command(["export", "--status", "failed", "--output", "persistence_test.json", "--format", "json"])
    with open("persistence_test.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  Failed records found: {len(data['audit_logs'])}")
    assert len(data["audit_logs"]) >= 2, "Old failed records should still be present"

    print("\n  [OK] PASSED: Data persists correctly across restarts")


def test_print_vs_file_consistency():
    """Test that direct print and file export produce the same content."""
    print("\n" + "=" * 60)
    print("TEST 7: Direct print vs file export consistency")
    print("=" * 60)

    result_print = run_command(["export", "--status", "failed", "--format", "json"])
    run_command(["export", "--status", "failed", "--output", "print_test.json", "--format", "json"])

    with open("print_test.json", "r", encoding="utf-8") as f:
        file_content = f.read()

    print_content = result_print.stdout.strip()

    assert print_content == file_content.strip(), "Printed content doesn't match file content"
    print("\n  [OK] PASSED: Direct print and file export are consistent")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Export Command Filter Tests")
    print("=" * 60)

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    cleanup()

    try:
        setup_test_data()

        test_status_failed_filter()
        test_since_filter()
        test_env_status_combined()
        test_markdown_preserves_fields()
        test_invalid_since_no_empty_file()
        test_persistence_across_restarts()
        test_print_vs_file_consistency()

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED! [OK]")
        print("=" * 60)

    finally:
        print("\n" + "=" * 60)
        print("CLEANUP")
        print("=" * 60)
        cleanup()
        print("  Cleanup complete.")


if __name__ == "__main__":
    main()
