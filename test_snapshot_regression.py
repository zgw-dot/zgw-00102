#!/usr/bin/env python
"""Regression tests for snapshot import/export functionality.

Tests:
1. Cross-restart recovery: Export snapshot, reinit DB, import, verify state
2. Conflict rejection: Default behavior rejects existing data
3. --force override: Force flag overwrites conflicting data
4. Permission restrictions: developer cannot restore prod lock/approval status
5. Import failure rollback: No partial data on import failure
6. CLI verification: Actual CLI commands work with JSON files
"""

import os
import sys
import json
import subprocess
import sqlite3
from datetime import datetime

DB_FILE = "pipeline.db"
SCRIPT = "pipeline.py"
SNAPSHOT_FILE = "test_snapshot.json"
SNAPSHOT_FILE_2 = "test_snapshot_2.json"


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
    for f in [DB_FILE, SNAPSHOT_FILE, SNAPSHOT_FILE_2,
              'corrupt_snapshot.json', 'invalid_snapshot.json']:
        if os.path.exists(f):
            os.remove(f)


def init_db_with_state():
    """Initialize database with a known state for testing."""
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

    run_cmd(['init'])

    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])

    run_cmd(['apply', '1.0.0', 'dev', '--yes'])
    run_cmd(['apply', '2.0.0', 'staging', '--yes'])

    run_cmd(['lock', 'prod', '--reason', 'Test lock for regression', '--role', 'release-manager'])

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod', '--notes', 'Pending approval test'])

    print("  Database initialized with known state")


def get_db_state():
    """Get current database state for comparison."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT version FROM configs ORDER BY version")
    configs = [r['version'] for r in cursor.fetchall()]

    cursor.execute("SELECT name, current_version FROM environments ORDER BY name")
    envs = {r['name']: r['current_version'] for r in cursor.fetchall()}

    cursor.execute("SELECT environment, is_locked, lock_reason FROM environment_locks ORDER BY environment")
    locks = {r['environment']: {
        'is_locked': r['is_locked'] == 1,
        'lock_reason': r['lock_reason']
    } for r in cursor.fetchall()}

    cursor.execute("SELECT version, environment, status FROM approvals ORDER BY version, environment")
    approvals = {(r['version'], r['environment']): r['status'] for r in cursor.fetchall()}

    conn.close()

    return {
        'configs': configs,
        'environments': envs,
        'locks': locks,
        'approvals': approvals,
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


def test_cross_restart_recovery():
    """Test 1: Export snapshot, reinit DB, import, verify state persists across restart."""
    print("\n" + "=" * 60)
    print("TEST 1: Cross-restart snapshot recovery")
    print("=" * 60)

    init_db_with_state()
    original_state = get_db_state()
    print(f"  Original state:")
    print(f"    Configs: {original_state['configs']}")
    print(f"    Environments: {original_state['environments']}")
    print(f"    Locks: {original_state['locks']}")
    print(f"    Approvals: {original_state['approvals']}")

    run_cmd(['snapshot', 'export', '--output', SNAPSHOT_FILE])

    with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
        snapshot = json.load(f)

    assert 'snapshot_metadata' in snapshot
    assert snapshot['snapshot_metadata']['snapshot_version'] == '1.0'
    assert len(snapshot['configs']) == 2
    assert len(snapshot['environments']) == 3
    assert len(snapshot['environment_locks']) == 3
    assert len(snapshot['approvals']) >= 1

    print(f"  Snapshot exported successfully")
    config_versions = [c['version'] for c in snapshot['configs']]
    print(f"    Configs in snapshot: {config_versions}")
    print(f"    Environments: {snapshot['environments']}")

    os.remove(DB_FILE)
    print("  Database deleted (simulating fresh environment)")

    run_cmd(['init'])
    print("  Database re-initialized")

    run_cmd(['snapshot', 'import', SNAPSHOT_FILE, '--role', 'release-manager'])

    imported_state = get_db_state()
    print(f"  Imported state:")
    print(f"    Configs: {imported_state['configs']}")
    print(f"    Environments: {imported_state['environments']}")
    print(f"    Locks: {imported_state['locks']}")
    print(f"    Approvals: {imported_state['approvals']}")

    assert imported_state['configs'] == original_state['configs']
    assert imported_state['environments']['dev'] == original_state['environments']['dev']
    assert imported_state['environments']['staging'] == original_state['environments']['staging']
    assert imported_state['locks']['prod']['is_locked'] == original_state['locks']['prod']['is_locked']
    assert imported_state['locks']['prod']['lock_reason'] == original_state['locks']['prod']['lock_reason']
    assert imported_state['approvals'] == original_state['approvals']

    logs = get_audit_logs()
    snapshot_import_logs = [l for l in logs if l[0] == 'snapshot_import' and l[1] == 'success']
    assert len(snapshot_import_logs) >= 1, "Expected at least one successful snapshot_import in audit logs"
    print(f"  Audit log verified: {len(snapshot_import_logs)} successful snapshot_import records found")

    print("\n  [OK] PASSED: Cross-restart recovery works correctly")


def test_conflict_rejection():
    """Test 2: Default behavior rejects conflicts without --force."""
    print("\n" + "=" * 60)
    print("TEST 2: Conflict rejection (default behavior)")
    print("=" * 60)

    init_db_with_state()

    run_cmd(['snapshot', 'export', '--output', SNAPSHOT_FILE])

    result = run_cmd(['snapshot', 'import', SNAPSHOT_FILE], expect_success=False)

    assert 'Conflicts detected' in result.stderr
    assert 'Config version' in result.stderr
    assert 'Use --force to overwrite' in result.stderr

    print("  Conflicts correctly rejected:")
    for line in result.stderr.split('\n'):
        if line.strip():
            print(f"    {line.strip()}")

    logs = get_audit_logs()
    failed_imports = [l for l in logs if l[0] == 'snapshot_import' and l[1] == 'failed']
    assert len(failed_imports) == 1
    assert 'Conflicts detected' in failed_imports[0][2]

    print("  Failure audit log recorded correctly")

    print("\n  [OK] PASSED: Conflicts are rejected by default")


def test_force_override():
    """Test 3: --force flag overwrites conflicting data."""
    print("\n" + "=" * 60)
    print("TEST 3: --force override of conflicts")
    print("=" * 60)

    init_db_with_state()
    original_state = get_db_state()

    run_cmd(['snapshot', 'export', '--output', SNAPSHOT_FILE])

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE configs SET config_json = ? WHERE version = '1.0.0'",
                   (json.dumps({"version": "1.0.0", "modified": True}),))
    cursor.execute("UPDATE environments SET current_version = '2.0.0' WHERE name = 'dev'")
    conn.commit()
    conn.close()

    modified_state = get_db_state()
    assert modified_state['environments']['dev'] == '2.0.0'
    assert modified_state['environments']['dev'] != original_state['environments']['dev']

    result = run_cmd(['snapshot', 'import', SNAPSHOT_FILE, '--force', '--role', 'release-manager'])

    assert 'Conflicts detected, using --force to overwrite' in result.stdout

    imported_state = get_db_state()

    assert imported_state['environments']['dev'] == original_state['environments']['dev']
    assert imported_state['configs'] == original_state['configs']

    print(f"  After force import:")
    print(f"    Configs: {imported_state['configs']}")
    print(f"    Dev environment version: {imported_state['environments']['dev']}")

    logs = get_audit_logs()
    success_imports = [l for l in logs if l[0] == 'snapshot_import' and l[1] == 'success']
    assert len(success_imports) >= 1

    print("\n  [OK] PASSED: --force correctly overwrites conflicts")


def test_permission_restrictions():
    """Test 4: developer cannot restore prod lock and approval status."""
    print("\n" + "=" * 60)
    print("TEST 4: Permission restrictions (developer vs release-manager)")
    print("=" * 60)

    init_db_with_state()
    run_cmd(['snapshot', 'export', '--output', SNAPSHOT_FILE])

    os.remove(DB_FILE)
    run_cmd(['init'])

    result = run_cmd(
        ['snapshot', 'import', SNAPSHOT_FILE, '--role', 'developer']
    )

    assert 'Permission restrictions applied' in result.stdout
    assert 'Skipped prod approval' in result.stdout
    assert 'Skipped prod lock status' in result.stdout

    state_dev = get_db_state()

    assert state_dev['locks']['prod']['is_locked'] == False
    assert len(state_dev['approvals']) == 0

    print(f"  Developer import state:")
    print(f"    Prod locked: {state_dev['locks']['prod']['is_locked']}")
    print(f"    Approvals count: {len(state_dev['approvals'])}")

    os.remove(DB_FILE)
    run_cmd(['init'])

    result_rm = run_cmd(
        ['snapshot', 'import', SNAPSHOT_FILE, '--role', 'release-manager']
    )

    assert 'Permission restrictions applied' not in result_rm.stdout

    state_rm = get_db_state()

    assert state_rm['locks']['prod']['is_locked'] == True
    assert len(state_rm['approvals']) == 1

    print(f"  Release-manager import state:")
    print(f"    Prod locked: {state_rm['locks']['prod']['is_locked']}")
    print(f"    Approvals count: {len(state_rm['approvals'])}")

    print("\n  [OK] PASSED: Permission restrictions work correctly")


def test_import_failure_rollback():
    """Test 5: Import failure does not leave partial data."""
    print("\n" + "=" * 60)
    print("TEST 5: Import failure rollback (atomic transaction)")
    print("=" * 60)

    init_db_with_state()
    run_cmd(['snapshot', 'export', '--output', SNAPSHOT_FILE])

    with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
        valid_snapshot = json.load(f)

    corrupt_snapshot = json.loads(json.dumps(valid_snapshot))
    corrupt_snapshot['configs'][0]['version'] = None
    with open('corrupt_snapshot.json', 'w', encoding='utf-8') as f:
        json.dump(corrupt_snapshot, f)

    original_state = get_db_state()

    result = run_cmd(
        ['snapshot', 'import', 'corrupt_snapshot.json', '--role', 'release-manager'],
        expect_success=False
    )

    after_failure_state = get_db_state()

    assert after_failure_state['configs'] == original_state['configs']
    assert after_failure_state['environments'] == original_state['environments']
    assert after_failure_state['locks'] == original_state['locks']
    assert after_failure_state['approvals'] == original_state['approvals']

    print("  State after failed import:")
    print(f"    Configs: {after_failure_state['configs']}")
    print(f"    Environments: {after_failure_state['environments']}")
    print(f"    No partial changes detected")

    logs = get_audit_logs()
    failed_imports = [l for l in logs if l[0] == 'snapshot_import' and l[1] == 'failed']
    assert len(failed_imports) == 1

    print("  Failure audit log recorded")

    invalid_snapshot = {'invalid_key': 'invalid'}
    with open('invalid_snapshot.json', 'w', encoding='utf-8') as f:
        json.dump(invalid_snapshot, f)

    state_before_invalid = get_db_state()
    result_invalid = run_cmd(
        ['snapshot', 'import', 'invalid_snapshot.json'],
        expect_success=False
    )

    assert 'Invalid snapshot format' in result_invalid.stderr

    after_invalid_state = get_db_state()
    assert after_invalid_state == state_before_invalid

    print("  Invalid format also rolls back correctly")

    print("\n  [OK] PASSED: Import failures rollback completely")


def test_cli_json_verification():
    """Test 6: Actual CLI commands work with JSON files."""
    print("\n" + "=" * 60)
    print("TEST 6: CLI JSON verification (end-to-end)")
    print("=" * 60)

    init_db_with_state()
    run_cmd(['snapshot', 'export', '--output', SNAPSHOT_FILE])

    with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
        export_json = json.load(f)

    assert export_json['snapshot_metadata']['snapshot_version'] == '1.0'
    assert 'configs' in export_json
    assert 'environments' in export_json
    assert 'approvals' in export_json
    assert 'environment_locks' in export_json

    print("  Exported JSON structure valid")

    for cfg in export_json['configs']:
        assert 'version' in cfg
        assert 'config_json' in cfg
        assert 'created_by' in cfg
        assert 'created_at' in cfg
        assert isinstance(cfg['config_json'], dict)
        assert 'app_name' in cfg['config_json']

    for env in export_json['environments']:
        assert 'name' in env
        assert 'current_version' in env

    for app in export_json['approvals']:
        assert 'version' in app
        assert 'environment' in app
        assert 'status' in app
        assert 'requested_by' in app
        assert 'requested_at' in app

    for lock in export_json['environment_locks']:
        assert 'environment' in lock
        assert 'is_locked' in lock
        assert lock['is_locked'] in [True, False]

    print("  All required fields present in JSON")

    result_stdout = run_cmd(['snapshot', 'export'])

    stdout_json = json.loads(result_stdout.stdout.strip())
    assert stdout_json['snapshot_metadata']['snapshot_version'] == '1.0'

    print("  Stdout export also produces valid JSON")

    os.remove(DB_FILE)
    run_cmd(['init'])

    run_cmd(['snapshot', 'import', SNAPSHOT_FILE, '--role', 'release-manager'])

    run_cmd(['snapshot', 'export', '--output', SNAPSHOT_FILE_2])

    with open(SNAPSHOT_FILE_2, 'r', encoding='utf-8') as f:
        reexport_json = json.load(f)

    orig_config_versions = sorted([c['version'] for c in export_json['configs']])
    reexp_config_versions = sorted([c['version'] for c in reexport_json['configs']])
    assert orig_config_versions == reexp_config_versions

    print(f"  Re-export after import matches original")
    print(f"    Original configs: {orig_config_versions}")
    print(f"    Re-exported configs: {reexp_config_versions}")

    orig_envs = {e['name']: e['current_version'] for e in export_json['environments']}
    reexp_envs = {e['name']: e['current_version'] for e in reexport_json['environments']}
    assert orig_envs == reexp_envs

    orig_locks = {l['environment']: l['is_locked'] for l in export_json['environment_locks']}
    reexp_locks = {l['environment']: l['is_locked'] for l in reexport_json['environment_locks']}
    assert orig_locks == reexp_locks

    orig_approvals = sorted([(a['version'], a['environment']) for a in export_json['approvals']])
    reexp_approvals = sorted([(a['version'], a['environment']) for a in reexport_json['approvals']])
    assert orig_approvals == reexp_approvals

    print("\n  [OK] PASSED: CLI JSON import/export works correctly")


def test_env_var_role():
    """Test role from environment variable works."""
    print("\n" + "=" * 60)
    print("TEST 7: Role from environment variable")
    print("=" * 60)

    init_db_with_state()
    run_cmd(['snapshot', 'export', '--output', SNAPSHOT_FILE])

    os.remove(DB_FILE)
    run_cmd(['init'])

    env = {'PIPELINE_ROLE': 'developer'}
    result = run_cmd(['snapshot', 'import', SNAPSHOT_FILE], env=env)

    assert 'Role: developer' in result.stdout
    assert 'Permission restrictions applied' in result.stdout

    print("  Role from env var works correctly")

    os.remove(DB_FILE)
    run_cmd(['init'])

    env_rm = {'PIPELINE_ROLE': 'release-manager'}
    result_rm = run_cmd(['snapshot', 'import', SNAPSHOT_FILE], env=env_rm)

    assert 'Role: release-manager' in result_rm.stdout
    assert 'Permission restrictions applied' not in result_rm.stdout

    print("  Release-manager role from env var works")

    print("\n  [OK] PASSED: Environment variable role works")


def main():
    print("=" * 60)
    print("Snapshot Import/Export Regression Tests")
    print("=" * 60)

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    cleanup()

    try:
        test_cross_restart_recovery()
        test_conflict_rejection()
        test_force_override()
        test_permission_restrictions()
        test_import_failure_rollback()
        test_cli_json_verification()
        test_env_var_role()

        print("\n" + "=" * 60)
        print("ALL SNAPSHOT REGRESSION TESTS PASSED! [OK]")
        print("=" * 60)

    finally:
        print("\n" + "=" * 60)
        print("CLEANUP")
        print("=" * 60)
        cleanup()
        print("  Cleanup complete.")


if __name__ == "__main__":
    main()
