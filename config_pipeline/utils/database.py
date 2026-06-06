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
