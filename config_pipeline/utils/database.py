import sqlite3
import json
import os
import getpass
from datetime import datetime
from contextlib import contextmanager

from .errors import (
    PipelineNotInitializedError,
    InvalidRoleError,
    PermissionDeniedError,
)


VALID_ENVIRONMENTS = ["dev", "staging", "prod"]
VALID_ROLES = ["developer", "release-manager"]
ROLE_ENV_VAR = "PIPELINE_ROLE"
APPROVAL_REQUIRED_ENVS = ["prod"]
REQUIRED_KEYS = ["app_name", "version", "features", "database", "api_endpoints"]

BATCH_STATUSES = ["pending", "running", "success", "failed", "partial"]
STEP_STATUSES = ["pending", "running", "success", "failed", "skipped"]

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
    CREATE TABLE IF NOT EXISTS batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        notes TEXT
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS batch_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL,
        step_index INTEGER NOT NULL,
        environment TEXT NOT NULL,
        version TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        error_reason TEXT,
        release_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
        UNIQUE(batch_id, step_index)
    )
    ''')

    cursor.execute("PRAGMA table_info(releases)")
    columns = [col[1] for col in cursor.fetchall()]
    if "conflict_reason" not in columns:
        cursor.execute("ALTER TABLE releases ADD COLUMN conflict_reason TEXT")
    if "approved_by" not in columns:
        cursor.execute("ALTER TABLE releases ADD COLUMN approved_by TEXT")

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


def insert_release(version, environment, config_json, status, plan_summary=None, approved_by=None, conflict_reason=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO releases 
            (version, environment, config_json, status, created_by, plan_summary, approved_by, conflict_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            version,
            environment,
            json.dumps(config_json),
            status,
            get_current_user(),
            plan_summary,
            approved_by,
            conflict_reason
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


def create_batch(name, description=None, notes=None):
    """Create a new batch."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO batches (name, description, notes, created_by)
                VALUES (?, ?, ?, ?)
            ''', (name, description, notes, get_current_user()))
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None


def get_batch(batch_id=None, batch_name=None):
    """Get a batch by ID or name."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if batch_id is not None:
            cursor.execute("SELECT * FROM batches WHERE id = ?", (batch_id,))
        elif batch_name is not None:
            cursor.execute("SELECT * FROM batches WHERE name = ?", (batch_name,))
        else:
            return None
        row = cursor.fetchone()
        if row:
            batch = dict(row)
            batch["steps"] = get_batch_steps(batch["id"])
            return batch
        return None


def get_all_batches(limit=100):
    """Get all batches, most recent first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM batches ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        batches = []
        for row in rows:
            batch = dict(row)
            batch["steps"] = get_batch_steps(batch["id"])
            batches.append(batch)
        return batches


def batch_name_exists(name):
    """Check if a batch name already exists."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM batches WHERE name = ?", (name,))
        return cursor.fetchone() is not None


def update_batch_status(batch_id, status, started_at=None, completed_at=None):
    """Update batch status."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params = [status]
        if started_at:
            updates.append("started_at = ?")
            params.append(started_at)
        if completed_at:
            updates.append("completed_at = ?")
            params.append(completed_at)
        params.append(batch_id)
        cursor.execute(
            f"UPDATE batches SET {', '.join(updates)} WHERE id = ?",
            params
        )
        return cursor.rowcount > 0


def update_batch_notes(batch_id, notes):
    """Update batch notes."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE batches SET notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (notes, batch_id))
        return cursor.rowcount > 0


def create_batch_step(batch_id, step_index, environment, version):
    """Create a batch step."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO batch_steps (batch_id, step_index, environment, version)
                VALUES (?, ?, ?, ?)
            ''', (batch_id, step_index, environment, version))
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None


def get_batch_steps(batch_id):
    """Get all steps for a batch, ordered by step index."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM batch_steps WHERE batch_id = ? ORDER BY step_index
        ''', (batch_id,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_batch_step(step_id):
    """Get a specific batch step by ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM batch_steps WHERE id = ?", (step_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_batch_step(step_id, status, error_reason=None, release_id=None):
    """Update batch step status and error reason."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params = [status]
        if error_reason is not None:
            updates.append("error_reason = ?")
            params.append(error_reason)
        if release_id is not None:
            updates.append("release_id = ?")
            params.append(release_id)
        params.append(step_id)
        cursor.execute(
            f"UPDATE batch_steps SET {', '.join(updates)} WHERE id = ?",
            params
        )
        return cursor.rowcount > 0


def reset_failed_batch_steps(batch_id):
    """Reset failed and skipped steps to pending for retry.
    
    Returns the number of steps reset.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE batch_steps
            SET status = 'pending', error_reason = NULL, release_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE batch_id = ? AND status IN ('failed', 'skipped')
        ''', (batch_id,))
        return cursor.rowcount


def get_first_pending_step(batch_id):
    """Get the first pending step for a batch."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM batch_steps
            WHERE batch_id = ? AND status = 'pending'
            ORDER BY step_index LIMIT 1
        ''', (batch_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def set_remaining_steps_skipped(batch_id, from_step_index):
    """Set all remaining steps (after failed step) to skipped."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE batch_steps
            SET status = 'skipped', updated_at = CURRENT_TIMESTAMP
            WHERE batch_id = ? AND step_index > ? AND status = 'pending'
        ''', (batch_id, from_step_index))
        return cursor.rowcount


def compute_batch_status(batch_id):
    """Compute overall batch status based on step states."""
    steps = get_batch_steps(batch_id)
    if not steps:
        return "pending"

    status_counts = {}
    for s in steps:
        status_counts[s["status"]] = status_counts.get(s["status"], 0) + 1

    if status_counts.get("failed", 0) > 0:
        if status_counts.get("success", 0) > 0:
            return "partial"
        return "failed"
    if status_counts.get("success", 0) == len(steps):
        return "success"
    if status_counts.get("running", 0) > 0:
        return "running"
    return "pending"


def export_batch(batch_id):
    """Export a batch and its steps to a JSON-serializable dict."""
    batch = get_batch(batch_id)
    if not batch:
        return None

    steps = []
    for step in batch["steps"]:
        steps.append({
            "step_index": step["step_index"],
            "environment": step["environment"],
            "version": step["version"],
            "status": step["status"],
            "error_reason": step["error_reason"],
        })

    return {
        "batch_export_version": "1.0",
        "exported_at": get_current_time(),
        "exported_by": get_current_user(),
        "batch": {
            "name": batch["name"],
            "description": batch["description"],
            "notes": batch["notes"],
            "status": batch["status"],
            "created_by": batch["created_by"],
            "created_at": batch["created_at"],
            "started_at": batch["started_at"],
            "completed_at": batch["completed_at"],
        },
        "steps": steps,
    }


def check_batch_import_conflicts(export_data, force=False):
    """Check for conflicts when importing a batch.
    
    Returns (conflicts, state_conflicts) lists.
    """
    conflicts = []
    state_conflicts = []

    batch_data = export_data.get("batch", {})
    batch_name = batch_data.get("name")
    steps_data = export_data.get("steps", [])

    if batch_name and batch_name_exists(batch_name):
        conflicts.append(f"Batch name '{batch_name}' already exists")

    for step in steps_data:
        status = step.get("status")
        env = step.get("environment")
        version = step.get("version")

        if status == "success":
            if not has_successful_release(version, env):
                state_conflicts.append(
                    f"Step {step['step_index']} ({version} -> {env}): marked success but "
                    f"no successful release exists in database"
                )
        elif status == "failed":
            if has_successful_release(version, env):
                state_conflicts.append(
                    f"Step {step['step_index']} ({version} -> {env}): marked failed but "
                    f"successful release exists in database"
                )

    return conflicts, state_conflicts


def import_batch(export_data, force=False, role=None):
    """Import a batch from exported JSON data.
    
    Returns (success, message, details).
    """
    batch_data = export_data.get("batch", {})
    batch_name = batch_data.get("name")
    steps_data = export_data.get("steps", [])

    if not batch_name:
        return False, "Batch name is required in export data", {}

    if not steps_data:
        return False, "Batch has no steps to import", {}

    current_role = role or get_role()
    is_release_manager = current_role == "release-manager"

    prod_state_restore = any(
        s.get("environment") == "prod" and s.get("status") in ("success", "failed")
        for s in steps_data
    )
    prod_approval_restore = any(
        s.get("environment") == "prod" for s in steps_data
    )

    if (prod_state_restore or prod_approval_restore) and not is_release_manager:
        return False, (
            "Permission denied: importing batches with prod environment state "
            "requires release-manager role"
        ), {}

    conflicts, state_conflicts = check_batch_import_conflicts(export_data)

    if conflicts and not force:
        return False, f"Import conflicts: {'; '.join(conflicts)}", {"conflicts": conflicts}

    if state_conflicts and not force:
        return False, f"State conflicts: {'; '.join(state_conflicts)}", {"state_conflicts": state_conflicts}

    details = {
        "conflicts_overridden": conflicts if force else [],
        "state_conflicts_overridden": state_conflicts if force else [],
        "steps_imported": 0,
    }

    conn = None
    try:
        db_path = get_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        if force and batch_name_exists(batch_name):
            cursor.execute("DELETE FROM batch_steps WHERE batch_id IN (SELECT id FROM batches WHERE name = ?)", (batch_name,))
            cursor.execute("DELETE FROM batches WHERE name = ?", (batch_name,))

        cursor.execute('''
            INSERT INTO batches (name, description, status, notes, created_by, 
                                created_at, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            batch_name,
            batch_data.get("description"),
            batch_data.get("status", "pending"),
            batch_data.get("notes"),
            batch_data.get("created_by", get_current_user()),
            batch_data.get("created_at"),
            batch_data.get("started_at"),
            batch_data.get("completed_at"),
        ))
        batch_id = cursor.lastrowid

        for step in sorted(steps_data, key=lambda s: s["step_index"]):
            cursor.execute('''
                INSERT INTO batch_steps (batch_id, step_index, environment, version, status, error_reason)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                batch_id,
                step["step_index"],
                step["environment"],
                step["version"],
                step.get("status", "pending"),
                step.get("error_reason"),
            ))
            details["steps_imported"] += 1

        conn.commit()

        log_audit(
            "batch_import",
            "success",
            details={"batch_name": batch_name, "force": force, "role": current_role, **details}
        )

        return True, f"Batch '{batch_name}' imported successfully", details

    except Exception as e:
        if conn:
            conn.rollback()
        log_error(
            "batch_import",
            "IMPORT_ERROR",
            str(e),
            details={"batch_name": batch_name, "force": force}
        )
        raise
    finally:
        if conn:
            conn.close()
