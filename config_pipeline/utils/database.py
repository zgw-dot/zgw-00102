import sqlite3
import json
import os
import getpass
from datetime import datetime
from contextlib import contextmanager

from .errors import PipelineNotInitializedError


VALID_ENVIRONMENTS = ["dev", "staging", "prod"]
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

    for env in VALID_ENVIRONMENTS:
        cursor.execute(
            "INSERT OR IGNORE INTO environments (name, current_version) VALUES (?, NULL)",
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


def insert_release(version, environment, config_json, status, plan_summary=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO releases 
            (version, environment, config_json, status, created_by, plan_summary)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            version,
            environment,
            json.dumps(config_json),
            status,
            get_current_user(),
            plan_summary
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
