#!/usr/bin/env python
"""Regression tests for release window management module.

Tests:
1. Window CRUD operations (create/list/disable)
2. Cross-restart persistence (windows survive DB restart)
3. Overlapping window detection
4. Invalid time/unknown environment errors
5. Permission control (release-manager vs developer for prod)
6. Apply blocked by closed window (no version advance, no success release)
7. Batch apply blocked by closed window
8. Rollback blocked by closed window
9. --override-window success (release-manager only)
10. --override-window failure (developer, missing reason)
11. Audit log export with window events
12. Window_override_reason recorded in release records
"""

import os
import sys
import json
import subprocess
import sqlite3
from datetime import datetime, timedelta

DB_FILE = "pipeline.db"
SCRIPT = "pipeline.py"


def run_cmd(args, expect_success=True, env=None):
    """Run a CLI command and return the result."""
    cmd = [sys.executable, SCRIPT] + args
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        env=merged_env
    )
    if expect_success and result.returncode != 0:
        print(f"FAIL: {' '.join(cmd)}")
        print(f"  STDOUT: {result.stdout}")
        print(f"  STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    if not expect_success and result.returncode == 0:
        print(f"FAIL: {' '.join(cmd)} expected to fail but succeeded")
        print(f"  STDOUT: {result.stdout}")
        print(f"  STDERR: {result.stderr}")
        raise RuntimeError(f"Command should have failed: {' '.join(cmd)}")
    return result


def cleanup():
    """Clean up test artifacts."""
    for f in [DB_FILE, "windows_before.txt", "windows_after.txt", "audit_test.json", "audit_test.md"]:
        if os.path.exists(f):
            os.remove(f)


def init_db_with_configs():
    """Initialize database with configs for testing."""
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v3.json'])


def get_db_state():
    """Get current database state for comparison."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT name, current_version FROM environments ORDER BY name")
    envs = {r['name']: r['current_version'] for r in cursor.fetchall()}

    cursor.execute("SELECT environment, is_locked FROM environment_locks ORDER BY environment")
    locks = {r['environment']: r['is_locked'] == 1 for r in cursor.fetchall()}

    cursor.execute("SELECT version, environment, status, window_override_reason FROM releases ORDER BY id DESC")
    releases = [(r['version'], r['environment'], r['status'], r['window_override_reason']) for r in cursor.fetchall()]

    cursor.execute("SELECT id, environment, start_time, end_time, reason, is_enabled FROM release_windows ORDER BY id")
    windows = [(r['id'], r['environment'], r['start_time'], r['end_time'], r['reason'], r['is_enabled'] == 1) for r in cursor.fetchall()]

    conn.close()

    return {
        'environments': envs,
        'locks': locks,
        'releases': releases,
        'windows': windows,
    }


def get_audit_logs():
    """Get audit logs for verification."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT action, status, error_reason, details FROM audit_logs ORDER BY id DESC")
    logs = []
    for r in cursor.fetchall():
        details = None
        if r['details']:
            try:
                details = json.loads(r['details'])
            except:
                details = r['details']
        logs.append({
            'action': r['action'],
            'status': r['status'],
            'error_reason': r['error_reason'],
            'details': details
        })
    conn.close()
    return logs


def get_window_times():
    """Generate valid window time strings for testing."""
    now = datetime.now()
    past = now - timedelta(days=365)
    future = now + timedelta(days=365)
    return past.isoformat(), future.isoformat()


def test_1_window_crud_operations():
    """Test 1: Window CRUD operations (create/list/disable)."""
    print("\n=== Test 1: Window CRUD Operations ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()

    state_before = get_db_state()
    assert len(state_before['windows']) == 0, "No windows should exist initially"

    result = run_cmd([
        'window', 'create', 'dev',
        start_time, end_time,
        '--reason', 'Test freeze',
        '--role', 'developer'
    ])
    assert "RELEASE WINDOW CREATED" in result.stdout
    assert "Environment:    dev" in result.stdout
    assert "Reason:         Test freeze" in result.stdout

    state_after_create = get_db_state()
    assert len(state_after_create['windows']) == 1, "One window should exist"
    window_id, env, s_time, e_time, reason, enabled = state_after_create['windows'][0]
    assert env == 'dev'
    assert reason == 'Test freeze'
    assert enabled == True

    result = run_cmd(['window', 'list'])
    assert "Test freeze" in result.stdout
    assert "dev" in result.stdout

    result = run_cmd(['window', 'status', 'dev'])
    assert "CLOSED" in result.stdout

    result = run_cmd(['window', 'disable', str(window_id), '--role', 'developer'])
    assert "RELEASE WINDOW DISABLED" in result.stdout

    state_after_disable = get_db_state()
    _, _, _, _, _, enabled = state_after_disable['windows'][0]
    assert enabled == False, "Window should be disabled"

    result = run_cmd(['window', 'list', '--all'])
    assert "DISABLED" in result.stdout

    print("✓ Test 1 passed: Window CRUD operations work correctly")


def test_2_cross_restart_persistence():
    """Test 2: Cross-restart persistence (windows survive DB close/reopen)."""
    print("\n=== Test 2: Cross-Restart Persistence ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()

    run_cmd(['window', 'create', 'dev', start_time, end_time, '--reason', 'Persistence test 1', '--role', 'developer'])
    run_cmd(['window', 'create', 'staging', start_time, end_time, '--reason', 'Persistence test 2', '--role', 'developer'])

    state_before = get_db_state()
    assert len(state_before['windows']) == 2

    run_cmd(['window', 'list'], expect_success=True, env=None)

    logs_before = get_audit_logs()
    create_events_before = [l for l in logs_before if l['action'] == 'create_release_window']
    assert len(create_events_before) == 2

    db_path = os.path.abspath(DB_FILE)
    assert os.path.exists(db_path), "Database file should exist"

    state_after = get_db_state()
    assert len(state_after['windows']) == 2, "Windows should persist across DB connections"

    logs_after = get_audit_logs()
    create_events_after = [l for l in logs_after if l['action'] == 'create_release_window']
    assert len(create_events_after) == 2, "Audit logs should persist"

    result = run_cmd(['apply', '1.0.0', 'dev', '--yes'], expect_success=False)
    assert "closed release window" in result.stderr.lower() or "RELEASE_WINDOW_CLOSED" in result.stderr

    print("✓ Test 2 passed: Windows persist across restarts")


def test_3_overlapping_window_detection():
    """Test 3: Overlapping window detection and prevention."""
    print("\n=== Test 3: Overlapping Window Detection ===")
    cleanup()
    init_db_with_configs()

    now = datetime.now()
    start1 = now.isoformat()
    end1 = (now + timedelta(days=7)).isoformat()
    start2 = (now + timedelta(days=3)).isoformat()
    end2 = (now + timedelta(days=10)).isoformat()

    run_cmd(['window', 'create', 'dev', start1, end1, '--reason', 'Window 1', '--role', 'developer'])

    result = run_cmd([
        'window', 'create', 'dev', start2, end2,
        '--reason', 'Window 2 (overlapping)',
        '--role', 'developer'
    ], expect_success=False)
    assert "Overlapping release window detected" in result.stderr
    assert "OVERLAPPING_WINDOW" in result.stderr

    state = get_db_state()
    assert len(state['windows']) == 1, "Only first window should exist"

    start3 = (now + timedelta(days=8)).isoformat()
    end3 = (now + timedelta(days=14)).isoformat()
    run_cmd(['window', 'create', 'dev', start3, end3, '--reason', 'Window 3 (non-overlapping)', '--role', 'developer'])

    state = get_db_state()
    assert len(state['windows']) == 2, "Non-overlapping window should be created"

    logs = get_audit_logs()
    overlap_errors = [l for l in logs if l['action'] == 'window_create' and l['status'] == 'failed']
    assert len(overlap_errors) == 1, "Overlap error should be logged"

    print("✓ Test 3 passed: Overlapping windows are correctly detected")


def test_4_invalid_input_errors():
    """Test 4: Invalid time format and unknown environment errors."""
    print("\n=== Test 4: Invalid Input Errors ===")
    cleanup()
    init_db_with_configs()

    result = run_cmd([
        'window', 'create', 'dev',
        'invalid-time', '2024-12-31T23:59:59',
        '--reason', 'Test', '--role', 'developer'
    ], expect_success=False)
    assert "Invalid datetime format" in result.stderr
    assert "INVALID_WINDOW_TIME" in result.stderr

    result = run_cmd([
        'window', 'create', 'dev',
        '2024-12-31T23:59:59', '2024-01-01T00:00:00',
        '--reason', 'Test', '--role', 'developer'
    ], expect_success=False)
    assert "End time" in result.stderr and "after start time" in result.stderr

    result = run_cmd([
        'window', 'create', 'invalid-env',
        '2024-01-01T00:00:00', '2024-12-31T23:59:59',
        '--reason', 'Test', '--role', 'developer'
    ], expect_success=False)
    assert "Invalid environment" in result.stderr

    logs = get_audit_logs()
    error_logs = [l for l in logs if l['status'] == 'failed' and l['action'] == 'window_create']
    assert len(error_logs) == 3, "All invalid inputs should be logged"

    print("✓ Test 4 passed: Invalid inputs are correctly rejected")


def test_5_permission_control_prod():
    """Test 5: Permission control for prod windows (release-manager only)."""
    print("\n=== Test 5: Permission Control for Prod ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()

    result = run_cmd([
        'window', 'create', 'prod',
        start_time, end_time,
        '--reason', 'Prod freeze',
        '--role', 'developer'
    ], expect_success=False)
    assert "Permission denied" in result.stderr
    assert "PERMISSION_DENIED" in result.stderr

    run_cmd([
        'window', 'create', 'prod',
        start_time, end_time,
        '--reason', 'Prod freeze',
        '--role', 'release-manager'
    ])

    state = get_db_state()
    assert len(state['windows']) == 1
    assert state['windows'][0][1] == 'prod'

    result = run_cmd(['window', 'disable', '1', '--role', 'developer'], expect_success=False)
    assert "Permission denied" in result.stderr

    run_cmd(['window', 'disable', '1', '--role', 'release-manager'])

    state = get_db_state()
    assert state['windows'][0][5] == False

    run_cmd([
        'window', 'create', 'dev',
        start_time, end_time,
        '--reason', 'Dev freeze',
        '--role', 'developer'
    ])
    state = get_db_state()
    assert len(state['windows']) == 2

    print("✓ Test 5 passed: Prod window permissions are correctly enforced")


def test_6_apply_blocked_by_closed_window():
    """Test 6: Apply is blocked by closed window - no version advance, no success release."""
    print("\n=== Test 6: Apply Blocked by Closed Window ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()
    run_cmd(['window', 'create', 'dev', start_time, end_time, '--reason', 'Freeze', '--role', 'developer'])

    state_before = get_db_state()
    assert state_before['environments']['dev'] is None
    assert len(state_before['releases']) == 0

    result = run_cmd(['apply', '1.0.0', 'dev', '--yes'], expect_success=False)
    assert "closed release window" in result.stderr.lower() or "RELEASE_WINDOW_CLOSED" in result.stderr

    state_after = get_db_state()
    assert state_after['environments']['dev'] is None, "Version should NOT advance"
    assert len(state_after['releases']) == 0, "No success release should be created"

    logs = get_audit_logs()
    window_blocked = [l for l in logs if l['action'] == 'apply' and l['status'] == 'window_blocked']
    assert len(window_blocked) == 1, "window_blocked event should be logged"

    error_logs_conn = sqlite3.connect(DB_FILE)
    error_logs_conn.row_factory = sqlite3.Row
    cursor = error_logs_conn.cursor()
    cursor.execute("SELECT * FROM error_logs WHERE error_code = 'RELEASE_WINDOW_CLOSED'")
    errors = cursor.fetchall()
    error_logs_conn.close()
    assert len(errors) == 1, "Error should be logged in error_logs"

    print("✓ Test 6 passed: Apply blocked correctly - no version advance, no success release")


def test_7_batch_apply_blocked_by_closed_window():
    """Test 7: Batch apply is blocked by closed window."""
    print("\n=== Test 7: Batch Apply Blocked by Closed Window ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()
    run_cmd(['window', 'create', 'dev', start_time, end_time, '--reason', 'Freeze', '--role', 'developer'])

    state_before = get_db_state()
    assert state_before['environments']['dev'] is None
    assert state_before['environments']['staging'] is None

    result = run_cmd(['batch', 'apply', 'dev:1.0.0', 'staging:1.0.0', '--yes'], expect_success=False)
    assert "FAILED" in result.stdout or "closed release window" in result.stderr.lower()

    state_after = get_db_state()
    assert state_after['environments']['dev'] is None, "dev version should NOT advance"
    assert state_after['environments']['staging'] is None, "staging version should NOT advance (skipped)"

    releases = [r for r in state_after['releases'] if r[2] == 'success']
    assert len(releases) == 0, "No success releases should be created"

    logs = get_audit_logs()
    skips = [l for l in logs if l['action'] == 'batch_apply' and l['status'] == 'skipped']
    assert len(skips) >= 1, "Subsequent step should be skipped"

    print("✓ Test 7 passed: Batch apply blocked correctly")


def test_8_rollback_blocked_by_closed_window():
    """Test 8: Rollback is blocked by closed window."""
    print("\n=== Test 8: Rollback Blocked by Closed Window ===")
    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'dev', '--yes'])

    state_before_window = get_db_state()
    assert state_before_window['environments']['dev'] == '1.0.0'

    start_time, end_time = get_window_times()
    run_cmd(['window', 'create', 'dev', start_time, end_time, '--reason', 'Freeze', '--role', 'developer'])

    result = run_cmd(['rollback', 'dev', '1.0.0', '--reason', 'Test rollback', '--yes'], expect_success=False)
    assert "closed release window" in result.stderr.lower() or "RELEASE_WINDOW_CLOSED" in result.stderr

    state_after = get_db_state()
    assert state_after['environments']['dev'] == '1.0.0', "Version should NOT change"

    logs = get_audit_logs()
    window_blocked = [l for l in logs if l['action'] == 'rollback' and l['status'] == 'window_blocked']
    assert len(window_blocked) == 1, "window_blocked event should be logged"

    print("✓ Test 8 passed: Rollback blocked correctly")


def test_9_override_window_success():
    """Test 9: --override-window success (release-manager only)."""
    print("\n=== Test 9: Override Window Success ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()
    run_cmd(['window', 'create', 'dev', start_time, end_time, '--reason', 'Freeze', '--role', 'developer'])

    result = run_cmd([
        'apply', '1.0.0', 'dev', '--yes',
        '--override-window', '--override-reason', 'Emergency hotfix',
        '--role', 'release-manager'
    ])
    assert "Release window overridden" in result.stdout
    assert "Emergency hotfix" in result.stdout
    assert "SUCCESS" in result.stdout

    state = get_db_state()
    assert state['environments']['dev'] == '1.0.0', "Version should advance"
    releases = [r for r in state['releases'] if r[2] == 'success']
    assert len(releases) == 1, "Success release should be created"
    assert releases[0][3] is not None, "window_override_reason should be recorded"

    override_reason = json.loads(releases[0][3])
    assert override_reason['override_reason'] == 'Emergency hotfix'
    assert override_reason['window_reason'] == 'Freeze'

    logs = get_audit_logs()
    overridden = [l for l in logs if l['action'] == 'apply' and l['status'] == 'window_overridden']
    assert len(overridden) == 1, "window_overridden event should be logged"
    assert overridden[0]['details']['override_reason'] == 'Emergency hotfix'

    print("✓ Test 9 passed: --override-window works correctly for release-manager")


def test_10_override_window_failure():
    """Test 10: --override-window failure cases (developer, missing reason)."""
    print("\n=== Test 10: Override Window Failures ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()
    run_cmd(['window', 'create', 'dev', start_time, end_time, '--reason', 'Freeze', '--role', 'developer'])

    result = run_cmd([
        'apply', '1.0.0', 'dev', '--yes',
        '--override-window', '--override-reason', 'Emergency',
        '--role', 'developer'
    ], expect_success=False)
    assert "Permission denied to override" in result.stderr
    assert "OVERRIDE_PERMISSION_DENIED" in result.stderr

    logs = get_audit_logs()
    override_denied = [l for l in logs if l['action'] == 'apply' and l['status'] == 'window_override_denied']
    assert len(override_denied) == 1, "window_override_denied event should be logged"

    result = run_cmd([
        'apply', '1.0.0', 'dev', '--yes',
        '--override-window',
        '--role', 'release-manager'
    ], expect_success=False)
    assert "Override reason is required" in result.stderr or "INVALID_WINDOW_TIME" in result.stderr

    state = get_db_state()
    assert state['environments']['dev'] is None, "Version should NOT advance"

    print("✓ Test 10 passed: Override failures are correctly handled")


def test_11_audit_export_with_window_events():
    """Test 11: Audit log export includes window events and override reasons."""
    print("\n=== Test 11: Audit Export with Window Events ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()
    run_cmd(['window', 'create', 'dev', start_time, end_time, '--reason', 'Audit test', '--role', 'developer'])

    run_cmd(['apply', '1.0.0', 'dev', '--yes'], expect_success=False)

    run_cmd([
        'apply', '1.0.0', 'dev', '--yes',
        '--override-window', '--override-reason', 'Hotfix',
        '--role', 'release-manager'
    ])

    run_cmd(['window', 'disable', '1', '--role', 'developer'])

    run_cmd(['export', '--output', 'audit_test.json', '--format', 'json'])

    with open('audit_test.json', 'r', encoding='utf-8') as f:
        audit_data = json.load(f)

    assert 'audit_logs' in audit_data or 'releases' in audit_data, "Audit export should contain data"

    if 'releases' in audit_data:
        releases_with_override = [r for r in audit_data['releases'] if r.get('window_override_reason')]
        assert len(releases_with_override) >= 1, "Release with override should be exported"
        override_data = json.loads(releases_with_override[0]['window_override_reason'])
        assert 'override_reason' in override_data, "Override reason should be in export"

    if 'audit_logs' in audit_data:
        window_actions = ['create_release_window', 'window_blocked', 'window_overridden', 'disable_release_window']
        window_events = [l for l in audit_data['audit_logs'] if l.get('action') in window_actions]
        assert len(window_events) >= 4, "All window events should be exported"

    run_cmd(['export', '--output', 'audit_test.md', '--format', 'markdown'])
    with open('audit_test.md', 'r', encoding='utf-8') as f:
        md_content = f.read()
    assert len(md_content) > 0, "Markdown export should not be empty"

    print("✓ Test 11 passed: Audit export includes window events and override reasons")


def test_12_batch_apply_and_rollback_override():
    """Test 12: Batch apply and rollback with --override-window."""
    print("\n=== Test 12: Batch Apply and Rollback Override ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()
    run_cmd(['window', 'create', 'dev', start_time, end_time, '--reason', 'Freeze', '--role', 'developer'])
    run_cmd(['window', 'create', 'staging', start_time, end_time, '--reason', 'Freeze', '--role', 'developer'])

    result = run_cmd([
        'batch', 'apply', 'dev:1.0.0', 'staging:1.0.0', '--yes',
        '--override-window', '--override-reason', 'Emergency batch release',
        '--role', 'release-manager'
    ])
    assert "Window overridden" in result.stdout
    assert "BATCH COMPLETED SUCCESSFULLY" in result.stdout

    state = get_db_state()
    assert state['environments']['dev'] == '1.0.0'
    assert state['environments']['staging'] == '1.0.0'

    run_cmd(['apply', '2.0.0', 'dev', '--yes', '--override-window', '--override-reason', 'Another hotfix', '--role', 'release-manager'])

    result = run_cmd([
        'rollback', 'dev', '1.0.0', '--reason', 'Rollback hotfix', '--yes',
        '--override-window', '--override-reason', 'Emergency rollback',
        '--role', 'release-manager'
    ])
    assert "Release window overridden" in result.stdout
    assert "Emergency rollback" in result.stdout
    assert "SUCCESS: Rollback to 1.0.0 completed in dev" in result.stdout

    state = get_db_state()
    assert state['environments']['dev'] == '1.0.0'

    releases = [r for r in state['releases'] if r[2] == 'success']
    assert len(releases) == 4, "All operations should create success releases"

    print("✓ Test 12 passed: Batch apply and rollback override work correctly")


def test_13_window_list_filters_and_status():
    """Test 13: Window list filters and status command."""
    print("\n=== Test 13: Window List Filters and Status ===")
    cleanup()
    init_db_with_configs()

    now = datetime.now()
    start_past = (now - timedelta(days=10)).isoformat()
    end_past = (now - timedelta(days=5)).isoformat()
    start_current = (now - timedelta(days=1)).isoformat()
    end_current = (now + timedelta(days=5)).isoformat()
    start_future = (now + timedelta(days=10)).isoformat()
    end_future = (now + timedelta(days=15)).isoformat()

    run_cmd(['window', 'create', 'dev', start_past, end_past, '--reason', 'Past window', '--role', 'developer'])
    run_cmd(['window', 'create', 'dev', start_current, end_current, '--reason', 'Current window', '--role', 'developer'])
    run_cmd(['window', 'create', 'staging', start_future, end_future, '--reason', 'Future window', '--role', 'developer'])

    result = run_cmd(['window', 'list'])
    assert "Past window" in result.stdout
    assert "Current window" in result.stdout
    assert "Future window" in result.stdout

    result = run_cmd(['window', 'list', '--env', 'dev'])
    assert "Past window" in result.stdout
    assert "Current window" in result.stdout
    assert "Future window" not in result.stdout

    result = run_cmd(['window', 'status'])
    assert "dev" in result.stdout
    assert "staging" in result.stdout
    assert "CLOSED" in result.stdout
    assert "OPEN" in result.stdout

    run_cmd(['window', 'disable', '1', '--role', 'developer'])

    result = run_cmd(['window', 'list'])
    assert "Past window" not in result.stdout, "Disabled window should not show by default"

    result = run_cmd(['window', 'list', '--all'])
    assert "Past window" in result.stdout, "Disabled window should show with --all"
    assert "DISABLED" in result.stdout

    print("✓ Test 13 passed: Window list filters and status work correctly")


def test_14_override_reason_persisted_in_release():
    """Test 14: Override reason is properly persisted and retrievable."""
    print("\n=== Test 14: Override Reason Persistence ===")
    cleanup()
    init_db_with_configs()

    start_time, end_time = get_window_times()
    run_cmd(['window', 'create', 'dev', start_time, end_time, '--reason', 'Test freeze', '--role', 'developer'])

    override_reason = "Critical P0 bug fix for payment system"
    run_cmd([
        'apply', '1.0.0', 'dev', '--yes',
        '--override-window', '--override-reason', override_reason,
        '--role', 'release-manager'
    ])

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM releases WHERE status = 'success' ORDER BY id DESC LIMIT 1")
    release = cursor.fetchone()
    conn.close()

    assert release is not None, "Release should exist"
    assert release['window_override_reason'] is not None, "Override reason should be stored"

    override_data = json.loads(release['window_override_reason'])
    assert override_data['override_reason'] == override_reason
    assert override_data['overridden_by'] is not None
    assert override_data['window_id'] == 1
    assert override_data['window_reason'] == 'Test freeze'

    run_cmd(['export', '--output', 'audit_test.json', '--format', 'json'])
    with open('audit_test.json', 'r', encoding='utf-8') as f:
        audit_data = json.load(f)

    if 'releases' in audit_data:
        success_releases = [r for r in audit_data['releases'] if r['status'] == 'success']
        assert len(success_releases) >= 1
        exported_override = json.loads(success_releases[0]['window_override_reason'])
        assert exported_override['override_reason'] == override_reason

    print("✓ Test 14 passed: Override reason is correctly persisted")


def main():
    """Run all regression tests."""
    print("=" * 70)
    print("RELEASE WINDOW REGRESSION TESTS")
    print("=" * 70)

    tests = [
        test_1_window_crud_operations,
        test_2_cross_restart_persistence,
        test_3_overlapping_window_detection,
        test_4_invalid_input_errors,
        test_5_permission_control_prod,
        test_6_apply_blocked_by_closed_window,
        test_7_batch_apply_blocked_by_closed_window,
        test_8_rollback_blocked_by_closed_window,
        test_9_override_window_success,
        test_10_override_window_failure,
        test_11_audit_export_with_window_events,
        test_12_batch_apply_and_rollback_override,
        test_13_window_list_filters_and_status,
        test_14_override_reason_persisted_in_release,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n✗ {test.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
        finally:
            cleanup()

    print("\n" + "=" * 70)
    print(f"TEST SUMMARY: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed > 0:
        sys.exit(1)
    else:
        print("\n✓ All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
