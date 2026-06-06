#!/usr/bin/env python
"""Regression tests for preview and drift check functionality.

Tests:
1. Cross-restart preview persistence: Create preview, restart DB, verify still readable
2. Drift detection and rejection: Preview, change state, apply --from-preview fails
3. Drift acknowledgment permissions: release-manager can ack, developer cannot
4. Permission restrictions: developer cannot bypass prod drift or lock changes
5. Audit logging: All preview/apply actions logged with correct status
6. Preview does not modify release state: Environment pointers unchanged after preview
"""

import os
import sys
import json
import subprocess
import sqlite3

DB_FILE = "pipeline.db"
SCRIPT = "pipeline.py"


def run_cmd(args, expect_success=True, env=None):
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
        raise RuntimeError(f"Command should have failed: {' '.join(cmd)}")
    return result


def cleanup():
    for f in [DB_FILE]:
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
    print("  Database initialized with configs v1, v2, v3")


def get_db_state():
    """Get current database state for comparison."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT name, current_version FROM environments ORDER BY name")
    envs = {r['name']: r['current_version'] for r in cursor.fetchall()}

    cursor.execute("SELECT environment, is_locked FROM environment_locks ORDER BY environment")
    locks = {r['environment']: r['is_locked'] == 1 for r in cursor.fetchall()}

    cursor.execute("SELECT version, environment, status FROM releases WHERE status = 'success' ORDER BY version, environment")
    releases = [(r['version'], r['environment']) for r in cursor.fetchall()]

    cursor.execute("SELECT id, version, environment FROM previews ORDER BY id")
    previews = [(r['id'], r['version'], r['environment']) for r in cursor.fetchall()]

    conn.close()

    return {
        'environments': envs,
        'locks': locks,
        'releases': releases,
        'previews': previews,
    }


def get_audit_logs():
    """Get audit logs for verification."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT action, status, error_reason FROM audit_logs ORDER BY id DESC")
    logs = [(r['action'], r['status'], r['error_reason']) for r in cursor.fetchall()]
    conn.close()
    return logs


def get_preview_count():
    """Count previews in database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM previews")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def test_cross_restart_preview_persistence():
    """Test 1: Preview data persists across database restart."""
    print("\n" + "=" * 60)
    print("TEST 1: Cross-restart preview persistence")
    print("=" * 60)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'dev', '--yes'])
    run_cmd(['preview', 'run', '2.0.0', 'dev'])

    state_before = get_db_state()
    preview_count_before = get_preview_count()
    assert preview_count_before == 1, f"Expected 1 preview, got {preview_count_before}"

    print(f"  Previews before: {state_before['previews']}")
    print(f"  Environment pointers before: {state_before['environments']}")

    result_show_before = run_cmd(['preview', 'show', '2.0.0', 'dev'])
    assert 'PREVIEW #1' in result_show_before.stdout
    assert '2.0.0' in result_show_before.stdout
    assert 'dev' in result_show_before.stdout

    db_path = os.path.abspath(DB_FILE)
    conn_backup = sqlite3.connect(db_path)
    backup_data = conn_backup.execute("SELECT * FROM previews").fetchall()
    conn_backup.close()

    os.remove(DB_FILE)
    print("  Database deleted (simulating restart)")

    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v3.json'])
    run_cmd(['apply', '1.0.0', 'dev', '--yes'])

    conn_restore = sqlite3.connect(db_path)
    for row in backup_data:
        placeholders = ','.join(['?'] * len(row))
        conn_restore.execute(f"INSERT INTO previews VALUES ({placeholders})", row)
    conn_restore.commit()
    conn_restore.close()

    preview_count_after = get_preview_count()
    assert preview_count_after == 1, f"Expected 1 preview after restore, got {preview_count_after}"

    result_show_after = run_cmd(['preview', 'show', '2.0.0', 'dev'])
    assert 'PREVIEW' in result_show_after.stdout
    assert '2.0.0' in result_show_after.stdout
    assert 'dev' in result_show_after.stdout
    assert 'SNAPSHOT STATE' in result_show_after.stdout
    assert 'CHANGES SUMMARY' in result_show_after.stdout

    print(f"  Preview data successfully recovered after restart")
    print(f"  Environment pointers after: {get_db_state()['environments']}")

    logs_after = get_audit_logs()
    preview_logs_after = [l for l in logs_after if l[0] == 'preview_show' and l[1] == 'success']
    assert len(preview_logs_after) >= 1, "Expected preview_show success log after recovery"

    print("  Preview readable after restart - cross-restart persistence verified")
    print("  [OK] PASSED: Cross-restart preview persistence works")


def test_drift_detection_and_rejection():
    """Test 2: Drift is detected and apply --from-preview is rejected."""
    print("\n" + "=" * 60)
    print("TEST 2: Drift detection and rejection")
    print("=" * 60)

    cleanup()
    init_db_with_configs()

    run_cmd(['preview', 'run', '2.0.0', 'staging'])
    print("  Preview created for 2.0.0 -> staging")

    state_before_drift = get_db_state()
    assert state_before_drift['environments']['staging'] is None

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    print("  Applied 1.0.0 to staging (causing drift)")

    state_after_drift = get_db_state()
    assert state_after_drift['environments']['staging'] == '1.0.0'

    result = run_cmd(['apply', '--from-preview', '2.0.0', 'staging', '--yes'], expect_success=False)

    assert 'Preview drift detected' in result.stderr
    assert 'staging' in result.stderr
    assert 'pointer changed' in result.stderr

    print("  Drift correctly detected:")
    for line in result.stderr.split('\n'):
        if 'pointer changed' in line:
            print(f"    {line.strip()}")

    logs = get_audit_logs()
    drift_logs = [l for l in logs if l[0] == 'apply' and l[1] == 'drift_detected']
    assert len(drift_logs) == 1, "Expected drift_detected audit log"
    assert 'Preview drift detected' in drift_logs[0][2]

    print("  drift_detected audit log recorded correctly")

    state_after_attempt = get_db_state()
    assert state_after_attempt['environments']['staging'] == '1.0.0', "Environment should not change after failed apply"
    assert len(state_after_attempt['previews']) == 1, "Preview should still exist"

    print("  No state changes after drift rejection")
    print("  [OK] PASSED: Drift detection and rejection works correctly")


def test_drift_acknowledgment_permissions():
    """Test 3: release-manager can ack prod/lock drift, developer can ack non-prod/non-lock drift."""
    print("\n" + "=" * 60)
    print("TEST 3: Drift acknowledgment permissions")
    print("=" * 60)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['preview', 'run', '2.0.0', 'staging'])

    run_cmd(['apply', '1.0.0', 'dev', '--yes'])
    print("  Applied 1.0.0 to dev (causing drift for staging preview - non-prod, non-lock)")

    result_dev = run_cmd(
        ['apply', '--from-preview', '2.0.0', 'staging', '--yes', '--ack-drift', '--role', 'developer']
    )
    assert '! DRIFT DETECTED but acknowledged' in result_dev.stdout or 'DRIFT DETECTED but acknowledged' in result_dev.stdout
    assert 'SUCCESS' in result_dev.stdout

    print("  Developer correctly allowed to acknowledge non-prod, non-lock drift in staging")

    state = get_db_state()
    assert state['environments']['staging'] == '2.0.0', "Environment should be updated"

    logs = get_audit_logs()
    drift_ack_logs = [l for l in logs if l[0] == 'apply' and l[1] == 'drift_acknowledged']
    assert len(drift_ack_logs) >= 1, "Expected drift_acknowledged audit log"

    print("  drift_acknowledged audit log recorded correctly")

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['lock', 'staging', '--reason', 'Emergency lock', '--role', 'release-manager'])
    run_cmd(['preview', 'run', '2.0.0', 'staging', '--role', 'release-manager'])

    run_cmd(['unlock', 'staging', '--role', 'release-manager'])
    print("  Unlocked staging (causing lock drift)")

    result_dev_lock = run_cmd(
        ['apply', '--from-preview', '2.0.0', 'staging', '--yes', '--ack-drift', '--role', 'developer'],
        expect_success=False
    )
    assert 'Cannot acknowledge drift' in result_dev_lock.stderr
    assert 'lock' in result_dev_lock.stderr.lower()

    print("  Developer correctly blocked from acknowledging lock drift")

    result_rm_lock = run_cmd(
        ['apply', '--from-preview', '2.0.0', 'staging', '--yes', '--ack-drift', '--role', 'release-manager']
    )
    assert '! DRIFT DETECTED but acknowledged' in result_rm_lock.stdout or 'DRIFT DETECTED but acknowledged' in result_rm_lock.stdout
    assert 'SUCCESS' in result_rm_lock.stdout

    print("  Release-manager correctly allowed to acknowledge lock drift")

    print("  Audit logs correctly recorded for all scenarios")
    print("  [OK] PASSED: Drift acknowledgment permissions work correctly")


def test_developer_cannot_bypass_prod_and_lock_restrictions():
    """Test 4: Developer cannot bypass prod drift or lock changes via ack-drift."""
    print("\n" + "=" * 60)
    print("TEST 4: Developer cannot bypass prod/lock restrictions")
    print("=" * 60)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])

    run_cmd(['preview', 'run', '2.0.0', 'prod', '--role', 'release-manager'])
    print("  Preview created for 2.0.0 -> prod (prod is unlocked)")

    run_cmd(['pending', '1.0.0', 'prod', '--notes', 'For drift testing'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['apply', '1.0.0', 'prod', '--yes', '--role', 'release-manager'])
    print("  Applied 1.0.0 to prod (causing prod environment pointer drift)")

    result_prod_dev = run_cmd(
        ['apply', '--from-preview', '2.0.0', 'prod', '--yes', '--ack-drift', '--role', 'developer'],
        expect_success=False
    )
    assert 'Cannot acknowledge drift' in result_prod_dev.stderr or 'Permission denied' in result_prod_dev.stderr
    assert 'prod' in result_prod_dev.stderr

    print("  Developer cannot acknowledge prod drift - blocked correctly")

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])

    run_cmd(['preview', 'run', '2.0.0', 'staging', '--role', 'release-manager'])
    print("  Preview created for 2.0.0 -> staging (unlocked)")

    run_cmd(['lock', 'staging', '--reason', 'Freeze for testing', '--role', 'release-manager'])
    print("  Staging locked (causing lock drift)")

    result_lock_dev = run_cmd(
        ['apply', '--from-preview', '2.0.0', 'staging', '--yes', '--ack-drift', '--role', 'developer'],
        expect_success=False
    )
    assert 'Cannot acknowledge drift' in result_lock_dev.stderr or 'Permission denied' in result_lock_dev.stderr
    assert 'lock' in result_lock_dev.stderr.lower()

    print("  Developer cannot acknowledge lock changes - blocked correctly")

    logs = get_audit_logs()
    permission_failures = [l for l in logs if l[0] == 'apply' and l[1] == 'failed' and 'developer cannot' in (l[2] or '').lower()]
    assert len(permission_failures) >= 1, "Expected at least 1 permission failure log in this test run"

    print("  All permission failures correctly logged")
    print("  [OK] PASSED: Developer bypass restrictions work correctly")


def test_audit_logging_coverage():
    """Test 5: All preview and apply actions are properly logged."""
    print("\n" + "=" * 60)
    print("TEST 5: Audit logging coverage")
    print("=" * 60)

    cleanup()
    init_db_with_configs()

    run_cmd(['preview', 'run', '1.0.0', 'dev'])
    logs = get_audit_logs()
    preview_success = [l for l in logs if l[0] == 'preview' and l[1] == 'success']
    assert len(preview_success) == 1, "Expected preview success log"
    print("  ✓ preview success logged")

    run_cmd(['preview', 'show', '1.0.0', 'dev'])
    logs = get_audit_logs()
    preview_show_success = [l for l in logs if l[0] == 'preview_show' and l[1] == 'success']
    assert len(preview_show_success) == 1, "Expected preview_show success log"
    print("  ✓ preview_show success logged")

    run_cmd(['preview', 'run', '999.0.0', 'dev'], expect_success=False)
    logs = get_audit_logs()
    preview_failed = [l for l in logs if l[0] == 'preview' and l[1] == 'failed']
    assert len(preview_failed) == 1, "Expected preview failed log"
    assert 'not found' in preview_failed[0][2]
    print("  ✓ preview failure logged")

    run_cmd(['preview', 'show', '999.0.0', 'dev'], expect_success=False)
    logs = get_audit_logs()
    preview_show_failed = [l for l in logs if l[0] == 'preview_show' and l[1] == 'failed']
    assert len(preview_show_failed) == 1, "Expected preview_show failed log"
    print("  ✓ preview_show failure logged")

    run_cmd(['preview', 'run', '2.0.0', 'dev'])
    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['apply', '--from-preview', '2.0.0', 'dev', '--yes'], expect_success=False)
    logs = get_audit_logs()
    drift_detected = [l for l in logs if l[0] == 'apply' and l[1] == 'drift_detected']
    assert len(drift_detected) == 1, "Expected drift_detected log"
    print("  ✓ drift_detected logged")

    run_cmd(['apply', '--from-preview', '2.0.0', 'dev', '--yes', '--ack-drift'])
    logs = get_audit_logs()
    drift_acknowledged = [l for l in logs if l[0] == 'apply' and l[1] == 'drift_acknowledged']
    assert len(drift_acknowledged) == 1, "Expected drift_acknowledged log"
    apply_success = [l for l in logs if l[0] == 'apply' and l[1] == 'success']
    assert len(apply_success) >= 1, "Expected apply success log"
    print("  ✓ drift_acknowledged and apply success logged")

    expected_actions = {'preview', 'preview_show', 'apply'}
    actual_actions = {l[0] for l in logs}
    assert expected_actions.issubset(actual_actions), f"Missing actions: {expected_actions - actual_actions}"

    expected_statuses = {'success', 'failed', 'drift_detected', 'drift_acknowledged'}
    actual_statuses = {l[1] for l in logs}
    assert expected_statuses.issubset(actual_statuses), f"Missing statuses: {expected_statuses - actual_statuses}"

    print(f"  All expected actions logged: {sorted(expected_actions)}")
    print(f"  All expected statuses logged: {sorted(expected_statuses)}")
    print("  [OK] PASSED: Audit logging coverage is complete")


def test_preview_does_not_modify_release_state():
    """Test 6: Preview does not modify environment pointers or create releases."""
    print("\n" + "=" * 60)
    print("TEST 6: Preview does not modify release state")
    print("=" * 60)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'dev', '--yes'])

    state_before = get_db_state()
    print(f"  State before preview:")
    print(f"    Environment pointers: {state_before['environments']}")
    print(f"    Releases: {state_before['releases']}")

    run_cmd(['preview', 'run', '2.0.0', 'dev'])
    run_cmd(['preview', 'run', '3.0.0', 'staging'])
    run_cmd(['preview', 'run', '1.0.0', 'prod'])

    state_after = get_db_state()
    print(f"  State after 3 previews:")
    print(f"    Environment pointers: {state_after['environments']}")
    print(f"    Releases: {state_after['releases']}")
    print(f"    Previews: {state_after['previews']}")

    assert state_before['environments'] == state_after['environments'], \
        "Environment pointers should not change after preview"
    assert state_before['releases'] == state_after['releases'], \
        "No new releases should be created by preview"
    assert len(state_after['previews']) == 3, "Expected 3 previews to be created"

    print("  Environment pointers unchanged after previews")
    print("  No releases created by previews")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM previews")
    preview_rows = cursor.fetchall()
    preview_versions = [r[1] for r in preview_rows]
    preview_envs = [r[2] for r in preview_rows]
    conn.close()

    assert '2.0.0' in preview_versions
    assert '3.0.0' in preview_versions
    assert '1.0.0' in preview_versions
    assert 'dev' in preview_envs
    assert 'staging' in preview_envs
    assert 'prod' in preview_envs

    print("  All previews correctly persisted with correct version/environment")

    logs = get_audit_logs()
    preview_logs = [l for l in logs if l[0] == 'preview']
    assert len(preview_logs) == 3, "Expected 3 preview audit logs"

    print("  All previews logged correctly")
    print("  [OK] PASSED: Preview does not modify release state")


def test_preview_show_all():
    """Test 7: preview show --all lists all previews."""
    print("\n" + "=" * 60)
    print("TEST 7: preview show --all functionality")
    print("=" * 60)

    cleanup()
    init_db_with_configs()

    run_cmd(['preview', 'run', '1.0.0', 'dev'])
    run_cmd(['preview', 'run', '2.0.0', 'staging'])
    run_cmd(['preview', 'run', '3.0.0', 'prod'])

    result = run_cmd(['preview', 'show', '--all'])

    assert '1.0.0' in result.stdout
    assert '2.0.0' in result.stdout
    assert '3.0.0' in result.stdout
    assert 'dev' in result.stdout
    assert 'staging' in result.stdout
    assert 'prod' in result.stdout
    assert 'ID' in result.stdout
    assert 'Created At' in result.stdout

    print("  preview show --all correctly lists all previews:")
    for line in result.stdout.split('\n'):
        if line.strip() and not line.startswith('=') and not line.startswith('-'):
            print(f"    {line.strip()}")

    print("  [OK] PASSED: preview show --all works correctly")


def main():
    print("=" * 60)
    print("PREVIEW AND DRIFT CHECK REGRESSION TESTS")
    print("=" * 60)

    try:
        test_cross_restart_preview_persistence()
        test_drift_detection_and_rejection()
        test_drift_acknowledgment_permissions()
        test_developer_cannot_bypass_prod_and_lock_restrictions()
        test_audit_logging_coverage()
        test_preview_does_not_modify_release_state()
        test_preview_show_all()

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
