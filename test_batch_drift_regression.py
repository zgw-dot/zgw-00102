#!/usr/bin/env python
"""Regression tests for batch apply drift detection and prevention.

Tests:
1. Preview drift bypass attempt: Preview, modify config in SQLite, batch apply should fail
2. Batch apply success scenario: Normal batch apply without drift
3. Prod permission enforcement: Developer cannot batch apply to prod
4. apply --from-preview drift detection: Existing drift check still works
5. Multi-step batch with drift: First step fails (drift), subsequent steps skipped
6. Lock status drift: Preview, lock environment, batch apply should detect drift
7. Environment pointer drift: Preview, change pointer, batch apply should detect drift
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


def get_db_state():
    """Get current database state for comparison."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT name, current_version FROM environments ORDER BY name")
    envs = {r['name']: r['current_version'] for r in cursor.fetchall()}

    cursor.execute("SELECT environment, is_locked FROM environment_locks ORDER BY environment")
    locks = {r['environment']: r['is_locked'] == 1 for r in cursor.fetchall()}

    cursor.execute("SELECT version, environment, status FROM releases ORDER BY id DESC")
    releases = [(r['version'], r['environment'], r['status']) for r in cursor.fetchall()]

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
    cursor.execute("SELECT action, status, error_reason, details FROM audit_logs ORDER BY id DESC")
    logs = []
    for r in cursor.fetchall():
        details = None
        if r['details']:
            try:
                details = json.loads(r['details'])
            except (json.JSONDecodeError, TypeError):
                details = r['details']
        logs.append((r['action'], r['status'], r['error_reason'], details))
    conn.close()
    return logs


def get_error_logs():
    """Get error logs for verification."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT command, error_code, error_message FROM error_logs ORDER BY id DESC")
    logs = [(r['command'], r['error_code'], r['error_message']) for r in cursor.fetchall()]
    conn.close()
    return logs


def _modify_config_in_db(version, key_path, new_value):
    """Directly modify config in SQLite to cause config drift."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT config_json FROM configs WHERE version = ?", (version,))
    row = cursor.fetchone()
    config = json.loads(row[0])
    keys = key_path.split('.')
    d = config
    for k in keys[:-1]:
        d = d[k]
    d[keys[-1]] = new_value
    cursor.execute(
        "UPDATE configs SET config_json = ? WHERE version = ?",
        (json.dumps(config), version)
    )
    conn.commit()
    conn.close()


def test_batch_drift_config_modification():
    """Test 1: Preview, modify config in SQLite, batch apply should detect drift and fail.
    
    Asserts:
    - Batch step status is 'failed'
    - Dev pointer remains at original version
    - Release table has no success entry for the drifted version
    - Audit log has 'drift_detected' entry
    - Error log has drift error
    - CLI output contains drift message
    """
    print("\n" + "=" * 70)
    print("TEST 1: Batch apply detects config drift after preview")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'dev', '--yes'])
    state_before = get_db_state()
    assert state_before['environments']['dev'] == '1.0.0'
    print("  Baseline: dev at 1.0.0")

    run_cmd(['preview', 'run', '2.0.0', 'dev'])
    state_after_preview = get_db_state()
    assert len(state_after_preview['previews']) == 1
    print("  Preview created for 2.0.0 -> dev")

    _modify_config_in_db('2.0.0', 'database.pool_size', 999)
    _modify_config_in_db('2.0.0', 'app_name', 'hacked_app')
    print("  Config 2.0.0 modified directly in SQLite (drift created)")

    result = run_cmd(['batch', 'apply', 'dev:2.0.0', '--yes'], expect_success=False)

    print("  Checking CLI output for drift message...")
    assert 'Preview drift detected' in result.stderr or 'Preview drift detected' in result.stdout, \
        f"CLI output should mention drift. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    assert 'content changed' in result.stderr or 'content changed' in result.stdout, \
        f"CLI output should mention content change. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    assert 'FAILED' in result.stdout or '[FAIL]' in result.stdout, \
        f"CLI output should show step failed. STDOUT: {result.stdout}"
    print("  ✓ CLI output correctly shows drift detected and step failed")

    state_after = get_db_state()
    assert state_after['environments']['dev'] == '1.0.0', \
        f"Dev pointer should remain at 1.0.0, got {state_after['environments']['dev']}"
    print(f"  ✓ Dev pointer unchanged: {state_after['environments']['dev']}")

    dev_releases = [(v, e, s) for v, e, s in state_after['releases'] if e == 'dev' and v == '2.0.0']
    success_releases = [r for r in dev_releases if r[2] == 'success']
    assert len(success_releases) == 0, \
        f"Release table should not have success entry for 2.0.0->dev, got: {dev_releases}"
    print(f"  ✓ No success release for 2.0.0->dev. Releases: {dev_releases}")

    audit_logs = get_audit_logs()
    drift_logs = [l for l in audit_logs if l[0] == 'batch_apply' and l[1] == 'drift_detected']
    assert len(drift_logs) >= 1, f"Expected drift_detected audit log, got: {audit_logs[:5]}"
    assert 'Preview drift detected' in (drift_logs[0][2] or ''), \
        f"Drift log should have error reason, got: {drift_logs[0]}"
    print("  ✓ Audit log has 'drift_detected' entry")

    error_logs = get_error_logs()
    drift_errors = [e for e in error_logs if e[0] == 'batch_apply' and 'DRIFT' in e[1]]
    assert len(drift_errors) >= 1, f"Expected drift error log, got: {error_logs[:5]}"
    print("  ✓ Error log has drift error entry")

    assert len(state_after['previews']) == 1, "Preview should still exist after failed batch apply"
    print("  ✓ Preview record preserved")

    print("  [OK] PASSED: Batch apply correctly blocks config drift")


def test_batch_apply_success():
    """Test 2: Normal batch apply without drift succeeds.
    
    Asserts:
    - Batch steps status is 'success'
    - Environment pointers are updated correctly
    - Release table has success entries
    - Audit log has 'success' entries
    - Preview is deleted after successful apply
    """
    print("\n" + "=" * 70)
    print("TEST 2: Batch apply succeeds without drift")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['preview', 'run', '1.0.0', 'dev'])
    run_cmd(['preview', 'run', '1.0.0', 'staging'])
    print("  Previews created for 1.0.0 -> dev, 1.0.0 -> staging")

    result = run_cmd(['batch', 'apply', 'dev:1.0.0', 'staging:1.0.0', '--yes'])

    assert 'SUCCESS' in result.stdout, f"CLI should show success. STDOUT: {result.stdout}"
    assert 'BATCH COMPLETED SUCCESSFULLY' in result.stdout, \
        f"CLI should show batch completed successfully. STDOUT: {result.stdout}"
    print("  ✓ CLI output shows batch completed successfully")

    state = get_db_state()
    assert state['environments']['dev'] == '1.0.0'
    assert state['environments']['staging'] == '1.0.0'
    print(f"  ✓ Environment pointers updated: dev={state['environments']['dev']}, staging={state['environments']['staging']}")

    releases = state['releases']
    dev_success = any(v == '1.0.0' and e == 'dev' and s == 'success' for v, e, s in releases)
    staging_success = any(v == '1.0.0' and e == 'staging' and s == 'success' for v, e, s in releases)
    assert dev_success and staging_success, f"Should have success releases. Releases: {releases[:5]}"
    print("  ✓ Release table has success entries")

    assert len(state['previews']) == 0, "Previews should be deleted after successful apply"
    print("  ✓ Previews deleted after successful apply")

    audit_logs = get_audit_logs()
    success_logs = [l for l in audit_logs if l[0] == 'batch_apply' and l[1] == 'success']
    assert len(success_logs) >= 2, f"Expected 2 batch_apply success logs, got {len(success_logs)}"
    batch_success_log = [l for l in audit_logs if l[0] == 'batch_apply' and l[1] == 'batch_success']
    assert len(batch_success_log) == 1, "Expected batch_success log"
    print("  ✓ Audit logs correctly recorded")

    print("  [OK] PASSED: Batch apply succeeds correctly")


def test_batch_prod_permission():
    """Test 3: Developer cannot batch apply to prod environment.
    
    Asserts:
    - Batch apply to prod fails for developer role
    - Dev pointer not updated
    - Audit log has 'failed' entry with permission reason
    - CLI output shows permission error
    """
    print("\n" + "=" * 70)
    print("TEST 3: Batch apply prod permission enforcement")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '2.0.0', 'staging', '--yes'])
    run_cmd(['pending', '2.0.0', 'prod', '--notes', 'Test prod release'])
    run_cmd(['approve', '2.0.0', 'prod', '--role', 'release-manager'])
    print("  Setup: 2.0.0 approved for prod")

    result = run_cmd(
        ['batch', 'apply', 'prod:2.0.0', '--yes', '--role', 'developer'],
        expect_success=False
    )

    assert 'Permission denied' in result.stderr or 'Permission denied' in result.stdout, \
        f"CLI should show permission denied. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    print("  ✓ CLI output shows permission denied for developer")

    state = get_db_state()
    assert state['environments']['prod'] is None, \
        f"Prod pointer should remain None, got {state['environments']['prod']}"
    print("  ✓ Prod pointer unchanged")

    audit_logs = get_audit_logs()
    permission_fail = [l for l in audit_logs if l[0] == 'batch_apply' and l[1] == 'failed' and 'Permission' in (l[2] or '')]
    assert len(permission_fail) >= 1, f"Expected permission failure audit log. Logs: {audit_logs[:5]}"
    print("  ✓ Audit log has permission failure entry")

    result_rm = run_cmd(
        ['batch', 'apply', 'prod:2.0.0', '--yes', '--role', 'release-manager']
    )
    assert 'SUCCESS' in result_rm.stdout, "Release-manager should be able to apply to prod"
    state_after = get_db_state()
    assert state_after['environments']['prod'] == '2.0.0', "Prod pointer should be updated by release-manager"
    print("  ✓ Release-manager can successfully apply to prod")

    print("  [OK] PASSED: Prod permission correctly enforced")


def test_apply_from_preview_drift():
    """Test 4: Existing apply --from-preview drift detection still works.
    
    Asserts:
    - apply --from-preview detects drift and fails
    - Environment pointer unchanged
    - Audit log has 'drift_detected' entry
    - CLI output shows drift message
    """
    print("\n" + "=" * 70)
    print("TEST 4: apply --from-preview drift detection still works")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'dev', '--yes'])
    run_cmd(['preview', 'run', '2.0.0', 'dev'])
    print("  Preview created for 2.0.0 -> dev")

    _modify_config_in_db('2.0.0', 'database.pool_size', 888)
    print("  Config modified to create drift")

    result = run_cmd(['apply', '--from-preview', '2.0.0', 'dev', '--yes'], expect_success=False)

    assert 'Preview drift detected' in result.stderr, \
        f"CLI should show drift detected. STDERR: {result.stderr}"
    assert 'content changed' in result.stderr, \
        f"CLI should show content changed. STDERR: {result.stderr}"
    print("  ✓ CLI output correctly shows drift")

    state = get_db_state()
    assert state['environments']['dev'] == '1.0.0', \
        f"Dev pointer should remain at 1.0.0, got {state['environments']['dev']}"
    print("  ✓ Dev pointer unchanged")

    audit_logs = get_audit_logs()
    drift_logs = [l for l in audit_logs if l[0] == 'apply' and l[1] == 'drift_detected']
    assert len(drift_logs) == 1, "Expected drift_detected audit log"
    print("  ✓ Audit log has drift_detected entry")

    print("  [OK] PASSED: apply --from-preview drift detection works")


def test_multi_step_batch_drift_skip():
    """Test 5: Multi-step batch - first step fails (drift), subsequent steps skipped.
    
    Asserts:
    - Step 1 (drifted) status is 'failed'
    - Step 2 status is 'skipped'
    - Step 1 environment pointer unchanged
    - Step 2 environment pointer unchanged
    - Audit log has 'failed' for step 1 and 'skipped' for step 2
    - CLI output shows both failed and skipped steps
    """
    print("\n" + "=" * 70)
    print("TEST 5: Multi-step batch - drift causes subsequent steps to be skipped")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['preview', 'run', '1.0.0', 'dev'])
    run_cmd(['preview', 'run', '2.0.0', 'staging'])
    print("  Previews created for dev:1.0.0 and staging:2.0.0")

    _modify_config_in_db('1.0.0', 'database.pool_size', 111)
    print("  Config 1.0.0 modified (drift for step 1)")

    result = run_cmd(['batch', 'apply', 'dev:1.0.0', 'staging:2.0.0', '--yes'], expect_success=False)

    assert 'FAILED' in result.stdout or '[FAIL]' in result.stdout, \
        f"CLI should show step 1 failed. STDOUT: {result.stdout}"
    assert 'SKIP' in result.stdout or 'SKIPPED' in result.stdout or '[SKIP]' in result.stdout, \
        f"CLI should show step 2 skipped. STDOUT: {result.stdout}"
    assert 'Previous step failed' in result.stdout, \
        f"CLI should show skip reason. STDOUT: {result.stdout}"
    print("  ✓ CLI output shows step 1 failed and step 2 skipped")

    state = get_db_state()
    assert state['environments']['dev'] is None, "Dev pointer should remain None"
    assert state['environments']['staging'] is None, "Staging pointer should remain None"
    print(f"  ✓ Environment pointers unchanged: dev={state['environments']['dev']}, staging={state['environments']['staging']}")

    audit_logs = get_audit_logs()
    drift_logs = [l for l in audit_logs if l[0] == 'batch_apply' and l[1] == 'drift_detected' and l[3] and l[3].get('step') == 'dev:1.0.0']
    skipped_logs = [l for l in audit_logs if l[0] == 'batch_apply' and l[1] == 'skipped' and l[3] and l[3].get('step') == 'staging:2.0.0']
    assert len(drift_logs) >= 1, f"Expected drift_detected log for step 1. Logs: {[(l[0], l[1], l[3].get('step') if l[3] else None) for l in audit_logs[:10]]}"
    assert len(skipped_logs) >= 1, "Expected skipped log for step 2"
    print("  ✓ Audit logs correctly show failed and skipped steps")

    batch_failed_log = [l for l in audit_logs if l[0] == 'batch_apply' and l[1] == 'batch_failed']
    assert len(batch_failed_log) == 1, "Expected batch_failed log"
    print("  ✓ Batch failure logged correctly")

    print("  [OK] PASSED: Multi-step batch correctly skips subsequent steps after drift")


def test_batch_lock_status_drift():
    """Test 6: Batch apply detects lock status drift.
    
    Asserts:
    - Batch apply detects lock drift and fails
    - Environment pointer unchanged
    - Audit log has 'drift_detected' entry
    - CLI output mentions lock status change
    """
    print("\n" + "=" * 70)
    print("TEST 6: Batch apply detects lock status drift")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['preview', 'run', '2.0.0', 'dev'])
    print("  Preview created for 2.0.0 -> dev (unlocked)")

    run_cmd(['lock', 'dev', '--reason', 'Emergency lock', '--role', 'release-manager'])
    print("  Dev environment locked after preview (drift created)")

    result = run_cmd(['batch', 'apply', 'dev:2.0.0', '--yes'], expect_success=False)

    assert 'Preview drift detected' in result.stderr or 'Preview drift detected' in result.stdout, \
        f"CLI should show drift. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    assert 'locked' in result.stderr.lower() or 'locked' in result.stdout.lower(), \
        f"CLI should mention lock status change. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    print("  ✓ CLI output shows lock drift detected")

    state = get_db_state()
    assert state['environments']['dev'] is None, "Dev pointer should remain None"
    print("  ✓ Dev pointer unchanged")

    audit_logs = get_audit_logs()
    drift_logs = [l for l in audit_logs if l[0] == 'batch_apply' and l[1] == 'drift_detected']
    assert len(drift_logs) >= 1, "Expected drift_detected audit log"
    print("  ✓ Audit log has drift_detected entry")

    print("  [OK] PASSED: Lock status drift correctly detected")


def test_batch_pointer_drift():
    """Test 7: Batch apply detects environment pointer drift.
    
    Asserts:
    - Batch apply detects pointer drift and fails
    - Environment pointer not overwritten with drifted version
    - Audit log has 'drift_detected' entry
    - CLI output mentions pointer change
    """
    print("\n" + "=" * 70)
    print("TEST 7: Batch apply detects environment pointer drift")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['preview', 'run', '2.0.0', 'dev'])
    print("  Preview created for 2.0.0 -> dev (current: None)")

    run_cmd(['apply', '1.0.0', 'dev', '--yes'])
    print("  Applied 1.0.0 to dev after preview (pointer drift created)")

    result = run_cmd(['batch', 'apply', 'dev:2.0.0', '--yes'], expect_success=False)

    assert 'Preview drift detected' in result.stderr or 'Preview drift detected' in result.stdout, \
        f"CLI should show drift. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    assert 'pointer changed' in result.stderr or 'pointer changed' in result.stdout, \
        f"CLI should mention pointer change. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    print("  ✓ CLI output shows pointer drift detected")

    state = get_db_state()
    assert state['environments']['dev'] == '1.0.0', \
        f"Dev pointer should remain at 1.0.0, got {state['environments']['dev']}"
    print(f"  ✓ Dev pointer preserved at 1.0.0")

    releases_200 = [r for r in state['releases'] if r[0] == '2.0.0' and r[1] == 'dev']
    assert len(releases_200) == 0 or all(r[2] != 'success' for r in releases_200), \
        f"Should not have successful release of 2.0.0 to dev. Releases: {releases_200}"
    print("  ✓ No tampered content written to release table")

    audit_logs = get_audit_logs()
    drift_logs = [l for l in audit_logs if l[0] == 'batch_apply' and l[1] == 'drift_detected']
    assert len(drift_logs) >= 1, "Expected drift_detected audit log"
    print("  ✓ Audit log has drift_detected entry")

    print("  [OK] PASSED: Environment pointer drift correctly detected")


def main():
    print("=" * 70)
    print("BATCH DRIFT PREVENTION REGRESSION TESTS")
    print("=" * 70)

    try:
        test_batch_drift_config_modification()
        test_batch_apply_success()
        test_batch_prod_permission()
        test_apply_from_preview_drift()
        test_multi_step_batch_drift_skip()
        test_batch_lock_status_drift()
        test_batch_pointer_drift()

        print("\n" + "=" * 70)
        print("ALL TESTS PASSED!")
        print("=" * 70)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
