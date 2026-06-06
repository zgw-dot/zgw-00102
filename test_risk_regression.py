#!/usr/bin/env python
"""Regression tests for risk assessment module.

Tests:
1. Risk scan for dev environment by developer - should succeed
2. Risk scan for prod environment by developer - should fail (permission denied)
3. Risk scan for staging environment by developer - should succeed
4. Risk scan produces valid risk level and hash
5. Risk scan with blocking items produces critical level
6. Risk approve for prod by developer - should fail (permission denied)
7. Risk approve for prod by release-manager - should succeed
8. Risk revoke for prod by developer - should fail (permission denied)
9. Risk revoke for prod by release-manager - should succeed
10. Risk view and list - should display correctly
11. Risk verify for valid assessment - should pass
12. Risk verify for tampered assessment - should fail
13. Risk export/import roundtrip - should preserve integrity
14. Risk import with hash mismatch - should fail
15. Risk import with conflict without --force - should fail
16. Risk import with conflict with --force - should succeed
17. Apply with blocking items - should fail
18. Apply with high risk without approval - should fail
19. Apply with high risk with approval - should succeed
20. Apply after risk approval revoked - should fail
21. Cross-restart persistence - assessments should survive restart
22. Failed apply does not advance current_version or write success release
23. Risk scan with high-risk features produces higher score
24. Risk list with filters works correctly
"""

import os
import sys
import json
import subprocess
import sqlite3
import time
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
    run_cmd(['import', 'config_pipeline/examples/config_v4_high_risk.json'])


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

    cursor.execute("SELECT version, environment, risk_level, approval_status FROM risk_assessments ORDER BY id DESC")
    risks = [(r['version'], r['environment'], r['risk_level'], r['approval_status']) for r in cursor.fetchall()]

    cursor.execute("SELECT id, action, status, environment, version, error_reason, details FROM audit_logs ORDER BY id DESC")
    audit_logs = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT id, command, error_code, error_message, environment, version FROM error_logs ORDER BY id DESC")
    error_logs = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        'environments': envs,
        'locks': locks,
        'releases': releases,
        'risks': risks,
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


def get_risk_assessment(version, environment):
    """Get risk assessment from database."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM risk_assessments WHERE version = ? AND environment = ?", (version, environment))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def _modify_risk_in_db(version, environment, field, new_value):
    """Directly modify risk assessment in SQLite to cause verification failure."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE risk_assessments SET {field} = ? WHERE version = ? AND environment = ?",
        (new_value, version, environment)
    )
    conn.commit()
    conn.close()


def setup_high_risk_scenario():
    """Set up a scenario that produces high risk score without blocking items."""
    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['risk', 'scan', '4.0.0', 'staging', '--role', 'release-manager'])
    run_cmd(['risk', 'approve', '4.0.0', 'staging', '--role', 'release-manager', '--notes', 'Approve staging for high risk version'])
    run_cmd(['apply', '4.0.0', 'staging', '--yes'])
    run_cmd(['pending', '4.0.0', 'prod'])
    run_cmd(['approve', '4.0.0', 'prod', '--role', 'release-manager'])


def setup_blocking_scenario():
    """Set up a scenario with blocking items (e.g., locked environment)."""
    run_cmd(['lock', 'prod', '--reason', 'Maintenance', '--role', 'release-manager'])


def test_risk_scan_dev_developer():
    """Test 1: Developer scanning dev environment should succeed."""
    print("\n" + "=" * 70)
    print("TEST 1: Developer can scan dev environment")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    result = run_cmd(
        ['risk', 'scan', '1.0.0', 'dev', '--role', 'developer']
    )

    assert 'RISK ASSESSMENT' in result.stdout, f"Should show risk assessment. STDOUT: {result.stdout}"
    assert 'Risk Level:' in result.stdout, f"Should show risk level. STDOUT: {result.stdout}"
    assert 'Config Hash:' in result.stdout, f"Should show config hash. STDOUT: {result.stdout}"

    state = get_db_state()
    assert len(state['risks']) == 1, "Risk assessment should be created"

    print("  [OK] PASSED: Developer can scan dev environment")


def test_risk_scan_prod_developer():
    """Test 2: Developer scanning prod environment should fail."""
    print("\n" + "=" * 70)
    print("TEST 2: Developer cannot scan prod environment")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    result = run_cmd(
        ['risk', 'scan', '1.0.0', 'prod', '--role', 'developer'],
        expect_success=False
    )

    assert 'Permission denied' in result.stderr or 'Permission denied' in result.stdout, \
        f"Should mention permission denied. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['risks']) == 0, "No risk assessment should be created"

    print("  [OK] PASSED: Developer cannot scan prod environment")


def test_risk_scan_staging_developer():
    """Test 3: Developer scanning staging environment should succeed."""
    print("\n" + "=" * 70)
    print("TEST 3: Developer can scan staging environment")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    result = run_cmd(
        ['risk', 'scan', '1.0.0', 'staging', '--role', 'developer']
    )

    assert 'RISK ASSESSMENT' in result.stdout, f"Should show risk assessment. STDOUT: {result.stdout}"

    state = get_db_state()
    assert len(state['risks']) == 1, "Risk assessment should be created"

    print("  [OK] PASSED: Developer can scan staging environment")


def test_risk_scan_produces_valid_data():
    """Test 4: Risk scan produces valid risk level and hash."""
    print("\n" + "=" * 70)
    print("TEST 4: Risk scan produces valid data")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'staging', '--role', 'developer'])

    risk = get_risk_assessment('1.0.0', 'staging')
    assert risk is not None, "Risk assessment should exist"
    assert risk['risk_level'] in ['none', 'low', 'medium', 'high', 'critical'], \
        f"Invalid risk level: {risk['risk_level']}"
    assert risk['risk_score'] >= 0, "Risk score should be non-negative"
    assert risk['config_hash'] is not None, "Config hash should exist"
    assert len(risk['config_hash']) == 64, "Config hash should be 64 chars (SHA-256)"
    assert risk['summary_hash'] is not None, "Summary hash should exist"
    assert len(risk['summary_hash']) == 64, "Summary hash should be 64 chars (SHA-256)"

    blocking_items = json.loads(risk['blocking_items'])
    assert isinstance(blocking_items, list), "Blocking items should be a list"

    print(f"  Risk level: {risk['risk_level']}, score: {risk['risk_score']}")
    print(f"  Config hash: {risk['config_hash'][:16]}...")
    print(f"  Summary hash: {risk['summary_hash'][:16]}...")

    print("  [OK] PASSED: Risk scan produces valid data")


def test_risk_scan_with_blocking_items():
    """Test 5: Risk scan with blocking items produces critical level."""
    print("\n" + "=" * 70)
    print("TEST 5: Risk scan with blocking items")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    setup_blocking_scenario()
    print("  Prod environment locked")

    result = run_cmd(
        ['risk', 'scan', '1.0.0', 'prod', '--role', 'release-manager']
    )

    assert 'CRITICAL' in result.stdout or 'critical' in result.stdout.lower(), \
        f"Should show critical risk level. STDOUT: {result.stdout}"
    assert 'blocking' in result.stdout.lower(), \
        f"Should mention blocking items. STDOUT: {result.stdout}"

    risk = get_risk_assessment('1.0.0', 'prod')
    assert risk['risk_level'] == 'critical', f"Risk level should be critical. Got: {risk['risk_level']}"

    blocking_items = json.loads(risk['blocking_items'])
    assert len(blocking_items) > 0, "Should have blocking items"
    print(f"  Blocking items: {blocking_items}")

    print("  [OK] PASSED: Risk scan with blocking items produces critical level")


def test_risk_approve_prod_developer():
    """Test 6: Developer approving prod risk should fail."""
    print("\n" + "=" * 70)
    print("TEST 6: Developer cannot approve prod risk")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'prod', '--role', 'release-manager'])
    print("  Risk assessment created")

    result = run_cmd(
        ['risk', 'approve', '1.0.0', 'prod', '--role', 'developer'],
        expect_success=False
    )

    assert 'Permission denied' in result.stderr or 'Permission denied' in result.stdout, \
        f"Should mention permission denied. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    risk = get_risk_assessment('1.0.0', 'prod')
    assert risk['approval_status'] in ['pending', 'requires_approval'], f"Approval status should remain pending/requires_approval. Got: {risk['approval_status']}"

    print("  [OK] PASSED: Developer cannot approve prod risk")


def test_risk_approve_prod_release_manager():
    """Test 7: Release-manager approving prod risk should succeed."""
    print("\n" + "=" * 70)
    print("TEST 7: Release-manager can approve prod risk")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'prod', '--role', 'release-manager'])
    print("  Risk assessment created")

    result = run_cmd(
        ['risk', 'approve', '1.0.0', 'prod', '--role', 'release-manager', '--notes', 'Approved for release']
    )

    assert 'approved' in result.stdout.lower(), f"Should show approved. STDOUT: {result.stdout}"
    assert 'SUCCESS' in result.stdout, f"Should show success. STDOUT: {result.stdout}"

    risk = get_risk_assessment('1.0.0', 'prod')
    assert risk['approval_status'] == 'approved', f"Approval status should be approved. Got: {risk['approval_status']}"
    assert risk['approved_by'] is not None, "Approved by should be set"
    assert risk['approved_at'] is not None, "Approved at should be set"
    assert 'Approved for release' in risk['approval_notes'], "Approval notes should be set"

    audit_logs = get_audit_logs()
    approve_logs = [l for l in audit_logs if l[0] == 'risk.approve' and l[1] == 'success']
    assert len(approve_logs) >= 1, "Should have success audit log for approve"

    print("  [OK] PASSED: Release-manager can approve prod risk")


def test_risk_revoke_prod_developer():
    """Test 8: Developer revoking prod risk should fail."""
    print("\n" + "=" * 70)
    print("TEST 8: Developer cannot revoke prod risk")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['risk', 'approve', '1.0.0', 'prod', '--role', 'release-manager'])
    print("  Risk assessment created and approved")

    result = run_cmd(
        ['risk', 'revoke', '1.0.0', 'prod', '--role', 'developer'],
        expect_success=False
    )

    assert 'Permission denied' in result.stderr or 'Permission denied' in result.stdout, \
        f"Should mention permission denied. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    risk = get_risk_assessment('1.0.0', 'prod')
    assert risk['approval_status'] == 'approved', f"Approval status should remain approved. Got: {risk['approval_status']}"

    print("  [OK] PASSED: Developer cannot revoke prod risk")


def test_risk_revoke_prod_release_manager():
    """Test 9: Release-manager revoking prod risk should succeed."""
    print("\n" + "=" * 70)
    print("TEST 9: Release-manager can revoke prod risk")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['risk', 'approve', '1.0.0', 'prod', '--role', 'release-manager'])
    print("  Risk assessment created and approved")

    result = run_cmd(
        ['risk', 'revoke', '1.0.0', 'prod', '--role', 'release-manager', '--reason', 'Issue found']
    )

    assert 'revoked' in result.stdout.lower(), f"Should show revoked. STDOUT: {result.stdout}"
    assert 'SUCCESS' in result.stdout, f"Should show success. STDOUT: {result.stdout}"

    risk = get_risk_assessment('1.0.0', 'prod')
    assert risk['approval_status'] == 'revoked', f"Approval status should be revoked. Got: {risk['approval_status']}"
    assert risk['revoked_by'] is not None, "Revoked by should be set"
    assert risk['revoked_at'] is not None, "Revoked at should be set"
    assert 'Issue found' in risk['revoke_reason'], "Revoke reason should be set"

    audit_logs = get_audit_logs()
    revoke_logs = [l for l in audit_logs if l[0] == 'risk.revoke' and l[1] == 'success']
    assert len(revoke_logs) >= 1, "Should have success audit log for revoke"

    print("  [OK] PASSED: Release-manager can revoke prod risk")


def test_risk_view_and_list():
    """Test 10: Risk view and list should display correctly."""
    print("\n" + "=" * 70)
    print("TEST 10: Risk view and list")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'dev', '--role', 'developer'])
    run_cmd(['risk', 'scan', '2.0.0', 'staging', '--role', 'developer'])
    print("  Risk assessments created")

    result_view = run_cmd(['risk', 'view', '1.0.0', 'dev'])
    assert 'Version:' in result_view.stdout, f"View should display version. STDOUT: {result_view.stdout}"
    assert 'Environment:' in result_view.stdout, f"View should display environment. STDOUT: {result_view.stdout}"
    assert 'Risk Level:' in result_view.stdout, f"View should display risk level. STDOUT: {result_view.stdout}"
    assert 'Config Hash:' in result_view.stdout, f"View should display config hash. STDOUT: {result_view.stdout}"
    assert 'Summary Hash:' in result_view.stdout, f"View should display summary hash. STDOUT: {result_view.stdout}"
    print("  Risk view works correctly")

    result_list = run_cmd(['risk', 'list'])
    assert '1.0.0' in result_list.stdout, f"List should include version 1.0.0. STDOUT: {result_list.stdout}"
    assert '2.0.0' in result_list.stdout, f"List should include version 2.0.0. STDOUT: {result_list.stdout}"
    assert 'dev' in result_list.stdout, f"List should include dev. STDOUT: {result_list.stdout}"
    assert 'staging' in result_list.stdout, f"List should include staging. STDOUT: {result_list.stdout}"
    print("  Risk list works correctly")

    result_list_env = run_cmd(['risk', 'list', '--env', 'dev'])
    assert '1.0.0' in result_list_env.stdout, f"List with env filter should include 1.0.0. STDOUT: {result_list_env.stdout}"
    assert '2.0.0' not in result_list_env.stdout, f"List with env filter should not include 2.0.0. STDOUT: {result_list_env.stdout}"
    print("  Risk list with env filter works correctly")

    print("  [OK] PASSED: Risk view and list work correctly")


def test_risk_verify_valid():
    """Test 11: Risk verify for valid assessment should pass."""
    print("\n" + "=" * 70)
    print("TEST 11: Risk verify valid assessment")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'staging', '--role', 'developer'])
    print("  Risk assessment created")

    result = run_cmd(['risk', 'verify', '1.0.0', 'staging'])
    assert 'VALID' in result.stdout, f"Should show valid. STDOUT: {result.stdout}"
    assert 'OK' in result.stdout, f"Should show OK. STDOUT: {result.stdout}"
    assert 'verified' in result.stdout.lower(), f"Should mention verified. STDOUT: {result.stdout}"

    print("  [OK] PASSED: Risk verify for valid assessment passes")


def test_risk_verify_tampered():
    """Test 12: Risk verify for tampered assessment should fail."""
    print("\n" + "=" * 70)
    print("TEST 12: Risk verify tampered assessment")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'staging', '--role', 'developer'])
    print("  Risk assessment created")

    _modify_risk_in_db('1.0.0', 'staging', 'risk_level', 'tampered')
    print("  Risk level tampered directly in SQLite")

    result = run_cmd(['risk', 'verify', '1.0.0', 'staging'], expect_success=False)
    assert 'FAILED' in result.stdout or 'FAILED' in result.stderr or 'failed' in result.stdout.lower(), \
        f"Should show failed. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    assert 'mismatch' in result.stdout.lower() or 'mismatch' in result.stderr.lower(), \
        f"Should show hash mismatch. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    error_logs = get_error_logs()
    verify_errors = [e for e in error_logs if e[1] == 'RISK_VERIFICATION_FAILED']
    assert len(verify_errors) >= 1, "Should have verification failed error log"

    print("  [OK] PASSED: Risk verify for tampered assessment fails")


def test_risk_export_import_roundtrip():
    """Test 13: Risk export/import roundtrip preserves integrity."""
    print("\n" + "=" * 70)
    print("TEST 13: Risk export/import roundtrip")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'staging', '--role', 'developer'])
    run_cmd(['risk', 'approve', '1.0.0', 'staging', '--role', 'release-manager', '--notes', 'Test approval'])
    print("  Risk assessment created and approved")

    export_file = 'test_risk_export.json'
    if os.path.exists(export_file):
        os.remove(export_file)

    run_cmd(['risk', 'export', '1.0.0', 'staging', '--output', export_file])
    assert os.path.exists(export_file), "Export file should exist"
    print("  Risk assessment exported")

    with open(export_file, 'r') as f:
        export_data = json.load(f)
    assert export_data['version'] == '1.0.0'
    assert export_data['environment'] == 'staging'
    assert export_data['approval_status'] == 'approved'
    assert 'summary_hash' in export_data
    assert 'config_hash' in export_data
    print("  Export data is valid")

    cleanup()
    init_db_with_configs()
    print("  Database reinitialized (fresh state)")

    result = run_cmd(['risk', 'import', export_file, '--role', 'release-manager'])
    assert 'SUCCESS' in result.stdout and 'imported' in result.stdout.lower(), \
        f"Should show imported successfully. STDOUT: {result.stdout}"

    state = get_db_state()
    assert len(state['risks']) == 1, "Risk assessment should be imported"
    assert state['risks'][0][:3] == ('1.0.0', 'staging', export_data['risk_level']), \
        f"Imported risk should match. Got: {state['risks'][0]}"

    result_verify = run_cmd(['risk', 'verify', '1.0.0', 'staging'])
    assert 'VALID' in result_verify.stdout, "Imported risk should verify as valid"
    print("  Imported risk verified successfully")

    if os.path.exists(export_file):
        os.remove(export_file)

    print("  [OK] PASSED: Export/import roundtrip works correctly")


def test_risk_import_hash_mismatch():
    """Test 14: Import with hash mismatch should fail."""
    print("\n" + "=" * 70)
    print("TEST 14: Import with hash mismatch")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'staging', '--role', 'developer'])
    export_file = 'test_risk_mismatch.json'
    run_cmd(['risk', 'export', '1.0.0', 'staging', '--output', export_file])
    print("  Risk exported")

    with open(export_file, 'r') as f:
        data = json.load(f)
    data['summary_hash'] = 'a' * 64
    with open(export_file, 'w') as f:
        json.dump(data, f)
    print("  Summary hash tampered in export file")

    cleanup()
    init_db_with_configs()

    result = run_cmd(['risk', 'import', export_file, '--role', 'release-manager'], expect_success=False)
    assert 'mismatch' in result.stderr.lower() or 'mismatch' in result.stdout.lower() or 'invalid' in result.stderr.lower(), \
        f"Should mention hash mismatch or invalid. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['risks']) == 0, "No risk assessment should be imported"

    error_logs = get_error_logs()
    mismatch_errors = [e for e in error_logs if e[1] in ('RISK_SUMMARY_MISMATCH', 'RISK_HASH_MISMATCH')]
    assert len(mismatch_errors) >= 1, "Should have hash mismatch error log"

    if os.path.exists(export_file):
        os.remove(export_file)

    print("  [OK] PASSED: Hash mismatch correctly rejected")


def test_risk_import_conflict_without_force():
    """Test 15: Import with conflict without --force should fail."""
    print("\n" + "=" * 70)
    print("TEST 15: Import with conflict without --force")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'staging', '--role', 'developer'])
    print("  Existing risk assessment created")

    export_file = 'test_risk_conflict.json'
    run_cmd(['risk', 'export', '1.0.0', 'staging', '--output', export_file])
    print("  Risk assessment exported")

    result = run_cmd(['risk', 'import', export_file, '--role', 'release-manager'], expect_success=False)
    assert 'already exists' in result.stderr.lower() or 'already exists' in result.stdout.lower(), \
        f"Should mention already exists. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    assert '--force' in result.stderr or '--force' in result.stdout, \
        f"Should mention --force option. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state = get_db_state()
    assert len(state['risks']) == 1, "Should still have only one risk assessment"

    if os.path.exists(export_file):
        os.remove(export_file)

    print("  [OK] PASSED: Conflict without --force correctly rejected")


def test_risk_import_conflict_with_force():
    """Test 16: Import with conflict with --force should succeed."""
    print("\n" + "=" * 70)
    print("TEST 16: Import with conflict with --force")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'staging', '--role', 'developer'])
    print("  Existing risk assessment created")

    export_file = 'test_risk_force.json'
    run_cmd(['risk', 'export', '1.0.0', 'staging', '--output', export_file])
    print("  Risk assessment exported")

    result = run_cmd(['risk', 'import', export_file, '--role', 'release-manager', '--force'])
    assert 'SUCCESS' in result.stdout and 'imported' in result.stdout.lower(), \
        f"Should show imported successfully. STDOUT: {result.stdout}"

    state = get_db_state()
    assert len(state['risks']) == 1, "Should still have only one risk assessment"

    if os.path.exists(export_file):
        os.remove(export_file)

    print("  [OK] PASSED: Conflict with --force correctly overwrites")


def test_apply_with_blocking_items():
    """Test 17: Risk scan with blocking items should produce critical level, and apply should be blocked."""
    print("\n" + "=" * 70)
    print("TEST 17: Apply with blocking items")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['apply', '1.0.0', 'staging', '--yes'])
    run_cmd(['pending', '1.0.0', 'prod'])
    run_cmd(['approve', '1.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['package', 'create', 'blocked-pkg', 'prod', '1.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'blocked-pkg', '--role', 'release-manager'])
    setup_blocking_scenario()
    print("  Setup: prod locked (blocking item)")

    risk_result = run_cmd(
        ['risk', 'scan', '1.0.0', 'prod', '--role', 'release-manager']
    )
    assert 'CRITICAL' in risk_result.stdout or 'critical' in risk_result.stdout.lower(), \
        f"Risk scan should show critical level. STDOUT: {risk_result.stdout}"

    risk = get_risk_assessment('1.0.0', 'prod')
    blocking_items = json.loads(risk['blocking_items'])
    assert len(blocking_items) > 0, "Risk assessment should have blocking items"
    assert any('locked' in item.lower() for item in blocking_items), \
        f"Blocking items should include locked env. Items: {blocking_items}"
    print(f"  Risk scan found blocking items: {blocking_items}")

    state_before = get_db_state()
    assert state_before['environments']['prod'] is None, "Prod should be at None"

    result = run_cmd(
        ['apply', '1.0.0', 'prod', '--yes', '--role', 'release-manager'],
        expect_success=False
    )

    assert 'locked' in result.stderr.lower() or 'locked' in result.stdout.lower() or \
           'blocked' in result.stderr.lower() or 'blocked' in result.stdout.lower(), \
        f"Should mention blocked or locked. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state_after = get_db_state()
    assert state_after['environments']['prod'] is None, \
        f"Prod current_version should NOT advance. Got: {state_after['environments']['prod']}"

    prod_releases = [(v, e, s) for v, e, s in state_after['releases']
                     if e == 'prod' and v == '1.0.0']
    success_releases = [r for r in prod_releases if r[2] == 'success']
    assert len(success_releases) == 0, \
        f"Should NOT have success release. Releases: {prod_releases}"

    print("  [OK] PASSED: Apply with blocking items correctly blocked")


def test_apply_with_high_risk_without_approval():
    """Test 18: Apply with high risk without approval should fail."""
    print("\n" + "=" * 70)
    print("TEST 18: Apply with high risk without approval")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    setup_high_risk_scenario()
    run_cmd(['package', 'create', 'highrisk-pkg', 'prod', '4.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'highrisk-pkg', '--role', 'release-manager'])
    print("  Setup: high risk scenario without risk approval")

    risk_result = run_cmd(['risk', 'scan', '4.0.0', 'prod', '--role', 'release-manager'])
    assert 'high' in risk_result.stdout.lower() or 'HIGH' in risk_result.stdout, \
        f"Risk scan should show high level. STDOUT: {risk_result.stdout}"
    risk = get_risk_assessment('4.0.0', 'prod')
    print(f"  Risk level: {risk['risk_level']}, score: {risk['risk_score']}")

    state_before = get_db_state()
    assert state_before['environments']['prod'] is None

    result = run_cmd(
        ['apply', '4.0.0', 'prod', '--yes', '--role', 'release-manager'],
        expect_success=False
    )

    assert 'approval' in result.stderr.lower() or 'approval' in result.stdout.lower(), \
        f"Should mention approval required. STDOUT: {result.stdout}, STDERR: {result.stderr}"
    assert 'RISK_APPROVAL_REQUIRED' in result.stderr or 'RISK_APPROVAL_REQUIRED' in result.stdout, \
        f"Should have error code. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state_after = get_db_state()
    assert state_after['environments']['prod'] is None, \
        f"Prod current_version should NOT advance. Got: {state_after['environments']['prod']}"

    error_logs = get_error_logs()
    approval_errors = [e for e in error_logs if e[1] == 'RISK_APPROVAL_REQUIRED']
    assert len(approval_errors) >= 1, "Should have risk approval required error log"

    print("  [OK] PASSED: Apply with high risk without approval correctly blocked")


def test_apply_with_high_risk_with_approval():
    """Test 19: Apply with high risk with approval should succeed."""
    print("\n" + "=" * 70)
    print("TEST 19: Apply with high risk with approval")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    setup_high_risk_scenario()
    run_cmd(['package', 'create', 'approved-pkg', 'prod', '4.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'approved-pkg', '--role', 'release-manager'])
    run_cmd(['risk', 'scan', '4.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['risk', 'approve', '4.0.0', 'prod', '--role', 'release-manager', '--notes', 'Approved for release'])
    print("  Setup: high risk scenario WITH risk approval")

    result = run_cmd(
        ['apply', '4.0.0', 'prod', '--yes', '--role', 'release-manager']
    )

    assert 'SUCCESS' in result.stdout and 'applied to prod' in result.stdout.lower(), \
        f"Should show success. STDOUT: {result.stdout}"

    state_after = get_db_state()
    assert state_after['environments']['prod'] == '4.0.0', \
        f"Prod should be at 4.0.0. Got: {state_after['environments']['prod']}"

    prod_releases = [(v, e, s) for v, e, s in state_after['releases']
                     if e == 'prod' and v == '4.0.0']
    success_releases = [r for r in prod_releases if r[2] == 'success']
    assert len(success_releases) >= 1, \
        f"Should have success release. Releases: {prod_releases}"

    print("  [OK] PASSED: Apply with high risk with approval succeeds")


def test_apply_after_risk_revoked():
    """Test 20: Apply after risk approval revoked should fail."""
    print("\n" + "=" * 70)
    print("TEST 20: Apply after risk approval revoked")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    setup_high_risk_scenario()
    run_cmd(['package', 'create', 'revoked-pkg', 'prod', '4.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'revoked-pkg', '--role', 'release-manager'])
    run_cmd(['risk', 'scan', '4.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['risk', 'approve', '4.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['risk', 'revoke', '4.0.0', 'prod', '--role', 'release-manager', '--reason', 'Issue found'])
    print("  Setup: risk approved then revoked")

    state_before = get_db_state()
    assert state_before['environments']['prod'] is None

    result = run_cmd(
        ['apply', '4.0.0', 'prod', '--yes', '--role', 'release-manager'],
        expect_success=False
    )

    assert 'revoked' in result.stderr.lower() or 'revoked' in result.stdout.lower() or \
           'blocked' in result.stderr.lower() or 'blocked' in result.stdout.lower(), \
        f"Should mention revoked or blocked. STDOUT: {result.stdout}, STDERR: {result.stderr}"

    state_after = get_db_state()
    assert state_after['environments']['prod'] is None, \
        f"Prod current_version should NOT advance. Got: {state_after['environments']['prod']}"

    prod_releases = [(v, e, s) for v, e, s in state_after['releases']
                     if e == 'prod' and v == '4.0.0']
    success_releases = [r for r in prod_releases if r[2] == 'success']
    assert len(success_releases) == 0, \
        f"Should NOT have success release. Releases: {prod_releases}"

    print("  [OK] PASSED: Apply after risk approval revoked correctly blocked")


def test_cross_restart_persistence():
    """Test 21: Risk assessments should persist across restarts."""
    print("\n" + "=" * 70)
    print("TEST 21: Cross-restart persistence")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'dev', '--role', 'developer'])
    run_cmd(['risk', 'scan', '2.0.0', 'staging', '--role', 'developer'])
    run_cmd(['risk', 'scan', '3.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['risk', 'approve', '3.0.0', 'prod', '--role', 'release-manager', '--notes', 'Approved'])
    print("  Risk assessments created")

    state_before = get_db_state()
    risks_before = state_before['risks']
    print(f"  Risks before: {risks_before}")

    list_before = run_cmd(['risk', 'list'])

    print("  Simulating restart by re-running commands...")

    state_after = get_db_state()
    risks_after = state_after['risks']
    print(f"  Risks after: {risks_after}")

    assert risks_before == risks_after, \
        f"Risk assessments should be identical. Before: {risks_before}, After: {risks_after}"

    list_after = run_cmd(['risk', 'list'])
    assert list_before.stdout == list_after.stdout, \
        "Risk list output should be identical"

    run_cmd(['risk', 'view', '1.0.0', 'dev'])
    run_cmd(['risk', 'view', '2.0.0', 'staging'])
    run_cmd(['risk', 'view', '3.0.0', 'prod'])
    print("  Risk assessments still accessible after 'restart'")

    print("  [OK] PASSED: Cross-restart persistence works correctly")


def test_failed_apply_no_side_effects():
    """Test 22: Failed apply does not advance current_version or write success release."""
    print("\n" + "=" * 70)
    print("TEST 22: Failed apply has no side effects")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    setup_high_risk_scenario()
    run_cmd(['package', 'create', 'sideeffect-pkg', 'prod', '2.0.0', '--role', 'release-manager'])
    run_cmd(['package', 'sign', 'sideeffect-pkg', '--role', 'release-manager'])
    print("  Setup: high risk without approval")

    state_before = get_db_state()
    print(f"  Before: prod version = {state_before['environments']['prod']}")
    print(f"  Before: releases = {len(state_before['releases'])}")

    prod_releases_before = [(v, e, s) for v, e, s in state_before['releases']
                            if e == 'prod' and v == '2.0.0']

    run_cmd(
        ['apply', '2.0.0', 'prod', '--yes', '--role', 'release-manager'],
        expect_success=False
    )
    print("  Apply failed (expected)")

    state_after = get_db_state()
    print(f"  After: prod version = {state_after['environments']['prod']}")
    print(f"  After: releases = {len(state_after['releases'])}")

    assert state_after['environments']['prod'] == state_before['environments']['prod'], \
        f"current_version should NOT change. Before: {state_before['environments']['prod']}, After: {state_after['environments']['prod']}"

    prod_releases_after = [(v, e, s) for v, e, s in state_after['releases']
                           if e == 'prod' and v == '2.0.0']
    success_releases_after = [r for r in prod_releases_after if r[2] == 'success']
    assert len(success_releases_after) == len([r for r in prod_releases_before if r[2] == 'success']), \
        f"Success release count should NOT change. Before: {prod_releases_before}, After: {prod_releases_after}"

    audit_fails_after = [a for a in state_after['audit_logs']
                         if a['action'] == 'apply' and a['status'] == 'failed'
                         and a.get('version') == '2.0.0' and a.get('environment') == 'prod']
    assert len(audit_fails_after) >= 1, "Should have failed audit log"

    error_logs_after = [e for e in state_after['error_logs']
                        if e['command'] == 'apply' and e.get('version') == '2.0.0'
                        and e.get('environment') == 'prod']
    assert len(error_logs_after) >= 1, "Should have error log"

    print("  [OK] PASSED: Failed apply correctly has no side effects on current_version or success releases")


def test_risk_scan_high_risk_features():
    """Test 23: Risk scan with high-risk features produces higher score."""
    print("\n" + "=" * 70)
    print("TEST 23: Risk scan with high-risk features")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'staging', '--role', 'developer'])
    risk_1 = get_risk_assessment('1.0.0', 'staging')
    score_1 = risk_1['risk_score']
    print(f"  Config v1 score: {score_1}, level: {risk_1['risk_level']}")

    run_cmd(['risk', 'scan', '2.0.0', 'staging', '--role', 'developer'])
    risk_2 = get_risk_assessment('2.0.0', 'staging')
    score_2 = risk_2['risk_score']
    print(f"  Config v2 score: {score_2}, level: {risk_2['risk_level']}")

    assert score_2 >= score_1, f"v2 should have >= score than v1. v1={score_1}, v2={score_2}"

    print("  [OK] PASSED: Risk scan with high-risk features produces higher score")


def test_risk_list_filters():
    """Test 24: Risk list with filters works correctly."""
    print("\n" + "=" * 70)
    print("TEST 24: Risk list with filters")
    print("=" * 70)

    cleanup()
    init_db_with_configs()

    run_cmd(['risk', 'scan', '1.0.0', 'dev', '--role', 'developer'])
    run_cmd(['risk', 'scan', '2.0.0', 'staging', '--role', 'developer'])
    run_cmd(['risk', 'scan', '3.0.0', 'prod', '--role', 'release-manager'])
    run_cmd(['risk', 'approve', '3.0.0', 'prod', '--role', 'release-manager'])
    print("  Risk assessments created")

    result_level = run_cmd(['risk', 'list', '--level', 'low'])
    assert '1.0.0' in result_level.stdout or '2.0.0' in result_level.stdout, \
        f"List with level filter should include low risk. STDOUT: {result_level.stdout}"

    result_status = run_cmd(['risk', 'list', '--status', 'approved'])
    assert '3.0.0' in result_status.stdout, \
        f"List with status filter should include approved. STDOUT: {result_status.stdout}"
    assert '1.0.0' not in result_status.stdout, \
        f"List with status filter should not include pending. STDOUT: {result_status.stdout}"

    result_limit = run_cmd(['risk', 'list', '--limit', '2'])
    lines = [l for l in result_limit.stdout.strip().split('\n') 
             if l.strip() and not l.startswith('=') and not l.startswith('ID') and not l.startswith('-') and not l.startswith('Total')]
    assert len(lines) <= 2, f"List with limit should return <= 2 items. Got: {len(lines)}"

    print("  [OK] PASSED: Risk list with filters works correctly")


def main():
    print("=" * 70)
    print("RISK ASSESSMENT REGRESSION TESTS")
    print("=" * 70)

    try:
        test_risk_scan_dev_developer()
        test_risk_scan_prod_developer()
        test_risk_scan_staging_developer()
        test_risk_scan_produces_valid_data()
        test_risk_scan_with_blocking_items()
        test_risk_approve_prod_developer()
        test_risk_approve_prod_release_manager()
        test_risk_revoke_prod_developer()
        test_risk_revoke_prod_release_manager()
        test_risk_view_and_list()
        test_risk_verify_valid()
        test_risk_verify_tampered()
        test_risk_export_import_roundtrip()
        test_risk_import_hash_mismatch()
        test_risk_import_conflict_without_force()
        test_risk_import_conflict_with_force()
        test_apply_with_blocking_items()
        test_apply_with_high_risk_without_approval()
        test_apply_with_high_risk_with_approval()
        test_apply_after_risk_revoked()
        test_cross_restart_persistence()
        test_failed_apply_no_side_effects()
        test_risk_scan_high_risk_features()
        test_risk_list_filters()

        print("\n" + "=" * 70)
        print("ALL TESTS PASSED!")
        print("=" * 70)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
