#!/usr/bin/env python
"""Regression tests for change package signoff module.

Tests:
1. Package create with missing versions - should fail
2. Package create with duplicate name - should fail
3. Package create for prod by developer - should fail
4. Package create for prod by release-manager - should succeed
5. Package create for non-prod by developer - should succeed
6. Package show and list - should display correctly
7. Package verify - should verify integrity
8. Package sign by developer - should fail
9. Package sign by release-manager - should succeed
10. Package sign already signed - should fail
11. Package revoke by developer - should fail
12. Package revoke by release-manager - should succeed
13. Package revoke not signed - should fail
14. Package export/import roundtrip - should preserve integrity
15. Package import with summary mismatch - should fail
16. Package import with missing version - should fail
17. Apply to prod without signed package - should fail
18. Apply to prod with signed package - should succeed
19. Apply to prod after signoff revoked - should fail
20. Batch apply to prod without signed package - should fail
21. Batch apply to prod with signed package - should succeed
22. Cross-restart persistence - packages should survive restart
23. Failed apply does not advance current_version or write success release
"""

import os
import sys
import json
import subprocess
import sqlite3
import time

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

    cursor.execute("SELECT package_name, target_environment, signoff_status FROM change_packages ORDER BY id")
    packages = [(r['package_name'], r['target_environment'], r['signoff_status']) for r in cursor.fetchall()]

    cursor.execute("SELECT id, action, status, environment, version, error_reason, details FROM audit_logs ORDER BY id DESC")
    audit_logs = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT id, command, error_code, error_message, environment, version FROM error_logs ORDER BY id DESC")
    error_logs = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        'environments': envs,
        'locks': locks,
        'releases': releases,
        'packages': packages,
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


def test_package_create_missing_version():
    """Test 1: Creating package with missing version should fail."""
    print("\n" + "=" * 70)
    print("TEST 1: Package create with missing version")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    result = run_cmd(
        ['package', 'create', 'test-pkg', 'staging', '1.0.0', '99.99.99'],
        expect_success=False
    )

    assert 'not found' in result.stderr.lower() or 'not found' in result.stdout.lower(), \
        f"Should mention version not found. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['packages']) == 0, "No package should be created"

    audit_logs = get_audit_logs()
    fail_logs = [l for l in audit_logs if l[0] == 'package.create' and l[1] == 'failed']
    assert len(fail_logs) >= 1, "Should have failed audit log"

    error_logs = get_error_logs()
    pkg_errors = [e for e in error_logs if e[0] == 'package.create' and 'VERSION_NOT_FOUND' in e[1]]
    assert len(pkg_errors) >= 1, "Should have error log"

    print("  [OK] PASSED: Missing version correctly rejected")


def test_package_create_duplicate_name():
    """Test 2: Creating package with duplicate name should fail."""
    print("\n" + "=" * 70)
    print("TEST 2: Package create with duplicate name")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'dup-pkg', 'staging', '1.0.0'])
    print("  First package created successfully")

    result = run_cmd(
        ['package', 'create', 'dup-pkg', 'staging', '2.0.0'],
        expect_success=False
    )

    assert 'already exists' in result.stderr.lower() or 'already exists' in result.stdout.lower(), \
        f"Should mention duplicate name. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['packages']) == 1, "Should have only one package"

    print("  [OK] PASSED: Duplicate name correctly rejected")


def test_package_create_prod_developer():
    """Test 3: Developer creating prod package should fail."""
    print("\n" + "=" * 70)
    print("TEST 3: Developer cannot create prod package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    result = run_cmd(
        ['package', 'create', 'prod-pkg', 'prod', '1.0.0', '--role', 'developer'],
        expect_success=False
    )

    assert 'Permission denied' in result.stderr or 'Permission denied' in result.stdout, \
        f"Should mention permission denied. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['packages']) == 0, "No package should be created"

    print("  [OK] PASSED: Developer cannot create prod package")


def test_package_create_prod_release_manager():
    """Test 4: Release-manager creating prod package should succeed."""
    print("\n" + "=" * 70)
    print("TEST 4: Release-manager can create prod package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    result = run_cmd(
        ['package', 'create', 'prod-pkg', 'prod', '1.0.0', '2.0.0', '--role', 'release-manager']
    )

    assert 'SUCCESS' in result.stdout, f"Should show success. STDOUT: {result.stdout}"

    state = get_db_state()
    assert len(state['packages']) == 1, "Package should be created"
    assert state['packages'][0] == ('prod-pkg', 'prod', 'pending'), \
        f"Package should be pending for prod. Got: {state['packages'][0]}"

    print("  [OK] PASSED: Release-manager can create prod package")


def test_package_create_non_prod_developer():
    """Test 5: Developer creating non-prod package should succeed."""
    print("\n" + "=" * 70)
    print("TEST 5: Developer can create non-prod package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    result = run_cmd(
        ['package', 'create', 'dev-pkg', 'staging', '1.0.0', '2.0.0', '--role', 'developer']
    )

    assert 'SUCCESS' in result.stdout, f"Should show success. STDOUT: {result.stdout}"

    state = get_db_state()
    assert len(state['packages']) == 1, "Package should be created"
    assert state['packages'][0] == ('dev-pkg', 'staging', 'pending'), \
        f"Package should be pending for staging. Got: {state['packages'][0]}"

    print("  [OK] PASSED: Developer can create non-prod package")


def test_package_show_and_list():
    """Test 6: Package show and list should display correctly."""
    print("\n" + "=" * 70)
    print("TEST 6: Package show and list")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'show-test', 'staging', '1.0.0', '2.0.0'])
    print("  Package created")

    result_show = run_cmd(['package', 'show', 'show-test'])
    assert 'PACKAGE: show-test' in result_show.stdout, \
        f"Show should display package name. STDOUT: {result_show.stdout}"
    assert 'Target Env:     staging' in result_show.stdout, \
        f"Show should display target env. STDOUT: {result_show.stdout}"
    assert '1.0.0' in result_show.stdout and '2.0.0' in result_show.stdout, \
        f"Show should display versions. STDOUT: {result_show.stdout}"
    assert 'VERIFICATION: OK' in result_show.stdout, \
        f"Show should include verification. STDOUT: {result_show.stdout}"
    print("  Package show works correctly")

    result_list = run_cmd(['package', 'list'])
    assert 'show-test' in result_list.stdout, \
        f"List should include package. STDOUT: {result_list.stdout}"
    assert 'staging' in result_list.stdout, \
        f"List should include environment. STDOUT: {result_list.stdout}"
    assert 'pending' in result_list.stdout, \
        f"List should include status. STDOUT: {result_list.stdout}"
    print("  Package list works correctly")

    result_list_env = run_cmd(['package', 'list', '--env', 'staging'])
    assert 'show-test' in result_list_env.stdout, \
        f"List with env filter should include package. STDOUT: {result_list_env.stdout}"

    result_list_prod = run_cmd(['package', 'list', '--env', 'prod'])
    assert 'show-test' not in result_list_prod.stdout, \
        f"List with prod filter should not include staging package. STDOUT: {result_list_prod.stdout}"
    print("  Package list with env filter works correctly")

    print("  [OK] PASSED: Package show and list work correctly")


def test_package_verify():
    """Test 7: Package verify should check integrity."""
    print("\n" + "=" * 70)
    print("TEST 7: Package verify integrity")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'verify-test', 'staging', '1.0.0', '2.0.0'])
    print("  Package created")

    result = run_cmd(['package', 'verify', 'verify-test'])
    assert 'VALID' in result.stdout, f"Should show valid. STDOUT: {result.stdout}"
    assert 'OK' in result.stdout, f"Should show OK for versions. STDOUT: {result.stdout}"
    print("  Verify shows valid for intact package")

    _modify_config_in_db('2.0.0', 'database.pool_size', 999)
    print("  Config 2.0.0 modified directly in SQLite")

    result2 = run_cmd(['package', 'verify', 'verify-test'], expect_success=False)
    assert 'FAILED' in result2.stdout or 'FAILED' in result2.stderr, \
        f"Should show failed. STDOUT: {result2.stdout}, STDERR: {result2.stderr}"
    assert 'MISMATCH' in result2.stdout or 'MISMATCH' in result2.stderr, \
        f"Should show hash mismatch. STDOUT: {result2.stdout}, STDERR: {result2.stderr}"
    print("  Verify detects modified config correctly")

    print("  [OK] PASSED: Package verify works correctly")


def test_package_sign_developer():
    """Test 8: Developer signing package should fail."""
    print("\n" + "=" * 70)
    print("TEST 8: Developer cannot sign package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'sign-test', 'prod', '1.0.0', '--role', 'release-manager'])
    print("  Package created")

    result = run_cmd(
        ['package', 'sign', 'sign-test', '--role', 'developer'],
        expect_success=False
    )

    assert 'Permission denied' in result.stderr or 'Permission denied' in result.stdout, \
        f"Should mention permission denied. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert state['packages'][0][2] == 'pending', \
        f"Package should remain pending. Got: {state['packages'][0]}"

    print("  [OK] PASSED: Developer cannot sign package")


def test_package_sign_release_manager():
    """Test 9: Release-manager signing package should succeed."""
    print("\n" + "=" * 70)
    print("TEST 9: Release-manager can sign package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'sign-test', 'prod', '1.0.0', '--role', 'release-manager'])
    print("  Package created")

    result = run_cmd(
        ['package', 'sign', 'sign-test', '--role', 'release-manager', '--notes', 'Test signoff']
    )

    assert 'SUCCESS' in result.stdout and 'signed' in result.stdout.lower(), \
        f"Should show signed successfully. STDOUT: {result.stdout}"

    state = get_db_state()
    assert state['packages'][0][2] == 'signed', \
        f"Package should be signed. Got: {state['packages'][0]}"

    audit_logs = get_audit_logs()
    sign_logs = [l for l in audit_logs if l[0] == 'package.sign' and l[1] == 'success']
    assert len(sign_logs) >= 1, "Should have success audit log for sign"

    print("  [OK] PASSED: Release-manager can sign package")


def test_package_sign_already_signed():
    """Test 10: Signing already signed package should fail."""
    print("\n" + "=" * 70)
    print("TEST 10: Cannot sign already signed package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'dup-sign', 'prod', '1.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'dup-sign', '--role', 'release-manager'])
    print("  Package signed")

    result = run_cmd(
        ['package', 'sign', 'dup-sign', '--role', 'release-manager'],
        expect_success=False
    )

    assert 'already signed' in result.stderr.lower() or 'already signed' in result.stdout.lower(), \
        f"Should mention already signed. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    print("  [OK] PASSED: Cannot sign already signed package")


def test_package_revoke_developer():
    """Test 11: Developer revoking signoff should fail."""
    print("\n" + "=" * 70)
    print("TEST 11: Developer cannot revoke signoff")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'revoke-test', 'prod', '1.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'revoke-test', '--role', 'release-manager'])
    print("  Package signed")

    result = run_cmd(
        ['package', 'revoke', 'revoke-test', '--role', 'developer'],
        expect_success=False
    )

    assert 'Permission denied' in result.stderr or 'Permission denied' in result.stdout, \
        f"Should mention permission denied. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert state['packages'][0][2] == 'signed', \
        f"Package should remain signed. Got: {state['packages'][0]}"

    print("  [OK] PASSED: Developer cannot revoke signoff")


def test_package_revoke_release_manager():
    """Test 12: Release-manager revoking signoff should succeed."""
    print("\n" + "=" * 70)
    print("TEST 12: Release-manager can revoke signoff")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'revoke-test', 'prod', '1.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'revoke-test', '--role', 'release-manager'])
    print("  Package signed")

    result = run_cmd(
        ['package', 'revoke', 'revoke-test', '--role', 'release-manager', '--reason', 'Issue found']
    )

    assert 'SUCCESS' in result.stdout and 'revoked' in result.stdout.lower(), \
        f"Should show revoked successfully. STDOUT: {result.stdout}"

    state = get_db_state()
    assert state['packages'][0][2] == 'pending', \
        f"Package should be pending again. Got: {state['packages'][0]}"

    audit_logs = get_audit_logs()
    revoke_logs = [l for l in audit_logs if l[0] == 'package.revoke' and l[1] == 'success']
    assert len(revoke_logs) >= 1, "Should have success audit log for revoke"

    print("  [OK] PASSED: Release-manager can revoke signoff")


def test_package_revoke_not_signed():
    """Test 13: Revoking not signed package should fail."""
    print("\n" + "=" * 70)
    print("TEST 13: Cannot revoke not signed package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'not-signed', 'prod', '1.0.0', '--role', 'release-manager'])
    print("  Package created but not signed")

    result = run_cmd(
        ['package', 'revoke', 'not-signed', '--role', 'release-manager'],
        expect_success=False
    )

    assert 'not signed' in result.stderr.lower() or 'not signed' in result.stdout.lower(), \
        f"Should mention not signed. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    print("  [OK] PASSED: Cannot revoke not signed package")


def test_package_export_import_roundtrip():
    """Test 14: Package export/import roundtrip preserves integrity."""
    print("\n" + "=" * 70)
    print("TEST 14: Package export/import roundtrip")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'export-test', 'staging', '1.0.0', '2.0.0'])
    print("  Package created")

    export_file = 'test_package_export.json'
    if os.path.exists(export_file):
        os.remove(export_file)

    run_cmd(['package', 'export', 'export-test', '--output', export_file])
    assert os.path.exists(export_file), "Export file should exist"
    print("  Package exported")

    with open(export_file, 'r') as f:
        export_data = json.load(f)
    assert export_data['package_name'] == 'export-test'
    assert export_data['target_environment'] == 'staging'
    assert '1.0.0' in export_data['versions'] and '2.0.0' in export_data['versions']
    assert 'summary_hash' in export_data
    print("  Export data is valid")

    cleanup()
    init_db_with_configs()
    print("  Database reinitialized (fresh state)")

    result = run_cmd(['package', 'import', export_file])
    assert 'SUCCESS' in result.stdout and 'imported' in result.stdout.lower(), \
        f"Should show imported successfully. STDOUT: {result.stdout}"

    state = get_db_state()
    assert len(state['packages']) == 1, "Package should be imported"
    assert state['packages'][0] == ('export-test', 'staging', 'pending'), \
        f"Imported package should match. Got: {state['packages'][0]}"

    result_verify = run_cmd(['package', 'verify', 'export-test'])
    assert 'VALID' in result_verify.stdout, "Imported package should verify as valid"
    print("  Imported package verified successfully")

    if os.path.exists(export_file):
        os.remove(export_file)

    print("  [OK] PASSED: Export/import roundtrip works correctly")


def test_package_import_summary_mismatch():
    """Test 15: Import with summary mismatch should fail."""
    print("\n" + "=" * 70)
    print("TEST 15: Import with summary mismatch")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'mismatch-test', 'staging', '1.0.0', '2.0.0'])
    export_file = 'test_mismatch.json'
    run_cmd(['package', 'export', 'mismatch-test', '--output', export_file])
    print("  Package exported")

    with open(export_file, 'r') as f:
        data = json.load(f)
    data['summary_hash'] = 'a' * 64
    with open(export_file, 'w') as f:
        json.dump(data, f)
    print("  Summary hash tampered in export file")

    cleanup()
    init_db_with_configs()

    result = run_cmd(['package', 'import', export_file], expect_success=False)
    assert 'mismatch' in result.stderr.lower() or 'mismatch' in result.stdout.lower(), \
        f"Should mention summary mismatch. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['packages']) == 0, "No package should be imported"

    error_logs = get_error_logs()
    mismatch_errors = [e for e in error_logs if e[1] == 'PACKAGE_SUMMARY_MISMATCH']
    assert len(mismatch_errors) >= 1, "Should have summary mismatch error log"

    if os.path.exists(export_file):
        os.remove(export_file)

    print("  [OK] PASSED: Summary mismatch correctly rejected")


def test_package_import_missing_version():
    """Test 16: Import with missing version should fail."""
    print("\n" + "=" * 70)
    print("TEST 16: Import with missing version")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'missing-ver', 'staging', '1.0.0'])
    export_file = 'test_missing_ver.json'
    run_cmd(['package', 'export', 'missing-ver', '--output', export_file])
    print("  Package exported")

    cleanup()
    run_cmd(['init'])
    run_cmd(['import', 'config_pipeline/examples/config_v2.json'])
    print("  Database initialized WITHOUT config v1")

    result = run_cmd(['package', 'import', export_file], expect_success=False)
    assert 'not found' in result.stderr.lower() or 'not found' in result.stdout.lower(), \
        f"Should mention version not found. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['packages']) == 0, "No package should be imported"

    error_logs = get_error_logs()
    missing_errors = [e for e in error_logs if e[1] == 'PACKAGE_VERSION_NOT_FOUND']
    assert len(missing_errors) >= 1, "Should have version not found error log"

    if os.path.exists(export_file):
        os.remove(export_file)

    print("  [OK] PASSED: Missing version correctly rejected")


def test_apply_prod_without_signed_package():
    """Test 17: Apply to prod without signed package should fail."""
    print("\n" + "=" * 70)
    print("TEST 17: Apply to prod without signed package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod', '--notes', 'Test'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])
    print("  Setup: version approved but NOT in signed package")

    state_before = get_db_state()
    assert state_before['environments']['prod'] is None, "Prod should be at None"

    result = run_cmd(
        ['apply', '1.0.0', 'prod', '--yes', '--role', 'release-manager'],
        expect_success=False
    )

    assert 'must be in a signed package' in result.stderr.lower() or \
           'must be in a signed package' in result.stdout.lower(), \
        f"Should mention package signoff required. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state_after = get_db_state()
    assert state_after['environments']['prod'] is None, \
        f"Prod current_version should NOT advance. Got: {state_after['environments']['prod']}"

    prod_releases = [(v, e, s) for v, e, s in state_after['releases']
                     if e == 'prod' and v == '1.0.0']
    success_releases = [r for r in prod_releases if r[2] == 'success']
    assert len(success_releases) == 0, \
        f"Should NOT have success release. Releases: {prod_releases}"

    audit_logs = get_audit_logs()
    apply_fails = [l for l in audit_logs
                   if l[0] == 'apply' and l[1] == 'failed' and 'signed package' in (l[2] or '')]
    assert len(apply_fails) >= 1, "Should have failed audit log"

    print("  [OK] PASSED: Apply to prod without signed package correctly blocked")


def test_apply_prod_with_signed_package():
    """Test 18: Apply to prod with signed package should succeed."""
    print("\n" + "=" * 70)
    print("TEST 18: Apply to prod with signed package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'release-001', 'prod', '1.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'release-001', '--role', 'release-manager'])
    print("  Package created and signed")

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])
    print("  Setup: staging applied, approved")

    result = run_cmd(
        ['apply', '1.0.0', 'prod', '--yes', '--role', 'release-manager']
    )

    assert 'SUCCESS' in result.stdout and 'applied to prod' in result.stdout.lower(), \
        f"Should show success. STDOUT: {result.stdout}"

    state_after = get_db_state()
    assert state_after['environments']['prod'] == '1.0.0', \
        f"Prod should be at 1.0.0. Got: {state_after['environments']['prod']}"

    prod_releases = [(v, e, s) for v, e, s in state_after['releases']
                     if e == 'prod' and v == '1.0.0']
    success_releases = [r for r in prod_releases if r[2] == 'success']
    assert len(success_releases) >= 1, \
        f"Should have success release. Releases: {prod_releases}"

    print("  [OK] PASSED: Apply to prod with signed package succeeds")


def test_apply_prod_after_signoff_revoked():
    """Test 19: Apply to prod after signoff revoked should fail."""
    print("\n" + "=" * 70)
    print("TEST 19: Apply to prod after signoff revoked")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'revoked-pkg', 'prod', '2.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'revoked-pkg', '--role', 'release-manager'])
    run_cmd(['package', 'revoke', 'revoked-pkg', '--role', 'release-manager', '--reason', 'Issue'])
    print("  Package signed then revoked")

    run_cmd(['apply', '2.0.0', 'staging', '--yes'])
    run_cmd(['pending', '2.0.0', 'prod'])
    run_cmd(['approve', '2.0.0', 'prod', '--role', 'release-manager'])
    print("  Setup: staging applied, approved")

    state_before = get_db_state()
    assert state_before['environments']['prod'] is None

    result = run_cmd(
        ['apply', '2.0.0', 'prod', '--yes', '--role', 'release-manager'],
        expect_success=False
    )

    assert 'must be in a signed package' in result.stderr.lower() or \
           'must be in a signed package' in result.stdout.lower(), \
        f"Should mention package signoff required. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state_after = get_db_state()
    assert state_after['environments']['prod'] is None, \
        f"Prod current_version should NOT advance. Got: {state_after['environments']['prod']}"

    prod_releases = [(v, e, s) for v, e, s in state_after['releases']
                     if e == 'prod' and v == '2.0.0']
    success_releases = [r for r in prod_releases if r[2] == 'success']
    assert len(success_releases) == 0, \
        f"Should NOT have success release. Releases: {prod_releases}"

    print("  [OK] PASSED: Apply to prod after signoff revoked correctly blocked")


def test_batch_apply_prod_without_signed_package():
    """Test 20: Batch apply to prod without signed package should fail."""
    print("\n" + "=" * 70)
    print("TEST 20: Batch apply to prod without signed package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])
    print("  Setup: approved but not in signed package")

    state_before = get_db_state()
    assert state_before['environments']['prod'] is None

    result = run_cmd(
        ['batch', 'apply', 'prod:1.0.0', '--yes', '--role', 'release-manager'],
        expect_success=False
    )

    assert 'must be in a signed package' in result.stderr.lower() or \
           'must be in a signed package' in result.stdout.lower(), \
        f"Should mention package signoff required. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    assert 'FAILED' in result.stdout or '[FAIL]' in result.stdout, \
        f"Should show step failed. STDOUT: {result.stdout}"

    state_after = get_db_state()
    assert state_after['environments']['prod'] is None, \
        f"Prod current_version should NOT advance. Got: {state_after['environments']['prod']}"

    prod_releases = [(v, e, s) for v, e, s in state_after['releases']
                     if e == 'prod' and v == '1.0.0']
    success_releases = [r for r in prod_releases if r[2] == 'success']
    assert len(success_releases) == 0, \
        f"Should NOT have success release. Releases: {prod_releases}"

    print("  [OK] PASSED: Batch apply to prod without signed package correctly blocked")


def test_batch_apply_prod_with_signed_package():
    """Test 21: Batch apply to prod with signed package should succeed."""
    print("\n" + "=" * 70)
    print("TEST 21: Batch apply to prod with signed package")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'batch-release', 'prod', '1.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'batch-release', '--role', 'release-manager'])
    print("  Package created and signed")

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])
    print("  Setup: staging applied, approved")

    result = run_cmd(
        ['batch', 'apply', 'prod:1.0.0', '--yes', '--role', 'release-manager']
    )

    assert 'BATCH COMPLETED SUCCESSFULLY' in result.stdout, \
        f"Should show batch success. STDOUT: {result.stdout}"

    state_after = get_db_state()
    assert state_after['environments']['staging'] == '1.0.0', \
        f"Staging should be at 1.0.0. Got: {state_after['environments']['staging']}"
    assert state_after['environments']['prod'] == '1.0.0', \
        f"Prod should be at 1.0.0. Got: {state_after['environments']['prod']}"

    print("  [OK] PASSED: Batch apply to prod with signed package succeeds")


def test_cross_restart_persistence():
    """Test 22: Packages should persist across restarts."""
    print("\n" + "=" * 70)
    print("TEST 22: Cross-restart persistence")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['package', 'create', 'persist-test', 'staging', '1.0.0', '2.0.0'])
    run_cmd(['package', 'create', 'persist-prod', 'prod', '1.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'persist-prod', '--role', 'release-manager'])
    print("  Packages created")

    state_before = get_db_state()
    packages_before = state_before['packages']
    print(f"  Packages before: {packages_before}")

    list_before = run_cmd(['package', 'list'])

    print("  Simulating restart by re-running commands...")

    state_after = get_db_state()
    packages_after = state_after['packages']
    print(f"  Packages after: {packages_after}")

    assert packages_before == packages_after, \
        f"Packages should be identical. Before: {packages_before}, After: {packages_after}"

    list_after = run_cmd(['package', 'list'])
    assert list_before.stdout == list_after.stdout, \
        "Package list output should be identical"

    run_cmd(['package', 'show', 'persist-test'])
    run_cmd(['package', 'show', 'persist-prod'])
    print("  Packages still accessible after 'restart'")

    print("  [OK] PASSED: Cross-restart persistence works correctly")


def test_failed_apply_no_side_effects():
    """Test 23: Failed apply does not advance current_version or write success release."""
    print("\n" + "=" * 70)
    print("TEST 23: Failed apply has no side effects")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])
    print("  Setup: approved but no signed package")

    state_before = get_db_state()
    print(f"  Before: prod version = {state_before['environments']['prod']}")
    print(f"  Before: releases = {len(state_before['releases'])}")

    prod_releases_before = [(v, e, s) for v, e, s in state_before['releases']
                            if e == 'prod' and v == '1.0.0']

    run_cmd(
        ['apply', '1.0.0', 'prod', '--yes', '--role', 'release-manager'],
        expect_success=False
    )
    print("  Apply failed (expected)")

    state_after = get_db_state()
    print(f"  After: prod version = {state_after['environments']['prod']}")
    print(f"  After: releases = {len(state_after['releases'])}")

    assert state_after['environments']['prod'] == state_before['environments']['prod'], \
        f"current_version should NOT change. Before: {state_before['environments']['prod']}, After: {state_after['environments']['prod']}"

    prod_releases_after = [(v, e, s) for v, e, s in state_after['releases']
                           if e == 'prod' and v == '1.0.0']
    success_releases_after = [r for r in prod_releases_after if r[2] == 'success']
    assert len(success_releases_after) == len([r for r in prod_releases_before if r[2] == 'success']), \
        f"Success release count should NOT change. Before: {prod_releases_before}, After: {prod_releases_after}"

    audit_fails_after = [a for a in state_after['audit_logs']
                         if a['action'] == 'apply' and a['status'] == 'failed'
                         and a.get('version') == '1.0.0' and a.get('environment') == 'prod']
    assert len(audit_fails_after) >= 1, "Should have failed audit log"

    error_logs_after = [e for e in state_after['error_logs']
                        if e['command'] == 'apply' and e.get('version') == '1.0.0'
                        and e.get('environment') == 'prod']
    assert len(error_logs_after) >= 1, "Should have error log"

    print("  [OK] PASSED: Failed apply correctly has no side effects on current_version or success releases")


def main():
    print("=" * 70)
    print("CHANGE PACKAGE SIGNOFF REGRESSION TESTS")
    print("=" * 70)

    try:
        test_package_create_missing_version()
        test_package_create_duplicate_name()
        test_package_create_prod_developer()
        test_package_create_prod_release_manager()
        test_package_create_non_prod_developer()
        test_package_show_and_list()
        test_package_verify()
        test_package_sign_developer()
        test_package_sign_release_manager()
        test_package_sign_already_signed()
        test_package_revoke_developer()
        test_package_revoke_release_manager()
        test_package_revoke_not_signed()
        test_package_export_import_roundtrip()
        test_package_import_summary_mismatch()
        test_package_import_missing_version()
        test_apply_prod_without_signed_package()
        test_apply_prod_with_signed_package()
        test_apply_prod_after_signoff_revoked()
        test_batch_apply_prod_without_signed_package()
        test_batch_apply_prod_with_signed_package()
        test_cross_restart_persistence()
        test_failed_apply_no_side_effects()

        print("\n" + "=" * 70)
        print("ALL TESTS PASSED!")
        print("=" * 70)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
