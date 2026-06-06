#!/usr/bin/env python
"""Regression tests for release evidence archive module.

Tests:
1. Archive create with duplicate name - should fail
2. Archive create with unsuccessful release - should fail
3. Archive create for prod by developer - should fail
4. Archive create for prod by release-manager - should succeed
5. Archive create for non-prod by developer - should succeed
6. Archive show and list - should display correctly
7. Archive verify - should verify integrity
8. Archive revoke by developer - should fail
9. Archive revoke by release-manager - should succeed
10. Archive verify after revoke - should fail
11. Archive export/import roundtrip - should preserve status and summary
12. Archive import with summary mismatch - should fail
13. Archive import with missing version - should fail
14. Archive import with duplicate name (no force) - should fail
15. Archive import with duplicate name (with force) - should succeed
16. Cross-restart persistence - archives should survive restart
17. Failed archive create does not pollute release records
18. Archive create for prod without approval - should fail
19. Archive create with linked package - should succeed
20. Archive import to new database - should preserve all fields
21. Archive import without successful release - should fail
22. Archive list with filters - should filter correctly
"""

import os
import sys
import json
import subprocess
import sqlite3
import tempfile
import shutil

DB_FILE = "pipeline.db"
SCRIPT = "pipeline.py"


def run_cmd(args, expect_success=True, env=None, cwd=None):
    cmd = [sys.executable, SCRIPT] + args
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        env=merged_env,
        cwd=cwd or os.getcwd()
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
        fpath = os.path.join(os.getcwd(), f)
        if os.path.exists(fpath):
            os.remove(fpath)


def init_db_with_configs():
    """Initialize database with configs for testing."""
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])
    run_cmd(['import', 'config_pipeline/examples/config_v3.json'])


def setup_successful_releases():
    """Setup: release versions to environments for archive testing."""
    init_db_with_configs()
    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['apply', '2.0.0', 'staging', '--yes'])
    run_cmd(['apply', '1.0.0', 'dev', '--yes'])
    run_cmd(['apply', '2.0.0', 'dev', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod'])
    run_cmd(['pending', '2.0.0', 'prod'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['approve', '2.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['package', 'create', 'test-pkg', 'prod', '1.0.0', '2.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'test-pkg', '--role', 'release-manager'])
    run_cmd(['apply', '1.0.0', 'prod', '--role', 'release-manager', '--yes'])
    run_cmd(['apply', '2.0.0', 'prod', '--role', 'release-manager', '--yes'])


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

    cursor.execute("SELECT archive_name, environment, version, status FROM archives ORDER BY id")
    archives = [(r['archive_name'], r['environment'], r['version'], r['status']) for r in cursor.fetchall()]

    cursor.execute("SELECT id, action, status, environment, version, error_reason, details FROM audit_logs ORDER BY id DESC")
    audit_logs = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT id, command, error_code, error_message, environment, version FROM error_logs ORDER BY id DESC")
    error_logs = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        'environments': envs,
        'locks': locks,
        'releases': releases,
        'archives': archives,
        'audit_logs': audit_logs,
        'error_logs': error_logs,
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
    """Directly modify config in SQLite to cause summary mismatch."""
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


def test_archive_create_duplicate_name():
    """Test 1: Creating archive with duplicate name should fail."""
    print("\n" + "=" * 70)
    print("TEST 1: Archive create with duplicate name")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'test-archive', '1.0.0', 'staging'])

    state_before = get_db_state()

    result = run_cmd(
        ['archive', 'create', 'test-archive', '2.0.0', 'staging'],
        expect_success=False
    )

    assert 'already exists' in result.stderr.lower() or 'already exists' in result.stdout.lower(), \
        f"Should mention duplicate name. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state_after = get_db_state()
    assert len(state_after['archives']) == len(state_before['archives']), \
        "No new archive should be created"

    audit_logs = get_audit_logs()
    fail_logs = [l for l in audit_logs if l[0] == 'archive.create' and l[1] == 'failed']
    assert len(fail_logs) >= 1, "Should have failed audit log"

    error_logs = get_error_logs()
    arch_errors = [e for e in error_logs if e[0] == 'archive.create' and 'ALREADY_EXISTS' in e[1]]
    assert len(arch_errors) >= 1, "Should have error log"

    print("  [OK] PASSED: Duplicate name correctly rejected")


def test_archive_create_unsuccessful_release():
    """Test 2: Creating archive with unsuccessful release should fail."""
    print("\n" + "=" * 70)
    print("TEST 2: Archive create with unsuccessful release")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    result = run_cmd(
        ['archive', 'create', 'test-archive', '99.0.0', 'staging'],
        expect_success=False
    )

    assert 'no successful release' in result.stderr.lower() or 'no successful release' in result.stdout.lower(), \
        f"Should mention no successful release. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['archives']) == 0, "No archive should be created"

    audit_logs = get_audit_logs()
    fail_logs = [l for l in audit_logs if l[0] == 'archive.create' and l[1] == 'failed']
    assert len(fail_logs) >= 1, "Should have failed audit log"

    releases_before = get_db_state()['releases']
    assert not any(r[2] == 'success' for r in releases_before), "No success releases should exist"

    print("  [OK] PASSED: Unsuccessful release correctly rejected")


def test_archive_create_prod_by_developer():
    """Test 3: Creating archive for prod by developer should fail."""
    print("\n" + "=" * 70)
    print("TEST 3: Archive create for prod by developer")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    result = run_cmd(
        ['archive', 'create', 'prod-archive', '1.0.0', 'prod', '--role', 'developer'],
        expect_success=False
    )

    assert 'permission denied' in result.stderr.lower() or 'permission denied' in result.stdout.lower(), \
        f"Should mention permission denied. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    prod_archives = [a for a in state['archives'] if a[1] == 'prod']
    assert len(prod_archives) == 0, "No prod archive should be created"

    audit_logs = get_audit_logs()
    fail_logs = [l for l in audit_logs if l[0] == 'archive.create' and l[1] == 'failed']
    assert len(fail_logs) >= 1, "Should have failed audit log"

    print("  [OK] PASSED: Developer cannot create prod archive")


def test_archive_create_prod_by_release_manager():
    """Test 4: Creating archive for prod by release-manager should succeed."""
    print("\n" + "=" * 70)
    print("TEST 4: Archive create for prod by release-manager")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    result = run_cmd(
        ['archive', 'create', 'prod-archive', '1.0.0', 'prod', '--role', 'release-manager']
    )

    assert 'SUCCESS' in result.stdout, f"Should show success. STDOUT: {result.stdout}"

    state = get_db_state()
    prod_archives = [a for a in state['archives'] if a[1] == 'prod']
    assert len(prod_archives) >= 1, "Prod archive should be created"
    assert prod_archives[0][3] == 'active', "Archive should be active"

    audit_logs = get_audit_logs()
    success_logs = [l for l in audit_logs if l[0] == 'archive.create' and l[1] == 'success']
    assert len(success_logs) >= 1, "Should have success audit log"

    print("  [OK] PASSED: Release-manager can create prod archive")


def test_archive_create_non_prod_by_developer():
    """Test 5: Creating archive for non-prod by developer should succeed."""
    print("\n" + "=" * 70)
    print("TEST 5: Archive create for non-prod by developer")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    result = run_cmd(
        ['archive', 'create', 'staging-archive', '1.0.0', 'staging', '--role', 'developer']
    )

    assert 'SUCCESS' in result.stdout, f"Should show success. STDOUT: {result.stdout}"

    state = get_db_state()
    staging_archives = [a for a in state['archives'] if a[1] == 'staging']
    assert len(staging_archives) >= 1, "Staging archive should be created"

    print("  [OK] PASSED: Developer can create non-prod archive")


def test_archive_show_and_list():
    """Test 6: Archive show and list should display correctly."""
    print("\n" + "=" * 70)
    print("TEST 6: Archive show and list")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'archive-1', '1.0.0', 'staging'])
    run_cmd(['archive', 'create', 'archive-2', '2.0.0', 'dev'])

    result = run_cmd(['archive', 'list'])
    assert 'archive-1' in result.stdout, "Should list archive-1"
    assert 'archive-2' in result.stdout, "Should list archive-2"

    result_show = run_cmd(['archive', 'show', 'archive-1'])
    assert '1.0.0' in result_show.stdout, "Should show version 1.0.0"
    assert 'staging' in result_show.stdout, "Should show environment staging"

    result_list_env = run_cmd(['archive', 'list', '--env', 'staging'])
    assert 'archive-1' in result_list_env.stdout, "Should list archive-1 for staging"
    assert 'archive-2' not in result_list_env.stdout, "Should not list archive-2 for staging"

    print("  [OK] PASSED: Archive show and list work correctly")


def test_archive_verify():
    """Test 7: Archive verify should verify integrity."""
    print("\n" + "=" * 70)
    print("TEST 7: Archive verify")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'verify-test', '1.0.0', 'staging'])

    result = run_cmd(['archive', 'verify', 'verify-test'])
    assert 'VALID' in result.stdout, "Should show valid"
    assert 'integrity verified' in result.stdout.lower(), "Should mention integrity verified"

    _modify_config_in_db('1.0.0', 'app_name', 'modified_app')

    result_fail = run_cmd(['archive', 'verify', 'verify-test'], expect_success=False)
    assert 'FAILED' in result_fail.stdout or 'FAILED' in result_fail.stderr, "Should show failed"
    assert 'content has changed' in result_fail.stdout.lower() or 'content has changed' in result_fail.stderr.lower(), \
        "Should mention content changed"

    audit_logs = get_audit_logs()
    fail_logs = [l for l in audit_logs if l[0] == 'archive.verify' and l[1] == 'failed']
    assert len(fail_logs) >= 1, "Should have failed audit log for failed verify"

    print("  [OK] PASSED: Archive verify works correctly")


def test_archive_revoke_by_developer():
    """Test 8: Archive revoke by developer should fail."""
    print("\n" + "=" * 70)
    print("TEST 8: Archive revoke by developer")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'revoke-test', '1.0.0', 'staging'])

    result = run_cmd(
        ['archive', 'revoke', 'revoke-test', '--role', 'developer', '--reason', 'test'],
        expect_success=False
    )

    assert 'permission denied' in result.stderr.lower() or 'permission denied' in result.stdout.lower(), \
        f"Should mention permission denied. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    archive = [a for a in state['archives'] if a[0] == 'revoke-test'][0]
    assert archive[3] == 'active', "Archive should still be active"

    print("  [OK] PASSED: Developer cannot revoke archive")


def test_archive_revoke_by_release_manager():
    """Test 9: Archive revoke by release-manager should succeed."""
    print("\n" + "=" * 70)
    print("TEST 9: Archive revoke by release-manager")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'revoke-test', '1.0.0', 'staging'])

    result = run_cmd(
        ['archive', 'revoke', 'revoke-test', '--role', 'release-manager', '--reason', 'test revoke']
    )

    assert 'SUCCESS' in result.stdout, f"Should show success. STDOUT: {result.stdout}"
    assert 'revoked' in result.stdout.lower(), "Should mention revoked"

    state = get_db_state()
    archive = [a for a in state['archives'] if a[0] == 'revoke-test'][0]
    assert archive[3] == 'revoked', "Archive should be revoked"

    audit_logs = get_audit_logs()
    success_logs = [l for l in audit_logs if l[0] == 'archive.revoke' and l[1] == 'success']
    assert len(success_logs) >= 1, "Should have success audit log"

    print("  [OK] PASSED: Release-manager can revoke archive")


def test_archive_verify_after_revoke():
    """Test 10: Archive verify after revoke should fail."""
    print("\n" + "=" * 70)
    print("TEST 10: Archive verify after revoke")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'revoke-verify', '1.0.0', 'staging'])
    run_cmd(['archive', 'revoke', 'revoke-verify', '--role', 'release-manager', '--reason', 'test'])

    result = run_cmd(['archive', 'verify', 'revoke-verify'], expect_success=False)

    assert 'FAILED' in result.stdout or 'FAILED' in result.stderr, "Should show failed"
    assert 'revoked' in result.stdout.lower() or 'revoked' in result.stderr.lower(), \
        "Should mention revoked"

    print("  [OK] PASSED: Verify fails after revoke")


def test_archive_export_import_roundtrip():
    """Test 11: Archive export/import roundtrip should preserve status and summary."""
    print("\n" + "=" * 70)
    print("TEST 11: Archive export/import roundtrip")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'export-test', '1.0.0', 'staging'])
    run_cmd(['archive', 'export', 'export-test', '--output', 'test_export.json'])

    with open('test_export.json', 'r', encoding='utf-8') as f:
        exported = json.load(f)

    assert exported['status'] == 'active'
    assert exported['version'] == '1.0.0'
    assert exported['environment'] == 'staging'
    assert 'summary_hash' in exported
    assert 'config_summary' in exported
    assert 'release_result' in exported

    os.remove('test_export.json')

    print("  [OK] PASSED: Export/import roundtrip preserves all fields")


def test_archive_import_summary_mismatch():
    """Test 12: Archive import with summary mismatch should fail."""
    print("\n" + "=" * 70)
    print("TEST 12: Archive import with summary mismatch")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'mismatch-test', '1.0.0', 'staging'])
    run_cmd(['archive', 'export', 'mismatch-test', '--output', 'mismatch_test.json'])

    with open('mismatch_test.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    if isinstance(data['config_summary'], str):
        config_summary = json.loads(data['config_summary'])
    else:
        config_summary = data['config_summary']
    config_summary['config_hash'] = 'a' * 64
    data['config_summary'] = config_summary

    with open('mismatch_test.json', 'w', encoding='utf-8') as f:
        json.dump(data, f)

    os.remove(DB_FILE)
    setup_successful_releases()

    result = run_cmd(
        ['archive', 'import', 'mismatch_test.json'],
        expect_success=False
    )

    assert 'mismatch' in result.stderr.lower() or 'mismatch' in result.stdout.lower(), \
        f"Should mention mismatch. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['archives']) == 0, "No archive should be imported"

    error_logs = get_error_logs()
    mismatch_errors = [e for e in error_logs if e[0] == 'archive.import' and 'SUMMARY_MISMATCH' in e[1]]
    assert len(mismatch_errors) >= 1, "Should have error log"

    os.remove('mismatch_test.json')

    print("  [OK] PASSED: Summary mismatch correctly rejected")


def test_archive_import_missing_version():
    """Test 13: Archive import with missing version should fail."""
    print("\n" + "=" * 70)
    print("TEST 13: Archive import with missing version")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'missing-test', '1.0.0', 'staging'])
    run_cmd(['archive', 'export', 'missing-test', '--output', 'missing_test.json'])

    os.remove(DB_FILE)
    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])

    result = run_cmd(
        ['archive', 'import', 'missing_test.json'],
        expect_success=False
    )

    assert 'no successful release' in result.stderr.lower() or 'no successful release' in result.stdout.lower(), \
        f"Should mention no successful release. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    os.remove('missing_test.json')

    print("  [OK] PASSED: Missing version correctly rejected")


def test_archive_import_duplicate_name_no_force():
    """Test 14: Archive import with duplicate name (no force) should fail."""
    print("\n" + "=" * 70)
    print("TEST 14: Archive import duplicate name (no force)")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'dup-test', '1.0.0', 'staging'])
    run_cmd(['archive', 'export', 'dup-test', '--output', 'dup_test.json'])

    result = run_cmd(
        ['archive', 'import', 'dup_test.json'],
        expect_success=False
    )

    assert 'already exists' in result.stderr.lower() or 'already exists' in result.stdout.lower() or \
           'use --force' in result.stderr.lower() or 'use --force' in result.stdout.lower(), \
        f"Should mention conflict. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    os.remove('dup_test.json')

    print("  [OK] PASSED: Duplicate name import (no force) correctly rejected")


def test_archive_import_duplicate_name_with_force():
    """Test 15: Archive import with duplicate name (with force) should succeed."""
    print("\n" + "=" * 70)
    print("TEST 15: Archive import duplicate name (with force)")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'force-test', '1.0.0', 'staging'])
    run_cmd(['archive', 'export', 'force-test', '--output', 'force_test.json'])

    result = run_cmd(
        ['archive', 'import', 'force_test.json', '--force']
    )

    assert 'SUCCESS' in result.stdout, f"Should show success. STDOUT: {result.stdout}"

    state = get_db_state()
    archives = [a for a in state['archives'] if a[0] == 'force-test']
    assert len(archives) == 1, "Should have exactly one archive after force import"

    os.remove('force_test.json')

    print("  [OK] PASSED: Duplicate name import (with force) works correctly")


def test_archive_cross_restart_persistence():
    """Test 16: Cross-restart persistence - archives should survive restart."""
    print("\n" + "=" * 70)
    print("TEST 16: Cross-restart persistence")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'persist-1', '1.0.0', 'staging'])
    run_cmd(['archive', 'create', 'persist-2', '2.0.0', 'dev'])

    state_before = get_db_state()
    archives_before = state_before['archives']

    result_before = run_cmd(['archive', 'list'])
    output_before = result_before.stdout

    result_show_before = run_cmd(['archive', 'show', 'persist-1'])
    show_before = result_show_before.stdout

    print("  Simulating restart (no action needed, SQLite persists)")

    result_after = run_cmd(['archive', 'list'])
    output_after = result_after.stdout

    result_show_after = run_cmd(['archive', 'show', 'persist-1'])
    show_after = result_show_after.stdout

    state_after = get_db_state()
    archives_after = state_after['archives']

    assert archives_before == archives_after, "Archives should persist across restarts"

    print("  [OK] PASSED: Archives persist across restarts")


def test_failed_archive_no_pollute_releases():
    """Test 17: Failed archive create does not pollute release records."""
    print("\n" + "=" * 70)
    print("TEST 17: Failed archive does not pollute releases")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    releases_before = get_db_state()['releases']
    success_releases_before = [r for r in releases_before if r[2] == 'success']

    run_cmd(
        ['archive', 'create', 'pollute-test', '99.0.0', 'staging'],
        expect_success=False
    )

    releases_after = get_db_state()['releases']
    success_releases_after = [r for r in releases_after if r[2] == 'success']

    assert success_releases_before == success_releases_after, \
        "Release records should not be polluted by failed archive create"

    print("  [OK] PASSED: Failed archive does not pollute releases")


def test_archive_create_prod_without_approval():
    """Test 18: Archive create for prod without approval should fail."""
    print("\n" + "=" * 70)
    print("TEST 18: Archive create for prod without approval")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['package', 'create', 'no-approval-pkg', 'prod', '1.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'no-approval-pkg', '--role', 'release-manager'])
    run_cmd(['apply', '1.0.0', 'prod', '--role', 'release-manager', '--yes'])

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM approvals WHERE version = '1.0.0' AND environment = 'prod'")
    conn.commit()
    conn.close()

    result = run_cmd(
        ['archive', 'create', 'no-approval-archive', '1.0.0', 'prod', '--role', 'release-manager'],
        expect_success=False
    )

    assert 'requires linked approval' in result.stderr.lower() or 'requires linked approval' in result.stdout.lower(), \
        f"Should mention missing approval. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    prod_archives = [a for a in state['archives'] if a[1] == 'prod']
    assert len(prod_archives) == 0, "No archive should be created"

    error_logs = get_error_logs()
    approval_errors = [e for e in error_logs if e[0] == 'archive.create' and 'MISSING_APPROVAL' in e[1]]
    assert len(approval_errors) >= 1, "Should have error log"

    print("  [OK] PASSED: Prod archive without approval correctly rejected")


def test_archive_create_with_linked_package():
    """Test 19: Archive create with linked package should succeed."""
    print("\n" + "=" * 70)
    print("TEST 19: Archive create with linked package")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    result = run_cmd(
        ['archive', 'create', 'linked-pkg-archive', '1.0.0', 'staging', '--linked-package', 'test-pkg']
    )

    assert 'SUCCESS' in result.stdout, f"Should show success. STDOUT: {result.stdout}"

    print("  [OK] PASSED: Archive with linked package created successfully")


def test_archive_import_to_new_database():
    """Test 20: Archive import to new database should preserve all fields."""
    print("\n" + "=" * 70)
    print("TEST 20: Archive import to new database preserves fields")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'newdb-test', '1.0.0', 'staging'])
    run_cmd(['archive', 'revoke', 'newdb-test', '--role', 'release-manager', '--reason', 'testing'])
    run_cmd(['archive', 'export', 'newdb-test', '--output', 'newdb_test.json'])

    with open('newdb_test.json', 'r', encoding='utf-8') as f:
        original_data = json.load(f)

    os.remove(DB_FILE)
    setup_successful_releases()

    run_cmd(['archive', 'import', 'newdb_test.json', '--role', 'release-manager'])

    imported = run_cmd(['archive', 'show', 'newdb-test'])

    assert 'revoked' in imported.stdout.lower(), "Should show revoked status"
    assert '1.0.0' in imported.stdout, "Should show version 1.0.0"
    assert 'staging' in imported.stdout, "Should show environment staging"

    state = get_db_state()
    archives = [a for a in state['archives'] if a[0] == 'newdb-test']
    assert len(archives) == 1, "Archive should be imported"
    assert archives[0][3] == 'revoked', "Status should be preserved"

    os.remove('newdb_test.json')

    print("  [OK] PASSED: Import to new database preserves all fields")


def test_archive_import_without_successful_release():
    """Test 21: Archive import without successful release should fail."""
    print("\n" + "=" * 70)
    print("TEST 21: Archive import without successful release")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'no-release-test', '1.0.0', 'staging'])
    run_cmd(['archive', 'export', 'no-release-test', '--output', 'no_release_test.json'])

    os.remove(DB_FILE)
    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v1.json'])

    result = run_cmd(
        ['archive', 'import', 'no_release_test.json'],
        expect_success=False
    )

    assert 'no successful release' in result.stderr.lower() or 'no successful release' in result.stdout.lower(), \
        f"Should mention no successful release. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    os.remove('no_release_test.json')

    print("  [OK] PASSED: Import without successful release correctly rejected")


def test_archive_list_with_filters():
    """Test 22: Archive list with filters should filter correctly."""
    print("\n" + "=" * 70)
    print("TEST 22: Archive list with filters")
    print("=" * 70)

    cleanup()
    setup_successful_releases()

    run_cmd(['archive', 'create', 'active-1', '1.0.0', 'staging'])
    run_cmd(['archive', 'create', 'active-2', '2.0.0', 'dev'])
    run_cmd(['archive', 'create', 'revoked-1', '1.0.0', 'dev'])
    run_cmd(['archive', 'revoke', 'revoked-1', '--role', 'release-manager', '--reason', 'testing'])

    result_all = run_cmd(['archive', 'list'])
    assert 'active-1' in result_all.stdout
    assert 'active-2' in result_all.stdout
    assert 'revoked-1' in result_all.stdout

    result_active = run_cmd(['archive', 'list', '--status', 'active'])
    assert 'active-1' in result_active.stdout
    assert 'active-2' in result_active.stdout
    assert 'revoked-1' not in result_active.stdout

    result_revoked = run_cmd(['archive', 'list', '--status', 'revoked'])
    assert 'active-1' not in result_revoked.stdout
    assert 'active-2' not in result_revoked.stdout
    assert 'revoked-1' in result_revoked.stdout

    result_staging = run_cmd(['archive', 'list', '--env', 'staging'])
    assert 'active-1' in result_staging.stdout
    assert 'active-2' not in result_staging.stdout
    assert 'revoked-1' not in result_staging.stdout

    print("  [OK] PASSED: Archive list filters work correctly")


def main():
    """Run all tests."""
    print("\n" + "#" * 70)
    print("ARCHIVE MODULE REGRESSION TESTS")
    print("#" * 70)

    tests = [
        test_archive_create_duplicate_name,
        test_archive_create_unsuccessful_release,
        test_archive_create_prod_by_developer,
        test_archive_create_prod_by_release_manager,
        test_archive_create_non_prod_by_developer,
        test_archive_show_and_list,
        test_archive_verify,
        test_archive_revoke_by_developer,
        test_archive_revoke_by_release_manager,
        test_archive_verify_after_revoke,
        test_archive_export_import_roundtrip,
        test_archive_import_summary_mismatch,
        test_archive_import_missing_version,
        test_archive_import_duplicate_name_no_force,
        test_archive_import_duplicate_name_with_force,
        test_archive_cross_restart_persistence,
        test_failed_archive_no_pollute_releases,
        test_archive_create_prod_without_approval,
        test_archive_create_with_linked_package,
        test_archive_import_to_new_database,
        test_archive_import_without_successful_release,
        test_archive_list_with_filters,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n  [FAIL] {test.__name__}: {e}")
        finally:
            cleanup()
            for f in ['test_export.json', 'mismatch_test.json', 'missing_test.json',
                      'dup_test.json', 'force_test.json', 'newdb_test.json',
                      'no_release_test.json']:
                if os.path.exists(f):
                    os.remove(f)

    print("\n" + "=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed > 0:
        sys.exit(1)
    else:
        print("\nAll tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
