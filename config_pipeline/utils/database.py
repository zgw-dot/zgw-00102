import sqlite3
import json
import os
import hashlib
import getpass
from datetime import datetime
from contextlib import contextmanager

from .errors import (
    PipelineNotInitializedError,
    InvalidRoleError,
    PermissionDeniedError,
    EnvironmentError,
    ReleaseWindowError,
    OverridePermissionDeniedError,
    InvalidWindowTimeError,
    OverlappingWindowError,
    WindowNotFoundError,
)


VALID_ENVIRONMENTS = ["dev", "staging", "prod"]
VALID_ROLES = ["developer", "release-manager"]
ROLE_ENV_VAR = "PIPELINE_ROLE"
APPROVAL_REQUIRED_ENVS = ["prod"]
REQUIRED_KEYS = ["app_name", "version", "features", "database", "api_endpoints"]

DB_FILENAME = "pipeline.db"


def get_db_path():
    return os.path.join(os.getcwd(), DB_FILENAME)


def is_initialized():
    return os.path.exists(get_db_path())


@contextmanager
def get_db_connection():
    if not is_initialized():
        raise PipelineNotInitializedError()
    
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS environments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        current_version TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT NOT NULL,
        config_json TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(version)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS releases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT NOT NULL,
        environment TEXT NOT NULL,
        config_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        plan_summary TEXT
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS rollbacks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        environment TEXT NOT NULL,
        from_version TEXT NOT NULL,
        to_version TEXT NOT NULL,
        reason TEXT,
        created_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        environment TEXT,
        version TEXT,
        status TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        details TEXT,
        error_reason TEXT
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS error_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        command TEXT NOT NULL,
        error_code TEXT NOT NULL,
        error_message TEXT NOT NULL,
        environment TEXT,
        version TEXT,
        created_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        details TEXT
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS environment_locks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        environment TEXT UNIQUE NOT NULL,
        is_locked INTEGER NOT NULL DEFAULT 0,
        lock_reason TEXT,
        locked_by TEXT,
        locked_at TIMESTAMP,
        conflict_reason TEXT
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT NOT NULL,
        environment TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        requested_by TEXT NOT NULL,
        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        approved_by TEXT,
        approved_at TIMESTAMP,
        notes TEXT,
        conflict_reason TEXT,
        UNIQUE(version, environment)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS previews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT NOT NULL,
        environment TEXT NOT NULL,
        target_config_json TEXT NOT NULL,
        current_version TEXT,
        current_config_json TEXT,
        env_pointer_snapshot TEXT NOT NULL,
        lock_snapshot TEXT NOT NULL,
        approval_snapshot TEXT NOT NULL,
        plan_summary TEXT NOT NULL,
        diff_json TEXT NOT NULL,
        requires_approval INTEGER NOT NULL DEFAULT 0,
        requires_staging INTEGER NOT NULL DEFAULT 0,
        is_locked INTEGER NOT NULL DEFAULT 0,
        has_changes INTEGER NOT NULL DEFAULT 0,
        created_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(version, environment)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS release_windows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        environment TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_by TEXT NOT NULL,
        is_enabled INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS change_packages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_name TEXT UNIQUE NOT NULL,
        target_environment TEXT NOT NULL,
        versions_list TEXT NOT NULL,
        config_summary TEXT NOT NULL,
        summary_hash TEXT NOT NULL,
        created_by TEXT NOT NULL,
        signoff_status TEXT NOT NULL DEFAULT 'pending',
        signed_by TEXT,
        signed_at TIMESTAMP,
        revoked_by TEXT,
        revoked_at TIMESTAMP,
        revoke_reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    cursor.execute("PRAGMA table_info(releases)")
    columns = [col[1] for col in cursor.fetchall()]
    if "conflict_reason" not in columns:
        cursor.execute("ALTER TABLE releases ADD COLUMN conflict_reason TEXT")
    if "approved_by" not in columns:
        cursor.execute("ALTER TABLE releases ADD COLUMN approved_by TEXT")
    if "window_override_reason" not in columns:
        cursor.execute("ALTER TABLE releases ADD COLUMN window_override_reason TEXT")

    for env in VALID_ENVIRONMENTS:
        cursor.execute(
            "INSERT OR IGNORE INTO environments (name, current_version) VALUES (?, NULL)",
            (env,)
        )
        cursor.execute(
            "INSERT OR IGNORE INTO environment_locks (environment, is_locked) VALUES (?, 0)",
            (env,)
        )

    conn.commit()
    conn.close()


def get_current_user():
    return getpass.getuser()


def get_current_time():
    return datetime.now().isoformat()


def log_audit(action, status, environment=None, version=None, details=None, error_reason=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO audit_logs 
            (action, environment, version, status, created_by, details, error_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            action,
            environment,
            version,
            status,
            get_current_user(),
            json.dumps(details) if details else None,
            error_reason
        ))


def log_error(command, error_code, error_message, environment=None, version=None, details=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO error_logs 
            (command, error_code, error_message, environment, version, created_by, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            command,
            error_code,
            error_message,
            environment,
            version,
            get_current_user(),
            json.dumps(details) if details else None
        ))


def insert_config(version, config_json):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO configs (version, config_json, created_by)
            VALUES (?, ?, ?)
        ''', (version, json.dumps(config_json), get_current_user()))


def get_config(version):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM configs WHERE version = ?", (version,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def config_exists(version):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM configs WHERE version = ?", (version,))
        return cursor.fetchone() is not None


def get_current_version(env):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_version FROM environments WHERE name = ?", (env,))
        row = cursor.fetchone()
        return row["current_version"] if row else None


def set_current_version(env, version):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE environments 
            SET current_version = ?, updated_at = CURRENT_TIMESTAMP
            WHERE name = ?
        ''', (version, env))


def insert_release(version, environment, config_json, status, plan_summary=None, approved_by=None, conflict_reason=None, window_override_reason=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO releases 
            (version, environment, config_json, status, created_by, plan_summary, approved_by, conflict_reason, window_override_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            version,
            environment,
            json.dumps(config_json),
            status,
            get_current_user(),
            plan_summary,
            approved_by,
            conflict_reason,
            window_override_reason
        ))


def get_release(version, environment):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM releases 
            WHERE version = ? AND environment = ? AND status = 'success'
            ORDER BY id DESC LIMIT 1
        ''', (version, environment))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def has_successful_release(version, environment):
    return get_release(version, environment) is not None


def insert_rollback(environment, from_version, to_version, reason=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO rollbacks 
            (environment, from_version, to_version, reason, created_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (environment, from_version, to_version, reason, get_current_user()))


def get_audit_logs(limit=100):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_audit_logs_filtered(environment=None, status=None, since=None, limit=1000):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM audit_logs WHERE 1=1"
        params = []

        if environment is not None:
            query += " AND environment = ?"
            params.append(environment)

        if status is not None:
            query += " AND status = ?"
            params.append(status)

        if since is not None:
            query += " AND created_at >= ?"
            params.append(since)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_releases(environment=None, limit=100):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if environment:
            cursor.execute('''
                SELECT * FROM releases WHERE environment = ? 
                ORDER BY id DESC LIMIT ?
            ''', (environment, limit))
        else:
            cursor.execute('''
                SELECT * FROM releases ORDER BY id DESC LIMIT ?
            ''', (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_rollbacks(environment=None, limit=100):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if environment:
            cursor.execute('''
                SELECT * FROM rollbacks WHERE environment = ? 
                ORDER BY id DESC LIMIT ?
            ''', (environment, limit))
        else:
            cursor.execute('''
                SELECT * FROM rollbacks ORDER BY id DESC LIMIT ?
            ''', (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_all_error_logs(limit=100):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM error_logs ORDER BY id DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_environment_status():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM environments ORDER BY name")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_role(cli_role=None):
    """Get role from CLI parameter or environment variable."""
    role = cli_role or os.environ.get(ROLE_ENV_VAR)
    if not role:
        role = "developer"
    if role not in VALID_ROLES:
        raise InvalidRoleError(role, VALID_ROLES)
    return role


def check_permission(action, required_role, cli_role=None):
    """Check if current role has permission for the given action."""
    current_role = get_role(cli_role)
    role_hierarchy = {
        "developer": 1,
        "release-manager": 2,
    }
    if role_hierarchy.get(current_role, 0) < role_hierarchy.get(required_role, 999):
        raise PermissionDeniedError(action, required_role, current_role)
    return True


def is_environment_locked(environment):
    """Check if an environment is locked."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT is_locked FROM environment_locks WHERE environment = ?",
            (environment,)
        )
        row = cursor.fetchone()
        return row["is_locked"] == 1 if row else False


def get_environment_lock(environment):
    """Get lock details for an environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM environment_locks WHERE environment = ?",
            (environment,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_environment_locks():
    """Get lock status for all environments."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM environment_locks ORDER BY environment")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def lock_environment(environment, reason=None, conflict_reason=None):
    """Lock an environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT is_locked FROM environment_locks WHERE environment = ?",
            (environment,)
        )
        row = cursor.fetchone()
        if row and row["is_locked"] == 1:
            return False
        cursor.execute('''
            UPDATE environment_locks
            SET is_locked = 1, lock_reason = ?, locked_by = ?, locked_at = CURRENT_TIMESTAMP, conflict_reason = ?
            WHERE environment = ?
        ''', (reason, get_current_user(), conflict_reason, environment))
        return True


def unlock_environment(environment):
    """Unlock an environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT is_locked FROM environment_locks WHERE environment = ?",
            (environment,)
        )
        row = cursor.fetchone()
        if not row or row["is_locked"] == 0:
            return False
        cursor.execute('''
            UPDATE environment_locks
            SET is_locked = 0, lock_reason = NULL, locked_by = NULL, locked_at = NULL, conflict_reason = NULL
            WHERE environment = ?
        ''', (environment,))
        return True


def requires_approval(environment):
    """Check if an environment requires approval for releases."""
    return environment in APPROVAL_REQUIRED_ENVS


def create_pending_approval(version, environment, notes=None):
    """Create a pending approval for a version in an environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO approvals (version, environment, status, requested_by, notes)
                VALUES (?, ?, 'pending', ?, ?)
            ''', (version, environment, get_current_user(), notes))
            return True
        except sqlite3.IntegrityError:
            return False


def get_approval(version, environment):
    """Get approval record for a version in an environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM approvals
            WHERE version = ? AND environment = ?
            ORDER BY id DESC LIMIT 1
        ''', (version, environment))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_pending_approvals(environment=None):
    """Get all pending approvals, optionally filtered by environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if environment:
            cursor.execute('''
                SELECT * FROM approvals
                WHERE status = 'pending' AND environment = ?
                ORDER BY requested_at DESC
            ''', (environment,))
        else:
            cursor.execute('''
                SELECT * FROM approvals
                WHERE status = 'pending'
                ORDER BY requested_at DESC
            ''')
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_all_approvals(environment=None, limit=100):
    """Get all approvals, optionally filtered by environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if environment:
            cursor.execute('''
                SELECT * FROM approvals
                WHERE environment = ?
                ORDER BY requested_at DESC LIMIT ?
            ''', (environment, limit))
        else:
            cursor.execute('''
                SELECT * FROM approvals
                ORDER BY requested_at DESC LIMIT ?
            ''', (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def is_approved(version, environment):
    """Check if a version is approved for an environment."""
    if not requires_approval(environment):
        return True
    approval = get_approval(version, environment)
    return approval is not None and approval["status"] == "approved"


def approve_version(version, environment, cli_role=None, notes=None):
    """Approve a version for release to an environment."""
    check_permission("approve", "release-manager", cli_role)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE approvals
            SET status = 'approved', approved_by = ?, approved_at = CURRENT_TIMESTAMP, notes = ?
            WHERE version = ? AND environment = ? AND status = 'pending'
        ''', (get_current_user(), notes, version, environment))
        return cursor.rowcount > 0


def reject_approval(version, environment, cli_role=None, conflict_reason=None):
    """Reject a pending approval."""
    check_permission("approve", "release-manager", cli_role)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE approvals
            SET status = 'rejected', approved_by = ?, approved_at = CURRENT_TIMESTAMP, conflict_reason = ?
            WHERE version = ? AND environment = ? AND status = 'pending'
        ''', (get_current_user(), conflict_reason, version, environment))
        return cursor.rowcount > 0


def set_release_conflict_reason(release_id, conflict_reason):
    """Set conflict reason on a release record."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE releases SET conflict_reason = ? WHERE id = ?",
            (conflict_reason, release_id)
        )


def get_all_configs():
    """Get all configuration versions."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM configs ORDER BY created_at")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_snapshot_data():
    """Get all snapshot data: configs, environments, pending approvals, and lock status."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT version, config_json, created_by, created_at FROM configs ORDER BY created_at")
        configs = []
        for row in cursor.fetchall():
            configs.append({
                "version": row["version"],
                "config_json": json.loads(row["config_json"]),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            })

        cursor.execute("SELECT name, current_version, updated_at FROM environments ORDER BY name")
        environments = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT version, environment, status, requested_by, requested_at, approved_by, approved_at, notes FROM approvals ORDER BY requested_at")
        approvals = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT environment, is_locked, lock_reason, locked_by, locked_at FROM environment_locks ORDER BY environment")
        locks = []
        for row in cursor.fetchall():
            locks.append({
                "environment": row["environment"],
                "is_locked": row["is_locked"] == 1,
                "lock_reason": row["lock_reason"],
                "locked_by": row["locked_by"],
                "locked_at": row["locked_at"],
            })

        return {
            "snapshot_metadata": {
                "snapshot_version": "1.0",
                "exported_at": get_current_time(),
                "exported_by": get_current_user(),
            },
            "configs": configs,
            "environments": environments,
            "approvals": approvals,
            "environment_locks": locks,
        }


def check_snapshot_conflicts(snapshot_data):
    """Check for conflicts between snapshot data and current database state.
    Returns a list of conflict descriptions.
    """
    conflicts = []
    with get_db_connection() as conn:
        cursor = conn.cursor()

        for cfg in snapshot_data.get("configs", []):
            cursor.execute("SELECT 1 FROM configs WHERE version = ?", (cfg["version"],))
            if cursor.fetchone():
                conflicts.append(f"Config version '{cfg['version']}' already exists")

        for env in snapshot_data.get("environments", []):
            if env.get("current_version"):
                cursor.execute("SELECT current_version FROM environments WHERE name = ?", (env["name"],))
                row = cursor.fetchone()
                if row and row["current_version"] == env["current_version"]:
                    conflicts.append(f"Environment '{env['name']}' already has version pointer '{env['current_version']}'")

        for app in snapshot_data.get("approvals", []):
            cursor.execute(
                "SELECT 1 FROM approvals WHERE version = ? AND environment = ?",
                (app["version"], app["environment"])
            )
            if cursor.fetchone():
                conflicts.append(f"Approval record for version '{app['version']}' in environment '{app['environment']}' already exists")

    return conflicts


def import_snapshot(snapshot_data, force=False, role=None):
    """Import snapshot data into the database.
    Returns (success, message, details).
    Uses a single transaction to ensure atomicity.
    """
    conn = None
    try:
        db_path = get_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("BEGIN IMMEDIATE")

        import_details = {
            "configs_imported": 0,
            "configs_skipped": 0,
            "environments_updated": 0,
            "approvals_imported": 0,
            "approvals_skipped": 0,
            "locks_updated": 0,
            "locks_skipped": 0,
            "permissions_applied": [],
        }

        current_role = role or get_role()
        is_release_manager = current_role == "release-manager"

        for cfg in snapshot_data.get("configs", []):
            cursor.execute("SELECT 1 FROM configs WHERE version = ?", (cfg["version"],))
            if cursor.fetchone():
                if force:
                    cursor.execute(
                        "UPDATE configs SET config_json = ?, created_by = ?, created_at = ? WHERE version = ?",
                        (json.dumps(cfg["config_json"]), cfg["created_by"], cfg["created_at"], cfg["version"])
                    )
                    import_details["configs_imported"] += 1
                else:
                    import_details["configs_skipped"] += 1
            else:
                cursor.execute(
                    "INSERT INTO configs (version, config_json, created_by, created_at) VALUES (?, ?, ?, ?)",
                    (cfg["version"], json.dumps(cfg["config_json"]), cfg["created_by"], cfg["created_at"])
                )
                import_details["configs_imported"] += 1

        for env in snapshot_data.get("environments", []):
            cursor.execute(
                "UPDATE environments SET current_version = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (env.get("current_version"), env["name"])
            )
            import_details["environments_updated"] += 1

        for app in snapshot_data.get("approvals", []):
            if app["environment"] == "prod" and not is_release_manager:
                import_details["approvals_skipped"] += 1
                import_details["permissions_applied"].append(
                    f"Skipped prod approval '{app['version']}' (requires release-manager role)"
                )
                continue

            cursor.execute(
                "SELECT 1 FROM approvals WHERE version = ? AND environment = ?",
                (app["version"], app["environment"])
            )
            if cursor.fetchone():
                if force:
                    cursor.execute(
                        """UPDATE approvals SET status = ?, requested_by = ?, requested_at = ?, 
                           approved_by = ?, approved_at = ?, notes = ?
                           WHERE version = ? AND environment = ?""",
                        (app["status"], app["requested_by"], app["requested_at"],
                         app.get("approved_by"), app.get("approved_at"), app.get("notes"),
                         app["version"], app["environment"])
                    )
                    import_details["approvals_imported"] += 1
                else:
                    import_details["approvals_skipped"] += 1
            else:
                cursor.execute(
                    """INSERT INTO approvals (version, environment, status, requested_by, requested_at, 
                       approved_by, approved_at, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (app["version"], app["environment"], app["status"], app["requested_by"], app["requested_at"],
                     app.get("approved_by"), app.get("approved_at"), app.get("notes"))
                )
                import_details["approvals_imported"] += 1

        for lock in snapshot_data.get("environment_locks", []):
            if lock["environment"] == "prod" and not is_release_manager:
                import_details["locks_skipped"] += 1
                import_details["permissions_applied"].append(
                    f"Skipped prod lock status (requires release-manager role)"
                )
                continue

            is_locked_val = 1 if lock["is_locked"] else 0
            cursor.execute(
                """UPDATE environment_locks 
                   SET is_locked = ?, lock_reason = ?, locked_by = ?, locked_at = ?
                   WHERE environment = ?""",
                (is_locked_val, lock.get("lock_reason"), lock.get("locked_by"),
                 lock.get("locked_at"), lock["environment"])
            )
            import_details["locks_updated"] += 1

        conn.commit()
        return True, "Snapshot imported successfully", import_details

    except Exception as e:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def insert_preview(version, environment, target_config, current_version, current_config,
                   plan_summary, diff, requires_approval, requires_staging, is_locked, has_changes):
    """Insert or replace a preview record."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        env_pointer_snapshot = json.dumps({
            env: get_current_version(env) for env in VALID_ENVIRONMENTS
        })

        lock_snapshot = json.dumps({
            env: is_environment_locked(env) for env in VALID_ENVIRONMENTS
        })

        approval_snapshot = json.dumps({
            "is_approved": is_approved(version, environment),
            "approval_status": get_approval(version, environment)
        })

        cursor.execute('''
            INSERT OR REPLACE INTO previews 
            (version, environment, target_config_json, current_version, current_config_json,
             env_pointer_snapshot, lock_snapshot, approval_snapshot, plan_summary, diff_json,
             requires_approval, requires_staging, is_locked, has_changes, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            version,
            environment,
            json.dumps(target_config),
            current_version,
            json.dumps(current_config) if current_config else None,
            env_pointer_snapshot,
            lock_snapshot,
            approval_snapshot,
            json.dumps(plan_summary),
            json.dumps(diff),
            1 if requires_approval else 0,
            1 if requires_staging else 0,
            1 if is_locked else 0,
            1 if has_changes else 0,
            get_current_user()
        ))


def get_latest_preview(version=None, environment=None):
    """Get the latest preview, optionally filtered by version and environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM previews WHERE 1=1"
        params = []

        if version:
            query += " AND version = ?"
            params.append(version)
        if environment:
            query += " AND environment = ?"
            params.append(environment)

        query += " ORDER BY id DESC LIMIT 1"
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row:
            return _row_to_preview_dict(row)
        return None


def get_preview_by_id(preview_id):
    """Get a preview by ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM previews WHERE id = ?", (preview_id,))
        row = cursor.fetchone()
        if row:
            return _row_to_preview_dict(row)
        return None


def get_all_previews(limit=100):
    """Get all previews, most recent first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM previews ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return [_row_to_preview_dict(row) for row in rows]


def _row_to_preview_dict(row):
    """Convert a preview row to a dict with JSON fields parsed."""
    return {
        "id": row["id"],
        "version": row["version"],
        "environment": row["environment"],
        "target_config": json.loads(row["target_config_json"]),
        "current_version": row["current_version"],
        "current_config": json.loads(row["current_config_json"]) if row["current_config_json"] else None,
        "env_pointer_snapshot": json.loads(row["env_pointer_snapshot"]),
        "lock_snapshot": json.loads(row["lock_snapshot"]),
        "approval_snapshot": json.loads(row["approval_snapshot"]),
        "plan_summary": json.loads(row["plan_summary"]),
        "diff": json.loads(row["diff_json"]),
        "requires_approval": row["requires_approval"] == 1,
        "requires_staging": row["requires_staging"] == 1,
        "is_locked": row["is_locked"] == 1,
        "has_changes": row["has_changes"] == 1,
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def _deep_diff_configs(config1, config2, prefix=""):
    """Recursively compare two config dicts and return list of differences."""
    diffs = []
    if config1 is None or config2 is None:
        if config1 != config2:
            diffs.append(f"{prefix}: {config1} -> {config2}")
        return diffs

    if isinstance(config1, dict) and isinstance(config2, dict):
        all_keys = set(config1.keys()) | set(config2.keys())
        for key in sorted(all_keys):
            new_prefix = f"{prefix}.{key}" if prefix else key
            if key not in config1:
                diffs.append(f"{new_prefix}: added -> {config2[key]}")
            elif key not in config2:
                diffs.append(f"{new_prefix}: {config1[key]} -> removed")
            else:
                diffs.extend(_deep_diff_configs(config1[key], config2[key], new_prefix))
    elif isinstance(config1, list) and isinstance(config2, list):
        if json.dumps(config1, sort_keys=True) != json.dumps(config2, sort_keys=True):
            diffs.append(f"{prefix}: list content changed")
    else:
        if config1 != config2:
            diffs.append(f"{prefix}: {config1} -> {config2}")
    return diffs


def check_preview_drift(preview):
    """Check if the preview has drifted from current state.
    
    Returns a list of drift reasons. Empty list means no drift.
    """
    drift_reasons = []

    # Check environment pointer drift
    for env in VALID_ENVIRONMENTS:
        current_pointer = get_current_version(env)
        snapshot_pointer = preview["env_pointer_snapshot"].get(env)
        if current_pointer != snapshot_pointer:
            drift_reasons.append(
                f"Environment '{env}' pointer changed: '{snapshot_pointer}' -> '{current_pointer}'"
            )

    # Check lock status drift for target environment
    current_lock = is_environment_locked(preview["environment"])
    snapshot_lock = preview["lock_snapshot"].get(preview["environment"], False)
    if current_lock != snapshot_lock:
        lock_change = "locked" if current_lock else "unlocked"
        drift_reasons.append(
            f"Environment '{preview['environment']}' is now {lock_change}"
        )

    # Check if target config still exists
    if not config_exists(preview["version"]):
        drift_reasons.append(
            f"Target version '{preview['version']}' no longer exists in configs"
        )
    else:
        # Check if target config content has changed
        current_config_data = get_config(preview["version"])
        current_config = json.loads(current_config_data["config_json"])
        snapshot_config = preview["target_config"]

        config_diffs = _deep_diff_configs(snapshot_config, current_config)
        if config_diffs:
            drift_reasons.append(
                f"Target config '{preview['version']}' content changed: {', '.join(config_diffs[:3])}"
                + ("..." if len(config_diffs) > 3 else "")
            )

    return drift_reasons


def delete_preview(version, environment):
    """Delete a preview record."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM previews WHERE version = ? AND environment = ?",
            (version, environment)
        )
        return cursor.rowcount > 0


def compute_config_summary(versions):
    """Compute a summary and hash for a list of configuration versions.

    Returns (summary_dict, hash_hex)
    """
    summary = []
    for version in sorted(versions):
        cfg = get_config(version)
        if not cfg:
            raise ValueError(f"Version {version} not found")
        config_data = json.loads(cfg["config_json"])
        config_hash = hashlib.sha256(
            json.dumps(config_data, sort_keys=True).encode("utf-8")
        ).hexdigest()
        summary.append({
            "version": version,
            "config_hash": config_hash,
            "created_by": cfg["created_by"],
            "created_at": cfg["created_at"],
        })

    hash_input = json.dumps([
        {"version": s["version"], "config_hash": s["config_hash"]}
        for s in sorted(summary, key=lambda x: x["version"])
    ], sort_keys=True)
    summary_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
    return summary, summary_hash


def create_package(package_name, target_environment, versions, cli_role=None):
    """Create a new change package.

    Args:
        package_name: Unique name for the package
        target_environment: Target environment (dev/staging/prod)
        versions: List of configuration versions to include
        cli_role: Optional role override

    Returns:
        Package dict

    Raises:
        PackageAlreadyExistsError
        PackageVersionNotFoundError
        PermissionDeniedError (for developer creating prod package)
        EnvironmentError
    """
    from .errors import (
        PackageAlreadyExistsError,
        PackageVersionNotFoundError,
        EnvironmentError,
    )

    if target_environment not in VALID_ENVIRONMENTS:
        raise EnvironmentError(target_environment, VALID_ENVIRONMENTS)

    if target_environment == "prod":
        check_permission("package.create.prod", "release-manager", cli_role)

    current_role = get_role(cli_role)
    if target_environment == "prod" and current_role != "release-manager":
        check_permission("package.create.prod", "release-manager", cli_role)

    for version in versions:
        if not config_exists(version):
            raise PackageVersionNotFoundError(version)

    if package_exists(package_name):
        raise PackageAlreadyExistsError(package_name)

    config_summary, summary_hash = compute_config_summary(versions)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO change_packages
            (package_name, target_environment, versions_list, config_summary, summary_hash,
             created_by, signoff_status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
        ''', (
            package_name,
            target_environment,
            json.dumps(sorted(versions)),
            json.dumps(config_summary),
            summary_hash,
            get_current_user(),
        ))
        pkg_id = cursor.lastrowid

    log_audit(
        "package.create",
        "success",
        environment=target_environment,
        details={
            "package_name": package_name,
            "versions": versions,
            "summary_hash": summary_hash,
            "role": current_role,
        }
    )

    return get_package(package_name)


def package_exists(package_name):
    """Check if a package exists."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM change_packages WHERE package_name = ?",
            (package_name,)
        )
        return cursor.fetchone() is not None


def get_package(package_name):
    """Get a package by name. Returns dict or None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM change_packages WHERE package_name = ?",
            (package_name,)
        )
        row = cursor.fetchone()
        if row:
            return _row_to_package_dict(row)
        return None


def get_all_packages(environment=None, limit=100):
    """Get all packages, optionally filtered by environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if environment:
            cursor.execute('''
                SELECT * FROM change_packages
                WHERE target_environment = ?
                ORDER BY id DESC LIMIT ?
            ''', (environment, limit))
        else:
            cursor.execute('''
                SELECT * FROM change_packages
                ORDER BY id DESC LIMIT ?
            ''', (limit,))
        rows = cursor.fetchall()
        return [_row_to_package_dict(row) for row in rows]


def _row_to_package_dict(row):
    """Convert a package row to a dict with JSON fields parsed."""
    return {
        "id": row["id"],
        "package_name": row["package_name"],
        "target_environment": row["target_environment"],
        "versions": json.loads(row["versions_list"]),
        "config_summary": json.loads(row["config_summary"]),
        "summary_hash": row["summary_hash"],
        "created_by": row["created_by"],
        "signoff_status": row["signoff_status"],
        "signed_by": row["signed_by"],
        "signed_at": row["signed_at"],
        "revoked_by": row["revoked_by"],
        "revoked_at": row["revoked_at"],
        "revoke_reason": row["revoke_reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def sign_package(package_name, cli_role=None, notes=None):
    """Sign off a package for release. Only release-manager can sign prod packages.

    Returns True if successful.

    Raises:
        PackageNotFoundError
        PackageAlreadySignedError
        PermissionDeniedError
    """
    from .errors import PackageNotFoundError, PackageAlreadySignedError

    pkg = get_package(package_name)
    if not pkg:
        raise PackageNotFoundError(package_name)

    if pkg["signoff_status"] == "signed":
        raise PackageAlreadySignedError(package_name)

    check_permission("package.sign", "release-manager", cli_role)

    if pkg["target_environment"] == "prod":
        check_permission("package.sign.prod", "release-manager", cli_role)

    current_role = get_role(cli_role)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE change_packages
            SET signoff_status = 'signed',
                signed_by = ?,
                signed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE package_name = ? AND signoff_status != 'signed'
        ''', (get_current_user(), package_name))
        if cursor.rowcount == 0:
            raise PackageAlreadySignedError(package_name)

    log_audit(
        "package.sign",
        "success",
        environment=pkg["target_environment"],
        details={
            "package_name": package_name,
            "versions": pkg["versions"],
            "signed_by": get_current_user(),
            "role": current_role,
            "notes": notes,
        }
    )

    return True


def revoke_package_signoff(package_name, cli_role=None, reason=None):
    """Revoke a package signoff. Only release-manager can revoke.

    Returns True if successful.

    Raises:
        PackageNotFoundError
        PackageNotSignedForRevokeError
        PermissionDeniedError
    """
    from .errors import PackageNotFoundError, PackageNotSignedForRevokeError

    pkg = get_package(package_name)
    if not pkg:
        raise PackageNotFoundError(package_name)

    if pkg["signoff_status"] != "signed":
        raise PackageNotSignedForRevokeError(package_name)

    check_permission("package.revoke", "release-manager", cli_role)

    current_role = get_role(cli_role)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE change_packages
            SET signoff_status = 'pending',
                signed_by = NULL,
                signed_at = NULL,
                revoked_by = ?,
                revoked_at = CURRENT_TIMESTAMP,
                revoke_reason = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE package_name = ? AND signoff_status = 'signed'
        ''', (get_current_user(), reason, package_name))
        if cursor.rowcount == 0:
            raise PackageNotSignedForRevokeError(package_name)

    log_audit(
        "package.revoke",
        "success",
        environment=pkg["target_environment"],
        details={
            "package_name": package_name,
            "versions": pkg["versions"],
            "revoked_by": get_current_user(),
            "role": current_role,
            "reason": reason,
        }
    )

    return True


def is_version_in_signed_package(version, environment):
    """Check if a version is part of a signed package for the given environment.

    Returns the package name if found, None otherwise.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT package_name, versions_list FROM change_packages
            WHERE target_environment = ? AND signoff_status = 'signed'
            ORDER BY id DESC
        ''', (environment,))
        rows = cursor.fetchall()
        for row in rows:
            versions = json.loads(row["versions_list"])
            if version in versions:
                return row["package_name"]
        return None


def verify_package(package_name):
    """Verify a package's integrity.

    Checks:
    1. All versions still exist
    2. Config content hasn't changed (hash matches)

    Returns (is_valid, issues_list)
    """
    from .errors import PackageNotFoundError

    pkg = get_package(package_name)
    if not pkg:
        raise PackageNotFoundError(package_name)

    issues = []

    for item in pkg["config_summary"]:
        version = item["version"]
        expected_hash = item["config_hash"]

        if not config_exists(version):
            issues.append(f"Version '{version}' no longer exists in configs")
            continue

        cfg = get_config(version)
        config_data = json.loads(cfg["config_json"])
        actual_hash = hashlib.sha256(
            json.dumps(config_data, sort_keys=True).encode("utf-8")
        ).hexdigest()

        if actual_hash != expected_hash:
            issues.append(
                f"Version '{version}' content has changed. "
                f"Expected hash: {expected_hash[:12]}..., "
                f"Actual: {actual_hash[:12]}..."
            )

    try:
        _, actual_summary_hash = compute_config_summary(pkg["versions"])
        if actual_summary_hash != pkg["summary_hash"]:
            issues.append(
                f"Package summary hash mismatch. "
                f"Expected: {pkg['summary_hash'][:12]}..., "
                f"Actual: {actual_summary_hash[:12]}..."
            )
    except ValueError as e:
        issues.append(str(e))

    return len(issues) == 0, issues


def export_package(package_name):
    """Export a package to a dict for JSON serialization."""
    from .errors import PackageNotFoundError

    pkg = get_package(package_name)
    if not pkg:
        raise PackageNotFoundError(package_name)

    return {
        "package_format_version": "1.0",
        "package_name": pkg["package_name"],
        "target_environment": pkg["target_environment"],
        "versions": pkg["versions"],
        "config_summary": pkg["config_summary"],
        "summary_hash": pkg["summary_hash"],
        "created_by": pkg["created_by"],
        "created_at": pkg["created_at"],
        "signoff_status": pkg["signoff_status"],
        "signed_by": pkg["signed_by"],
        "signed_at": pkg["signed_at"],
        "_meta": {
            "exported_at": get_current_time(),
            "exported_by": get_current_user(),
        }
    }


def import_package(package_data, cli_role=None, force=False):
    """Import a package from exported data.

    Args:
        package_data: Dict from export_package
        cli_role: Optional role override
        force: If True, overwrite existing package

    Returns:
        Imported package dict

    Raises:
        InvalidPackageFormatError
        PackageAlreadyExistsError (unless force=True)
        PackageVersionNotFoundError
        PackageSummaryMismatchError
        PermissionDeniedError
    """
    from .errors import (
        InvalidPackageFormatError,
        PackageAlreadyExistsError,
        PackageVersionNotFoundError,
        PackageSummaryMismatchError,
        EnvironmentError,
    )

    required_fields = [
        "package_name", "target_environment", "versions",
        "config_summary", "summary_hash",
    ]
    for field in required_fields:
        if field not in package_data:
            raise InvalidPackageFormatError(f"Missing required field: {field}")

    package_name = package_data["package_name"]
    target_env = package_data["target_environment"]
    versions = package_data["versions"]
    expected_hash = package_data["summary_hash"]

    if target_env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(target_env, VALID_ENVIRONMENTS)

    if package_exists(package_name) and not force:
        raise PackageAlreadyExistsError(package_name)

    current_role = get_role(cli_role)
    if target_env == "prod":
        check_permission("package.import.prod", "release-manager", cli_role)

    for version in versions:
        if not config_exists(version):
            log_error(
                "package.import",
                "PACKAGE_VERSION_NOT_FOUND",
                f"Version '{version}' not found for package '{package_name}'",
                environment=target_env,
                details={
                    "package_name": package_name,
                    "missing_version": version,
                }
            )
            log_audit(
                "package.import",
                "failed",
                environment=target_env,
                error_reason=f"Version '{version}' not found",
                details={
                    "package_name": package_name,
                    "missing_version": version,
                    "role": current_role,
                }
            )
            raise PackageVersionNotFoundError(version)

    try:
        _, actual_hash = compute_config_summary(versions)
    except ValueError as e:
        raise PackageVersionNotFoundError(str(e).split("'")[1])

    if actual_hash != expected_hash:
        log_error(
            "package.import",
            "PACKAGE_SUMMARY_MISMATCH",
            f"Summary hash mismatch for package '{package_name}'",
            environment=target_env,
            details={
                "package_name": package_name,
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
            }
        )
        log_audit(
            "package.import",
            "failed",
            environment=target_env,
            error_reason=f"Summary hash mismatch for package '{package_name}'",
            details={
                "package_name": package_name,
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
                "role": current_role,
            }
        )
        raise PackageSummaryMismatchError(package_name, expected_hash, actual_hash)

    config_summary = package_data["config_summary"]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if package_exists(package_name) and force:
            cursor.execute('''
                UPDATE change_packages
                SET target_environment = ?,
                    versions_list = ?,
                    config_summary = ?,
                    summary_hash = ?,
                    signoff_status = 'pending',
                    signed_by = NULL,
                    signed_at = NULL,
                    revoked_by = NULL,
                    revoked_at = NULL,
                    revoke_reason = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE package_name = ?
            ''', (
                target_env,
                json.dumps(sorted(versions)),
                json.dumps(config_summary),
                expected_hash,
                package_name,
            ))
        else:
            cursor.execute('''
                INSERT INTO change_packages
                (package_name, target_environment, versions_list, config_summary, summary_hash,
                 created_by, signoff_status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            ''', (
                package_name,
                target_env,
                json.dumps(sorted(versions)),
                json.dumps(config_summary),
                expected_hash,
                get_current_user(),
            ))

    log_audit(
        "package.import",
        "success",
        environment=target_env,
        details={
            "package_name": package_name,
            "versions": versions,
            "summary_hash": expected_hash,
            "role": current_role,
            "force": force,
        }
    )

    return get_package(package_name)


def requires_package_signoff(environment):
    """Check if an environment requires package signoff for releases."""
    return environment == "prod"


def check_package_signoff(version, environment):
    """Check if a version has valid package signoff for the environment.

    Returns (is_valid, package_name_or_None, error_message_or_None)
    """
    if not requires_package_signoff(environment):
        return True, None, None

    package_name = is_version_in_signed_package(version, environment)
    if not package_name:
        return False, None, f"Version '{version}' must be in a signed package for {environment}"

    is_valid, issues = verify_package(package_name)
    if not is_valid:
        return False, package_name, f"Package '{package_name}' verification failed: {'; '.join(issues)}"

    return True, package_name, None


def parse_datetime(dt_str):
    """Parse datetime string in ISO format."""
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        raise InvalidWindowTimeError(f"Invalid datetime format: {dt_str}. Expected ISO format (e.g., 2024-01-01T12:00:00)")


def validate_window_times(start_time_str, end_time_str):
    """Validate window start and end times."""
    start_time = parse_datetime(start_time_str)
    end_time = parse_datetime(end_time_str)
    
    if end_time <= start_time:
        raise InvalidWindowTimeError(f"End time ({end_time_str}) must be after start time ({start_time_str})")
    
    return start_time, end_time


def get_overlapping_windows(environment, start_time_str, end_time_str, exclude_window_id=None):
    """Find overlapping windows for an environment."""
    start_time, end_time = validate_window_times(start_time_str, end_time_str)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = '''
            SELECT * FROM release_windows 
            WHERE environment = ? AND is_enabled = 1
        '''
        params = [environment]
        
        if exclude_window_id is not None:
            query += " AND id != ?"
            params.append(exclude_window_id)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        overlapping = []
        for row in rows:
            w_start = parse_datetime(row["start_time"])
            w_end = parse_datetime(row["end_time"])
            
            if not (end_time <= w_start or start_time >= w_end):
                overlapping.append(dict(row))
        
        return overlapping


def create_release_window(environment, start_time_str, end_time_str, reason, cli_role=None):
    """Create a new release window (closes the window for releases)."""
    if environment not in VALID_ENVIRONMENTS:
        raise EnvironmentError(environment, VALID_ENVIRONMENTS)
    
    if environment == "prod":
        check_permission("create_release_window", "release-manager", cli_role)
    
    overlapping = get_overlapping_windows(environment, start_time_str, end_time_str)
    if overlapping:
        raise OverlappingWindowError(environment, overlapping)
    
    validate_window_times(start_time_str, end_time_str)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO release_windows 
            (environment, start_time, end_time, reason, created_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            environment,
            start_time_str,
            end_time_str,
            reason,
            get_current_user()
        ))
        window_id = cursor.lastrowid
    
    log_audit(
        "create_release_window",
        "success",
        environment=environment,
        details={
            "window_id": window_id,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "reason": reason
        }
    )
    
    return window_id


def disable_release_window(window_id, cli_role=None):
    """Disable (re-open) a release window."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM release_windows WHERE id = ?", (window_id,))
        row = cursor.fetchone()
        
        if not row:
            raise WindowNotFoundError(window_id)
        
        environment = row["environment"]
        is_enabled = row["is_enabled"] == 1
        
        if not is_enabled:
            log_audit(
                "disable_release_window",
                "failed",
                environment=environment,
                error_reason=f"Window {window_id} is already disabled",
                details={"window_id": window_id}
            )
            return False
        
        if environment == "prod":
            check_permission("disable_release_window", "release-manager", cli_role)
        
        cursor.execute('''
            UPDATE release_windows 
            SET is_enabled = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (window_id,))
    
    log_audit(
        "disable_release_window",
        "success",
        environment=environment,
        details={"window_id": window_id}
    )
    
    return True


def get_release_window(window_id):
    """Get a release window by ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM release_windows WHERE id = ?", (window_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_release_windows(environment=None, include_disabled=False):
    """Get all release windows, optionally filtered by environment."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM release_windows WHERE 1=1"
        params = []
        
        if environment is not None:
            query += " AND environment = ?"
            params.append(environment)
        
        if not include_disabled:
            query += " AND is_enabled = 1"
        
        query += " ORDER BY start_time ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_active_release_window(environment):
    """Check if the given environment has an active (closed) release window at current time.
    
    Returns the active window dict if closed, None otherwise.
    """
    now = datetime.now()
    now_str = now.isoformat()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM release_windows 
            WHERE environment = ? AND is_enabled = 1
            AND start_time <= ? AND end_time >= ?
            ORDER BY start_time ASC
        ''', (environment, now_str, now_str))
        row = cursor.fetchone()
        return dict(row) if row else None


def check_release_window(environment, version=None, override=False, override_reason=None, cli_role=None, action="apply"):
    """Check if release window is closed for an environment.
    
    Returns (can_proceed, window_info, override_info)
    - can_proceed: True if release can proceed (window open or override allowed)
    - window_info: The closed window dict if window is closed, None otherwise
    - override_info: Dict with override details if override was used, None otherwise
    
    Raises:
    - ReleaseWindowError: If window is closed and no override
    - OverridePermissionDeniedError: If override attempted without permission
    """
    window_info = get_active_release_window(environment)
    
    if not window_info:
        return True, None, None
    
    if override:
        current_role = get_role(cli_role)
        if current_role != "release-manager":
            err = OverridePermissionDeniedError(action, "release-manager", current_role)
            log_error(
                action,
                err.code,
                err.message,
                environment=environment,
                version=version,
                details={
                    "window_id": window_info["id"],
                    "override_attempted": True,
                    "role": current_role
                }
            )
            log_audit(
                action,
                "window_override_denied",
                environment=environment,
                version=version,
                error_reason=err.message,
                details={
                    "window_id": window_info["id"],
                    "window_reason": window_info["reason"],
                    "override_reason": override_reason,
                    "role": current_role
                }
            )
            raise err
        
        if not override_reason:
            raise InvalidWindowTimeError("Override reason is required when using --override-window")
        
        override_info = {
            "override_reason": override_reason,
            "overridden_by": get_current_user(),
            "window_id": window_info["id"],
            "window_reason": window_info["reason"]
        }
        
        log_audit(
            action,
            "window_overridden",
            environment=environment,
            version=version,
            details=override_info
        )
        
        return True, window_info, override_info
    
    err = ReleaseWindowError(environment, window_info)
    log_error(
        action,
        err.code,
        err.message,
        environment=environment,
        version=version,
        details={
            "window_id": window_info["id"],
            "window_reason": window_info["reason"],
            "start_time": window_info["start_time"],
            "end_time": window_info["end_time"]
        }
    )
    log_audit(
        action,
        "window_blocked",
        environment=environment,
        version=version,
        error_reason=err.message,
        details={
            "window_id": window_info["id"],
            "window_reason": window_info["reason"],
            "start_time": window_info["start_time"],
            "end_time": window_info["end_time"]
        }
    )
    raise err
