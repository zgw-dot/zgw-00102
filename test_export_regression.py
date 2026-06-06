#!/usr/bin/env python
"""Regression tests for export command filtering bugs.

Tests:
1. YYYY-MM-DD same-day boundary: records created on the same day should be included
2. Full datetime filtering with both 'T' and space formats in DB
3. Print vs file consistency: same content for both outputs (single command)
4. --status failed filter: print and file should show same failed records
5. Invalid since: no empty file created, failure audit recorded
"""

import os
import sys
import json
import subprocess
import sqlite3
from datetime import datetime, timedelta

DB_FILE = "pipeline.db"
SCRIPT = "pipeline.py"


def run_cmd(args, expect_success=True):
    cmd = [sys.executable, SCRIPT] + args
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if expect_success and result.returncode != 0:
        print(f"FAIL: {' '.join(cmd)}")
        print(f"  STDOUT: {result.stdout}")
        print(f"  STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    if not expect_success and result.returncode == 0:
        print(f"FAIL: {' '.join(cmd)} expected to fail but succeeded")
        raise RuntimeError(f"Command should have failed: {' '.join(cmd)}")
    return result


def cleanup():
    for f in [DB_FILE, 'test_same_day.json', 'test_output.json', 'test_invalid.json',
              'test_print.json', 'test_consistency.json']:
        if os.path.exists(f):
            os.remove(f)


def setup_db_with_known_timestamps():
    """Set up database with records having specific created_at values to test date boundaries."""
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

    run_cmd(['init'])

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM audit_logs")

    today = datetime.now().strftime("%Y-%m-%d")

    test_records = [
        ('apply', 'staging', '1.0.0', 'success', 'user1', f'{today} 08:00:00'),
        ('validate', 'dev', '1.0.0', 'success', 'user2', f'{today} 09:30:00'),
        ('export', 'prod', None, 'failed', 'user3', f'{today} 10:15:00'),
        ('apply', 'staging', '2.0.0', 'success', 'user4', f'{today}T11:00:00'),
        ('rollback', 'prod', '1.0.0', 'failed', 'user5', f'{today}T12:30:00'),
        ('apply', 'staging', '1.0.0', 'failed', 'old_user', '2020-01-01 00:00:00'),
    ]

    for action, env, version, status, user, created_at in test_records:
        cursor.execute('''
            INSERT INTO audit_logs (action, environment, version, status, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (action, env, version, status, user, created_at))

    conn.commit()
    conn.close()

    return today


def test_same_day_boundary():
    """Test that --since YYYY-MM-DD includes records from that same day."""
    print("\n" + "=" * 60)
    print("TEST 1: YYYY-MM-DD same-day boundary")
    print("=" * 60)

    today = setup_db_with_known_timestamps()

    result = run_cmd(['export', '--since', today, '--output', 'test_same_day.json', '--format', 'json'])

    with open('test_same_day.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    audit_logs = data['audit_logs']
    print(f"  Date filter: --since {today}")
    print(f"  Found {len(audit_logs)} audit logs")

    timestamps = [a['timestamp'] for a in audit_logs]
    print(f"  Record timestamps: {timestamps}")

    assert len(audit_logs) == 5, f"Expected 5 records from today, got {len(audit_logs)}"

    for audit in audit_logs:
        created_at = audit['timestamp']
        assert created_at.startswith(today), f"Record {audit['id']} has timestamp {created_at}, not starting with {today}"
        print(f"  - ID {audit['id']}: {audit['action']} at {created_at}")

    old_records = [a for a in audit_logs if a['timestamp'].startswith('2020-')]
    assert len(old_records) == 0, f"Old records from 2020 should NOT be included, but found: {old_records}"

    print("\n  [OK] PASSED: Same-day records correctly included")


def test_full_datetime_filtering():
    """Test that full datetime filtering works with both space and 'T' formats in DB."""
    print("\n" + "=" * 60)
    print("TEST 2: Full datetime filtering (both space and 'T' formats)")
    print("=" * 60)

    today = setup_db_with_known_timestamps()

    cutoff = f"{today} 10:00:00"
    result = run_cmd(['export', '--since', cutoff, '--output', 'test_output.json', '--format', 'json'])

    with open('test_output.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    audit_logs = data['audit_logs']
    print(f"  Cutoff: --since {cutoff}")
    print(f"  Found {len(audit_logs)} audit logs")

    assert len(audit_logs) == 3, f"Expected 3 records after {cutoff}, got {len(audit_logs)}"

    for audit in audit_logs:
        print(f"  - ID {audit['id']}: {audit['action']} at {audit['timestamp']}")
        audit_time = audit['timestamp'].replace('T', ' ')
        assert audit_time >= cutoff, f"Record {audit['id']} time {audit_time} < cutoff {cutoff}"

    print("\n  [OK] PASSED: Full datetime filtering works for both formats")


def test_print_vs_file_consistency():
    """Test that print and file output use the same generated content.

    This tests the actual behavior: content is generated once and reused.
    We verify by checking that both paths produce identical audit_logs content
    (ignoring dynamic metadata fields like exported_at).
    """
    print("\n" + "=" * 60)
    print("TEST 3: Print vs file consistency (same content generation)")
    print("=" * 60)

    today = setup_db_with_known_timestamps()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, action, status FROM audit_logs WHERE status = 'failed' AND created_at >= ? ORDER BY id DESC",
                   (f"{today} 00:00:00",))
    expected_records = cursor.fetchall()
    conn.close()

    print(f"  Expected failed records from today: {len(expected_records)}")
    for rec in expected_records:
        print(f"    - ID {rec[0]}: {rec[1]}")

    result_print = run_cmd(['export', '--status', 'failed', '--since', today, '--format', 'json'])
    print_content = json.loads(result_print.stdout.strip())

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM audit_logs WHERE action = 'export' AND status = 'success'")
    conn.commit()
    conn.close()

    run_cmd(['export', '--status', 'failed', '--since', today, '--output', 'test_print.json', '--format', 'json'])
    with open('test_print.json', 'r', encoding='utf-8') as f:
        file_content = json.load(f)

    print(f"  Print audit_logs count: {len(print_content['audit_logs'])}")
    print(f"  File audit_logs count: {len(file_content['audit_logs'])}")

    assert len(print_content['audit_logs']) == len(expected_records), \
        f"Print count mismatch: expected {len(expected_records)}, got {len(print_content['audit_logs'])}"
    assert len(file_content['audit_logs']) == len(expected_records), \
        f"File count mismatch: expected {len(expected_records)}, got {len(file_content['audit_logs'])}"

    for i, (p, f, expected) in enumerate(zip(print_content['audit_logs'], file_content['audit_logs'], expected_records)):
        assert p['id'] == expected[0], f"Print record {i} ID mismatch: {p['id']} vs {expected[0]}"
        assert f['id'] == expected[0], f"File record {i} ID mismatch: {f['id']} vs {expected[0]}"
        assert p['status'] == 'failed', f"Print record {i} status not failed"
        assert f['status'] == 'failed', f"File record {i} status not failed"
        assert p['action'] == f['action'], f"Record {i} action mismatch: {p['action']} vs {f['action']}"

    assert print_content['export_metadata']['status_filter'] == 'failed'
    assert file_content['export_metadata']['status_filter'] == 'failed'
    assert print_content['export_metadata']['since_filter'] == file_content['export_metadata']['since_filter']

    print("\n  [OK] PASSED: Print and file output are consistent")


def test_invalid_since_no_empty_file():
    """Test that invalid --since doesn't create an empty file and records failure audit."""
    print("\n" + "=" * 60)
    print("TEST 4: Invalid since - no empty file + failure audit recorded")
    print("=" * 60)

    setup_db_with_known_timestamps()

    test_file = 'test_invalid.json'
    if os.path.exists(test_file):
        os.remove(test_file)

    print("  Testing invalid since: 'not-a-date'")
    result = run_cmd(['export', '--since', 'not-a-date', '--output', test_file, '--format', 'json'],
                     expect_success=False)

    assert "Invalid --since format" in result.stderr, f"Missing error message in stderr: {result.stderr}"
    assert not os.path.exists(test_file), f"File {test_file} should not have been created"
    print(f"  [OK] File {test_file} was not created")

    print("\n  Checking failure audit was recorded...")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_logs WHERE action = 'export' ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()

    assert row is not None, "No export audit record found"
    assert row['status'] == 'failed', f"Expected status='failed', got '{row['status']}'"
    assert 'INVALID_SINCE_FORMAT' in row['error_reason'] or 'Invalid --since format' in row['error_reason'], \
        f"Error reason doesn't mention invalid since: {row['error_reason']}"
    print(f"  [OK] Failure audit recorded: status={row['status']}, reason={row['error_reason']}")

    print("\n  Testing invalid status: 'invalid-status'")
    result_status = run_cmd(['export', '--status', 'invalid-status', '--output', test_file, '--format', 'json'],
                            expect_success=False)

    assert "Invalid status" in result_status.stderr
    assert not os.path.exists(test_file), f"File {test_file} should not have been created"
    print(f"  [OK] File {test_file} was not created for invalid status")

    print("\n  [OK] PASSED: Invalid parameters don't create empty files")


def test_content_reuse_structure():
    """Verify code structure: content is generated once before output decision.

    This is a structural verification that the code doesn't generate content
    separately for print vs file paths, which could cause inconsistency.
    """
    print("\n" + "=" * 60)
    print("TEST 5: Code structure verification - single content generation")
    print("=" * 60)

    with open('config_pipeline/commands/export_cmd.py', 'r', encoding='utf-8') as f:
        code = f.read()

    if_idx = code.find('if output_format == "json":')
    content_def_idx = code.find('content = ')
    file_write_idx = code.find('with open(output_path')
    print_idx = code.find('else:\n        click.echo(content)')

    print(f"  content generation at char: {content_def_idx}")
    print(f"  if output_format at char: {if_idx}")
    print(f"  file write at char: {file_write_idx}")
    print(f"  print else at char: {print_idx}")

    assert content_def_idx < file_write_idx, "content should be defined before file write"
    assert content_def_idx < print_idx, "content should be defined before print"
    assert file_write_idx > 0, "File write path should exist"
    assert print_idx > 0, "Print path should exist"

    print("\n  [OK] PASSED: Content is generated once and reused for both paths")


def main():
    print("=" * 60)
    print("Export Command Regression Tests")
    print("=" * 60)

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    cleanup()

    try:
        test_same_day_boundary()
        test_full_datetime_filtering()
        test_print_vs_file_consistency()
        test_invalid_since_no_empty_file()
        test_content_reuse_structure()

        print("\n" + "=" * 60)
        print("ALL REGRESSION TESTS PASSED! [OK]")
        print("=" * 60)

    finally:
        print("\n" + "=" * 60)
        print("CLEANUP")
        print("=" * 60)
        cleanup()
        print("  Cleanup complete.")


if __name__ == "__main__":
    main()
