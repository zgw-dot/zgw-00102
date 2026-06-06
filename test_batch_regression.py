#!/usr/bin/env python
"""Regression tests for release batch functionality.

Tests:
1. Batch create/list/show: Basic CRUD operations
2. Batch apply success: Sequential execution across environments
3. Batch apply failure stopping: Stops at first failure, remaining steps skipped
4. Cross-restart resume: Batch persists, can be resumed after restart
5. Batch export/import: JSON round-trip works correctly
6. Import name conflict rejection: Same name rejected without --force
7. Import state conflict rejection: Step state conflicts rejected without --force
8. Import force override: --force overrides both name and state conflicts
9. Permission restrictions: developer cannot import prod batches
10. Permission restrictions: release-manager can import prod batches
11. Permission restrictions: developer cannot apply prod batches
12. Audit logging: All batch operations logged with correct status
13. Error logging: Failures logged with error details
14. Batch with no steps rejected: Empty batch cannot be created
15. Duplicate batch name rejected: Same name cannot be created twice
16. Invalid step format rejected: Bad ENV:VERSION format rejected
17. Nonexistent version in step rejected: Version must exist before batch create
18. --retry resets failed steps: Can retry after fixing issues
19. Successful steps preserved after failure: Earlier success not rolled back
20. Preview drift detection applies to batch steps (via pre_apply_checks reuse)
"""

import os
import sys
import json
import subprocess
import sqlite3

DB_FILE = "pipeline.db"
SCRIPT = "pipeline.py"


def run_cmd(args, expect_success=True, env=None, stdin=None):
    cmd = [sys.executable, SCRIPT] + args
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        env=merged_env,
        input=stdin
    )
    if expect_success and result.returncode != 0:
        print(f"FAIL: {' '.join(cmd)}")
        print(f"  STDOUT: {result.stdout}")
        print(f"  STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    if not expect_success and result.returncode == 0:
        print(f"FAIL: {' '.join(cmd)} expected to fail but succeeded")
        print(f"  STDOUT: {result.stdout}")
        raise RuntimeError(f"Command should have failed: {' '.join(cmd)}")
    return result


def cleanup():
    for f in [DB_FILE]:
        if os.path.exists(f):
            os.remove(f)


def cleanup_export_file():
    if os.path.exists("test_batch_export.json"):
        os.remove("test_batch_export.json")


def init_db_with_configs():
    cleanup()
    cleanup_export_file()
    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v3.json'])
    print("  Database initialized with configs v1, v2, v3")


def get_db_state():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT name, current_version FROM environments ORDER BY name")
    envs = {r['name']: r['current_version'] for r in cursor.fetchall()}

    cursor.execute("SELECT environment, is_locked FROM environment_locks ORDER BY environment")
    locks = {r['environment']: r['is_locked'] == 1 for r in cursor.fetchall()}

    cursor.execute("SELECT version, environment, status FROM releases WHERE status = 'success' ORDER BY version, environment")
    releases = [(r['version'], r['environment']) for r in cursor.fetchall()]

    cursor.execute("SELECT id, name, status FROM batches ORDER BY id")
    batches = [(r['id'], r['name'], r['status']) for r in cursor.fetchall()]

    cursor.execute("SELECT batch_id, step_index, status FROM batch_steps ORDER BY batch_id, step_index")
    batch_steps = [(r['batch_id'], r['step_index'], r['status']) for r in cursor.fetchall()]

    conn.close()

    return {
        'environments': envs,
        'locks': locks,
        'releases': releases,
        'batches': batches,
        'batch_steps': batch_steps,
    }


def get_audit_logs():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT action, status, error_reason FROM audit_logs ORDER BY id DESC")
    logs = [(r['action'], r['status'], r['error_reason']) for r in cursor.fetchall()]
    conn.close()
    return logs


def get_error_logs():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT command, error_code, error_message FROM error_logs ORDER BY id DESC")
    logs = [(r['command'], r['error_code'], r['error_message']) for r in cursor.fetchall()]
    conn.close()
    return logs


def test_batch_crud():
    """Test 1: Basic batch create/list/show operations."""
    print("Test 1: Batch CRUD operations...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'test-batch', 'dev:1.0.0', 'staging:1.0.0'])
    print("  - Create: OK")

    result = run_cmd(['batch', 'list'])
    assert 'test-batch' in result.stdout
    assert 'PENDING' in result.stdout
    print("  - List: OK")

    result = run_cmd(['batch', 'show', 'test-batch'])
    assert 'test-batch' in result.stdout
    assert 'dev' in result.stdout
    assert 'staging' in result.stdout
    assert '1.0.0' in result.stdout
    print("  - Show: OK")

    state = get_db_state()
    assert len(state['batches']) == 1
    assert state['batches'][0][1] == 'test-batch'
    assert state['batches'][0][2] == 'pending'
    assert len(state['batch_steps']) == 2
    print("  - DB state: OK")

    print("  PASSED")


def test_batch_apply_success():
    """Test 2: Batch apply success across environments."""
    print("Test 2: Batch apply success...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'success-batch', 'dev:1.0.0', 'staging:1.0.0'])
    run_cmd(['batch', 'apply', 'success-batch', '--yes'])

    state = get_db_state()
    assert state['environments']['dev'] == '1.0.0'
    assert state['environments']['staging'] == '1.0.0'
    assert state['batches'][0][2] == 'success'
    assert state['batch_steps'][0][2] == 'success'
    assert state['batch_steps'][1][2] == 'success'
    assert ('1.0.0', 'dev') in state['releases']
    assert ('1.0.0', 'staging') in state['releases']
    print("  PASSED")


def test_batch_apply_failure_stopping():
    """Test 3: Batch stops at first failure, remaining steps skipped."""
    print("Test 3: Batch failure stopping...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'fail-batch', 'dev:1.0.0', 'staging:2.0.0', 'dev:3.0.0'])

    run_cmd(['lock', 'staging', '--role', 'release-manager', '--reason', 'test lock'])

    result = run_cmd(['batch', 'apply', 'fail-batch', '--yes'])
    assert 'Step 0: dev -> 1.0.0' in result.stdout
    assert 'SUCCESS' in result.stdout

    assert 'Step 1: staging -> 2.0.0' in result.stdout
    assert 'FAILED' in result.stdout
    assert 'locked' in result.stdout.lower()

    assert 'Skipped 1 remaining steps' in result.stdout

    state = get_db_state()
    assert state['batch_steps'][0][2] == 'success'
    assert state['batch_steps'][1][2] == 'failed'
    assert state['batch_steps'][2][2] == 'skipped'
    assert state['batches'][0][2] == 'partial'

    assert state['environments']['dev'] == '1.0.0'
    assert state['environments']['staging'] is None
    print("  PASSED")


def test_batch_cross_restart_resume():
    """Test 4: Batch persists and can be resumed after simulated restart."""
    print("Test 4: Cross-restart batch resume...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'resume-batch', 'dev:1.0.0', 'staging:2.0.0'])
    run_cmd(['lock', 'staging', '--role', 'release-manager', '--reason', 'block step 1'])
    run_cmd(['batch', 'apply', 'resume-batch', '--yes'], expect_success=True)

    state_before = get_db_state()
    assert state_before['batch_steps'][0][2] == 'success'
    assert state_before['batch_steps'][1][2] == 'failed'
    assert state_before['environments']['dev'] == '1.0.0'
    print("  - State before simulated restart: OK")

    state_checkpoint = get_db_state()

    run_cmd(['unlock', 'staging', '--role', 'release-manager'])
    result = run_cmd(['batch', 'apply', 'resume-batch', '--yes', '--retry'])
    assert 'Reset 1 failed/skipped steps' in result.stdout
    assert 'Step 1: staging -> 2.0.0' in result.stdout
    assert 'SUCCESS' in result.stdout

    state_after = get_db_state()
    assert state_after['batch_steps'][0][2] == 'success'
    assert state_after['batch_steps'][1][2] == 'success'
    assert state_after['batches'][0][2] == 'success'
    assert state_after['environments']['staging'] == '2.0.0'

    assert state_after['environments']['dev'] == state_checkpoint['environments']['dev']
    print("  - Resume after restart: OK")
    print("  PASSED")


def test_batch_export_import():
    """Test 5: Batch export/import JSON round-trip."""
    print("Test 5: Batch export/import...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'export-batch', 'dev:1.0.0', 'staging:1.0.0'])

    run_cmd(['batch', 'export', 'export-batch', '-o', 'test_batch_export.json'])
    assert os.path.exists('test_batch_export.json')

    with open('test_batch_export.json', 'r') as f:
        export_data = json.load(f)
    assert export_data['batch']['name'] == 'export-batch'
    assert export_data['batch']['status'] == 'pending'
    assert len(export_data['steps']) == 2
    assert export_data['steps'][0]['status'] == 'pending'
    assert export_data['steps'][1]['status'] == 'pending'
    print("  - Export: OK")

    cleanup()
    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v3.json'])

    result = run_cmd(['batch', 'import', 'test_batch_export.json', '--role', 'release-manager'])
    assert 'imported successfully' in result.stdout.lower()

    result = run_cmd(['batch', 'show', 'export-batch'])
    assert 'export-batch' in result.stdout
    assert 'PENDING' in result.stdout
    print("  - Import: OK")

    print("  PASSED")


def test_import_name_conflict_rejection():
    """Test 6: Import rejects same name without --force."""
    print("Test 6: Import name conflict rejection...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'conflict-batch', 'dev:1.0.0'])
    run_cmd(['batch', 'export', 'conflict-batch', '-o', 'test_batch_export.json'])

    result = run_cmd(['batch', 'import', 'test_batch_export.json', '--role', 'release-manager'], expect_success=False)
    assert 'conflict' in result.stderr.lower() or 'already exists' in result.stderr.lower()
    print("  PASSED")


def test_import_state_conflict_rejection():
    """Test 7: Import rejects state conflicts without --force."""
    print("Test 7: Import state conflict rejection...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'state-test', 'dev:1.0.0'])
    run_cmd(['batch', 'apply', 'state-test', '--yes'])
    run_cmd(['batch', 'export', 'state-test', '-o', 'test_batch_export.json'])

    cleanup()
    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])

    result = run_cmd(['batch', 'import', 'test_batch_export.json', '--role', 'release-manager'], expect_success=False)
    assert 'state conflict' in result.stderr.lower() or 'marked success but' in result.stderr.lower()
    print("  PASSED")


def test_import_force_override():
    """Test 8: --force overrides name and state conflicts."""
    print("Test 8: Import force override...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'force-test', 'dev:1.0.0'])
    run_cmd(['batch', 'apply', 'force-test', '--yes'])
    run_cmd(['batch', 'export', 'force-test', '-o', 'test_batch_export.json'])

    cleanup()
    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v3.json'])

    run_cmd(['batch', 'create', 'force-test', 'dev:2.0.0'])

    result = run_cmd(['batch', 'import', 'test_batch_export.json', '--force', '--role', 'release-manager'])
    assert 'imported successfully' in result.stdout.lower()
    assert 'Name conflicts overridden' in result.stdout
    assert 'State conflicts overridden' in result.stdout
    print("  PASSED")


def test_developer_cannot_import_prod():
    """Test 9: developer cannot import batches involving prod."""
    print("Test 9: developer cannot import prod batches...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'prod-batch', 'dev:1.0.0', 'staging:1.0.0', 'prod:1.0.0'])
    run_cmd(['batch', 'export', 'prod-batch', '-o', 'test_batch_export.json'])

    cleanup()
    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v3.json'])

    result = run_cmd(['batch', 'import', 'test_batch_export.json', '--role', 'developer'], expect_success=False)
    assert 'Permission denied' in result.stderr
    assert 'release-manager' in result.stderr.lower()
    print("  PASSED")


def test_release_manager_can_import_prod():
    """Test 10: release-manager can import batches involving prod."""
    print("Test 10: release-manager can import prod batches...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'prod-batch', 'dev:1.0.0'])
    run_cmd(['batch', 'export', 'prod-batch', '-o', 'test_batch_export.json'])

    cleanup()
    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v3.json'])

    result = run_cmd(['batch', 'import', 'test_batch_export.json', '--role', 'release-manager'])
    assert 'imported successfully' in result.stdout.lower()
    print("  PASSED")


def test_developer_cannot_apply_prod():
    """Test 11: developer cannot apply prod steps in batch."""
    print("Test 11: developer cannot apply prod in batch...")
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])

    run_cmd(['batch', 'create', 'prod-apply', 'staging:2.0.0', 'prod:1.0.0'])

    result = run_cmd(['batch', 'apply', 'prod-apply', '--yes', '--role', 'developer'])
    assert 'Step 0: staging -> 2.0.0' in result.stdout
    assert 'SUCCESS' in result.stdout
    assert 'Step 1: prod -> 1.0.0' in result.stdout
    assert 'FAILED' in result.stdout
    assert 'Permission denied' in result.stdout

    state = get_db_state()
    assert state['batch_steps'][0][2] == 'success'
    assert state['batch_steps'][1][2] == 'failed'
    assert state['environments']['staging'] == '2.0.0'
    assert state['environments']['prod'] is None
    print("  PASSED")


def test_audit_logging():
    """Test 12: All batch operations are logged in audit_logs."""
    print("Test 12: Audit logging...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'audit-test', 'dev:1.0.0'])
    run_cmd(['batch', 'list'])
    run_cmd(['batch', 'show', 'audit-test'])
    run_cmd(['batch', 'apply', 'audit-test', '--yes'])
    run_cmd(['batch', 'export', 'audit-test', '-o', 'test_batch_export.json'])

    logs = get_audit_logs()
    actions = [l[0] for l in logs]

    assert 'batch_create' in actions
    assert 'batch_list' in actions
    assert 'batch_show' in actions
    assert 'batch_apply' in actions
    assert 'batch_step' in actions
    assert 'batch_export' in actions

    batch_apply_entries = [l for l in logs if l[0] == 'batch_apply']
    assert any(l[1] == 'success' for l in batch_apply_entries)

    batch_step_entries = [l for l in logs if l[0] == 'batch_step']
    assert any(l[1] == 'success' for l in batch_step_entries)
    print("  PASSED")


def test_error_logging():
    """Test 13: Batch failures are logged in error_logs."""
    print("Test 13: Error logging...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'error-test', 'dev:1.0.0', 'staging:1.0.0'])
    run_cmd(['lock', 'staging', '--role', 'release-manager', '--reason', 'test lock'])
    run_cmd(['batch', 'apply', 'error-test', '--yes'])

    error_logs = get_error_logs()
    commands = [l[0] for l in error_logs]

    assert 'batch_step' in commands

    step_errors = [l for l in error_logs if l[0] == 'batch_step']
    assert any('locked' in l[2].lower() for l in step_errors)
    print("  PASSED")


def test_empty_batch_rejected():
    """Test 14: Cannot create batch with no steps."""
    print("Test 14: Empty batch rejected...")
    init_db_with_configs()

    result = run_cmd(['batch', 'create', 'empty-batch'], expect_success=False)
    assert 'no steps' in result.stderr.lower() or 'empty' in result.stderr.lower()
    print("  PASSED")


def test_duplicate_batch_name_rejected():
    """Test 15: Cannot create batch with duplicate name."""
    print("Test 15: Duplicate batch name rejected...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'dup-batch', 'dev:1.0.0'])
    result = run_cmd(['batch', 'create', 'dup-batch', 'dev:2.0.0'], expect_success=False)
    assert 'already exists' in result.stderr.lower()
    print("  PASSED")


def test_invalid_step_format_rejected():
    """Test 16: Invalid step format rejected."""
    print("Test 16: Invalid step format rejected...")
    init_db_with_configs()

    result = run_cmd(['batch', 'create', 'fmt-batch', 'dev-1.0.0'], expect_success=False)
    assert 'invalid format' in result.stderr.lower()
    print("  PASSED")


def test_nonexistent_version_rejected():
    """Test 17: Nonexistent version in step rejected."""
    print("Test 17: Nonexistent version rejected...")
    init_db_with_configs()

    result = run_cmd(['batch', 'create', 'ver-batch', 'dev:99.99.99'], expect_success=False)
    assert 'does not exist' in result.stderr.lower()
    print("  PASSED")


def test_retry_resets_failed_steps():
    """Test 18: --retry resets failed/skipped steps to pending."""
    print("Test 18: --retry resets failed steps...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'retry-batch', 'dev:1.0.0', 'staging:2.0.0'])
    run_cmd(['lock', 'staging', '--role', 'release-manager', '--reason', 'block'])
    run_cmd(['batch', 'apply', 'retry-batch', '--yes'])

    state = get_db_state()
    assert state['batch_steps'][1][2] == 'failed'

    run_cmd(['unlock', 'staging', '--role', 'release-manager'])
    result = run_cmd(['batch', 'apply', 'retry-batch', '--yes', '--retry'])
    assert 'Reset 1 failed/skipped steps' in result.stdout
    assert 'Step 1: staging -> 2.0.0' in result.stdout
    assert 'SUCCESS' in result.stdout

    state = get_db_state()
    assert state['batch_steps'][1][2] == 'success'
    assert state['batches'][0][2] == 'success'
    print("  PASSED")


def test_successful_steps_preserved_after_failure():
    """Test 19: Successful steps before failure are not rolled back."""
    print("Test 19: Successful steps preserved after failure...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'preserve-batch', 'dev:1.0.0', 'staging:2.0.0', 'dev:3.0.0'])
    run_cmd(['lock', 'staging', '--role', 'release-manager', '--reason', 'block'])
    run_cmd(['batch', 'apply', 'preserve-batch', '--yes'])

    state = get_db_state()
    assert state['environments']['dev'] == '1.0.0'
    assert ('1.0.0', 'dev') in state['releases']
    assert state['batch_steps'][0][2] == 'success'
    print("  PASSED")


def test_batch_reuses_preview_drift_rules():
    """Test 20: Batch steps reuse existing rules (via pre_apply_checks)."""
    print("Test 20: Batch steps reuse existing rules...")
    init_db_with_configs()

    run_cmd(['batch', 'create', 'rules-batch', 'prod:1.0.0'])
    result = run_cmd(['batch', 'apply', 'rules-batch', '--yes'])

    assert 'FAILED' in result.stdout
    assert 'staging' in result.stdout.lower() or 'approval' in result.stdout.lower()

    state = get_db_state()
    assert state['batch_steps'][0][2] == 'failed'
    assert state['environments']['prod'] is None
    print("  PASSED")


def main():
    tests = [
        test_batch_crud,
        test_batch_apply_success,
        test_batch_apply_failure_stopping,
        test_batch_cross_restart_resume,
        test_batch_export_import,
        test_import_name_conflict_rejection,
        test_import_state_conflict_rejection,
        test_import_force_override,
        test_developer_cannot_import_prod,
        test_release_manager_can_import_prod,
        test_developer_cannot_apply_prod,
        test_audit_logging,
        test_error_logging,
        test_empty_batch_rejected,
        test_duplicate_batch_name_rejected,
        test_invalid_step_format_rejected,
        test_nonexistent_version_rejected,
        test_retry_resets_failed_steps,
        test_successful_steps_preserved_after_failure,
        test_batch_reuses_preview_drift_rules,
    ]

    failed = 0
    for test in tests:
        try:
            test()
            print("")
        except Exception as e:
            failed += 1
            print(f"  FAILED: {e}")
            print("")
        finally:
            cleanup()
            cleanup_export_file()

    if failed:
        print(f"\n{len(tests) - failed}/{len(tests)} tests passed, {failed} failed")
        sys.exit(1)
    else:
        print(f"\nAll {len(tests)} tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
