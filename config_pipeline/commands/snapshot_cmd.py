import click
import json
import os

from ..utils import (
    log_audit,
    log_error,
    get_snapshot_data,
    check_snapshot_conflicts,
    import_snapshot,
    get_role,
    InvalidRoleError,
)


@click.group()
def snapshot():
    """Manage configuration snapshots for backup and restore."""
    pass


@snapshot.command("export")
@click.option("--output", type=click.Path(), default=None, help="Output file path")
def snapshot_export(output):
    """Export current configuration state as a snapshot.

    Exports config versions, environment pointers, approvals, and lock status as JSON.
    """
    try:
        snapshot_data = get_snapshot_data()
    except Exception as e:
        log_error("snapshot export", "SNAPSHOT_READ_ERROR", str(e))
        log_audit("snapshot_export", "failed", error_reason=f"Failed to read snapshot data: {e}")
        raise click.ClickException(f"Failed to read snapshot data: {e}")

    content = json.dumps(snapshot_data, indent=2, ensure_ascii=False)

    if output:
        output_path = os.path.abspath(output)
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
            click.echo(f"Snapshot exported to {output_path}")
            click.echo(f"  Config versions: {len(snapshot_data['configs'])}")
            click.echo(f"  Environments: {len(snapshot_data['environments'])}")
            click.echo(f"  Approvals: {len(snapshot_data['approvals'])}")
            click.echo(f"  Environment locks: {len(snapshot_data['environment_locks'])}")
        except Exception as e:
            log_error("snapshot export", "FILE_WRITE_ERROR", str(e), details={"output": output})
            log_audit("snapshot_export", "failed", error_reason=f"Failed to write snapshot file: {e}")
            raise click.ClickException(f"Failed to write snapshot file: {e}")
    else:
        click.echo(content)

    log_audit(
        "snapshot_export",
        "success",
        details={
            "output": output,
            "configs_count": len(snapshot_data["configs"]),
            "environments_count": len(snapshot_data["environments"]),
            "approvals_count": len(snapshot_data["approvals"]),
            "locks_count": len(snapshot_data["environment_locks"]),
        }
    )


@snapshot.command("import")
@click.argument("file_path", type=click.Path(exists=True, readable=True))
@click.option("--force", is_flag=True, default=False, help="Force overwrite existing data on conflict")
@click.option("--role", type=click.Choice(["developer", "release-manager"]), default=None, help="Role for permission checks")
def snapshot_import(file_path, force, role):
    """Import a configuration snapshot from a JSON file.

    Restores config versions, environment pointers, approvals, and lock status.
    Default behavior rejects conflicts; use --force to overwrite.

    Permission: developer cannot restore prod lock and approval status;
    only release-manager can restore everything.
    """
    try:
        resolved_role = get_role(role)
    except InvalidRoleError as e:
        log_error("snapshot import", e.code, e.message)
        log_audit("snapshot_import", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            snapshot_data = json.load(f)
    except json.JSONDecodeError as e:
        log_error("snapshot import", "INVALID_JSON", str(e), details={"file": file_path})
        log_audit("snapshot_import", "failed", error_reason=f"Invalid JSON: {e}")
        raise click.ClickException(f"Invalid JSON file: {e}")

    required_keys = ["snapshot_metadata", "configs", "environments", "approvals", "environment_locks"]
    missing_keys = [k for k in required_keys if k not in snapshot_data]
    if missing_keys:
        error_msg = f"Invalid snapshot format. Missing required keys: {', '.join(missing_keys)}"
        log_error("snapshot import", "INVALID_SNAPSHOT_FORMAT", error_msg, details={"file": file_path})
        log_audit("snapshot_import", "failed", error_reason=error_msg)
        raise click.ClickException(error_msg)

    try:
        conflicts = check_snapshot_conflicts(snapshot_data)
    except Exception as e:
        log_error("snapshot import", "CONFLICT_CHECK_ERROR", str(e), details={"file": file_path})
        log_audit("snapshot_import", "failed", error_reason=f"Conflict check failed: {e}")
        raise click.ClickException(f"Conflict check failed: {e}")

    if conflicts and not force:
        conflict_msg = "Conflicts detected:\n  " + "\n  ".join(conflicts)
        conflict_msg += "\n\nUse --force to overwrite existing data."
        log_error(
            "snapshot import",
            "CONFLICTS_DETECTED",
            conflict_msg,
            details={"file": file_path, "conflicts": conflicts}
        )
        log_audit(
            "snapshot_import",
            "failed",
            error_reason="Conflicts detected",
            details={"conflicts": conflicts}
        )
        raise click.ClickException(conflict_msg)

    if conflicts and force:
        click.echo("Conflicts detected, using --force to overwrite:")
        for c in conflicts:
            click.echo(f"  - {c}")
        click.echo("")

    try:
        success, message, details = import_snapshot(snapshot_data, force=force, role=resolved_role)
    except Exception as e:
        log_error("snapshot import", "IMPORT_ERROR", str(e), details={"file": file_path})
        log_audit("snapshot_import", "failed", error_reason=f"Import failed: {e}")
        raise click.ClickException(f"Import failed: {e}")

    click.echo(f"Snapshot imported successfully from {os.path.abspath(file_path)}")
    click.echo(f"  Role: {resolved_role}")
    click.echo(f"  Configs imported: {details['configs_imported']}")
    if details["configs_skipped"]:
        click.echo(f"  Configs skipped: {details['configs_skipped']}")
    click.echo(f"  Environments updated: {details['environments_updated']}")
    click.echo(f"  Approvals imported: {details['approvals_imported']}")
    if details["approvals_skipped"]:
        click.echo(f"  Approvals skipped: {details['approvals_skipped']}")
    click.echo(f"  Locks updated: {details['locks_updated']}")
    if details["locks_skipped"]:
        click.echo(f"  Locks skipped: {details['locks_skipped']}")
    if details["permissions_applied"]:
        click.echo("  Permission restrictions applied:")
        for p in details["permissions_applied"]:
            click.echo(f"    - {p}")

    log_audit(
        "snapshot_import",
        "success",
        details={
            "file": file_path,
            "role": resolved_role,
            "force": force,
            **details,
        }
    )
