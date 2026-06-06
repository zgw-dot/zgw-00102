import click
import json
import os
from datetime import datetime

from ..utils import (
    log_audit,
    log_error,
    get_audit_logs_filtered,
    get_releases,
    get_rollbacks,
    get_all_error_logs,
    get_environment_status,
    get_all_environment_locks,
    get_all_approvals,
    get_current_user,
    get_current_time,
    EnvironmentError,
    VALID_ENVIRONMENTS,
)

VALID_STATUS = ["success", "failed"]


def validate_environment(env):
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


def validate_status(status):
    if status is not None and status not in VALID_STATUS:
        raise click.BadParameter(
            f"Invalid status '{status}'. Must be one of: {', '.join(VALID_STATUS)}"
        )
    return status


def parse_since(since_str):
    if since_str is None:
        return None, None

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(since_str, fmt)
            if fmt == "%Y-%m-%d":
                dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            db_format = dt.strftime("%Y-%m-%d %H:%M:%S")
            return db_format, dt
        except ValueError:
            continue

    return None, None


@click.command()
@click.option("--env", type=click.STRING, default=None, help="Filter by environment")
@click.option("--status", type=click.STRING, default=None, help="Filter by audit status (success/failed)")
@click.option("--since", type=click.STRING, default=None, help="Filter by timestamp (YYYY-MM-DD or ISO datetime)")
@click.option("--output", type=click.Path(), default=None, help="Output file path")
@click.option("--format", "output_format", type=click.Choice(["json", "markdown"]), default="json", help="Output format")
def export(env, status, since, output, output_format):
    """Export audit data and configuration history."""
    if env is not None:
        try:
            validate_environment(env)
        except EnvironmentError as e:
            log_error("export", e.code, e.message, environment=env)
            log_audit("export", "failed", environment=env, error_reason=e.message)
            raise click.ClickException(e.message)

    try:
        validate_status(status)
    except click.BadParameter as e:
        log_error("export", "INVALID_STATUS", str(e), environment=env)
        log_audit("export", "failed", environment=env, error_reason=str(e))
        raise e

    since_db, since_dt = parse_since(since)
    if since is not None and since_db is None:
        error_msg = f"Invalid --since format '{since}'. Expected YYYY-MM-DD or ISO datetime (e.g., 2024-01-01 or 2024-01-01T12:00:00)"
        log_error("export", "INVALID_SINCE_FORMAT", error_msg, environment=env, details={"since_input": since})
        log_audit("export", "failed", environment=env, error_reason=error_msg, details={"since_input": since})
        raise click.ClickException(error_msg)

    try:
        env_status = get_environment_status()
        locks = get_all_environment_locks()
        approvals = get_all_approvals(environment=env, limit=1000)
        audit_logs = get_audit_logs_filtered(environment=env, status=status, since=since_db, limit=1000)
        releases = get_releases(environment=env, limit=1000)
        rollbacks = get_rollbacks(environment=env, limit=1000)
        error_logs = get_all_error_logs(limit=1000)
    except Exception as e:
        log_error("export", "DATA_READ_ERROR", str(e), environment=env)
        log_audit("export", "failed", environment=env, error_reason=str(e))
        raise click.ClickException(f"Failed to read data: {e}")

    if env:
        error_logs = [e for e in error_logs if e["environment"] == env]

    export_data = {
        "export_metadata": {
            "exported_at": get_current_time(),
            "exported_by": get_current_user(),
            "environment_filter": env,
            "status_filter": status,
            "since_filter": since_db,
            "record_counts": {
                "audit_logs": len(audit_logs),
                "releases": len(releases),
                "rollbacks": len(rollbacks),
                "error_logs": len(error_logs),
                "approvals": len(approvals),
                "locks": len(locks),
            }
        },
        "environment_status": env_status,
        "environment_locks": [],
        "approvals": [],
        "plan_summaries": [],
        "audit_logs": [],
        "releases": [],
        "rollbacks": [],
        "error_logs": [],
    }

    for lock in locks:
        export_data["environment_locks"].append({
            "environment": lock["environment"],
            "is_locked": lock["is_locked"] == 1,
            "lock_reason": lock["lock_reason"],
            "locked_by": lock["locked_by"],
            "locked_at": lock["locked_at"],
            "conflict_reason": lock.get("conflict_reason"),
        })

    for app in approvals:
        export_data["approvals"].append({
            "id": app["id"],
            "version": app["version"],
            "environment": app["environment"],
            "status": app["status"],
            "requested_by": app["requested_by"],
            "requested_at": app["requested_at"],
            "approved_by": app.get("approved_by"),
            "approved_at": app.get("approved_at"),
            "notes": app.get("notes"),
            "conflict_reason": app.get("conflict_reason"),
        })

    for release in releases:
        try:
            plan_summary = json.loads(release["plan_summary"]) if release["plan_summary"] else {}
        except (json.JSONDecodeError, TypeError):
            plan_summary = {}

        export_data["plan_summaries"].append({
            "release_id": release["id"],
            "version": release["version"],
            "environment": release["environment"],
            "status": release["status"],
            "applied_by": release["created_by"],
            "approved_by": release.get("approved_by"),
            "applied_at": release["created_at"],
            "conflict_reason": release.get("conflict_reason"),
            "plan_summary": plan_summary,
        })

        export_data["releases"].append({
            "id": release["id"],
            "version": release["version"],
            "environment": release["environment"],
            "status": release["status"],
            "created_by": release["created_by"],
            "approved_by": release.get("approved_by"),
            "conflict_reason": release.get("conflict_reason"),
            "created_at": release["created_at"],
        })

    for audit in audit_logs:
        try:
            details = json.loads(audit["details"]) if audit["details"] else None
        except (json.JSONDecodeError, TypeError):
            details = None

        conflict_reason = None
        if details and isinstance(details, dict):
            conflict_reason = details.get("conflict_reason")

        export_data["audit_logs"].append({
            "id": audit["id"],
            "action": audit["action"],
            "environment": audit["environment"],
            "version": audit["version"],
            "status": audit["status"],
            "operator": audit["created_by"],
            "timestamp": audit["created_at"],
            "details": details,
            "error_reason": audit["error_reason"],
            "conflict_reason": conflict_reason,
        })

    for rb in rollbacks:
        export_data["rollbacks"].append({
            "id": rb["id"],
            "environment": rb["environment"],
            "from_version": rb["from_version"],
            "to_version": rb["to_version"],
            "reason": rb["reason"],
            "operator": rb["created_by"],
            "timestamp": rb["created_at"],
        })

    for err in error_logs:
        try:
            details = json.loads(err["details"]) if err["details"] else None
        except (json.JSONDecodeError, TypeError):
            details = None

        export_data["error_logs"].append({
            "id": err["id"],
            "command": err["command"],
            "error_code": err["error_code"],
            "error_message": err["error_message"],
            "environment": err["environment"],
            "version": err["version"],
            "operator": err["created_by"],
            "timestamp": err["created_at"],
            "details": details,
        })

    if output_format == "json":
        content = json.dumps(export_data, indent=2, ensure_ascii=False)
    else:
        content = _format_markdown(export_data)

    if output:
        output_path = os.path.abspath(output)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        click.echo(f"Audit data exported to {output_path}")
    else:
        click.echo(content)

    log_audit(
        "export",
        "success",
        environment=env,
        details={"format": output_format, "output": output, "record_count": len(audit_logs), "status_filter": status, "since_filter": since_db}
    )


def _format_markdown(data):
    """Format export data as markdown."""
    lines = []
    
    lines.append("# Configuration Pipeline Audit Export")
    lines.append("")
    lines.append(f"**Exported At:** {data['export_metadata']['exported_at']}")
    lines.append(f"**Exported By:** {data['export_metadata']['exported_by']}")
    if data['export_metadata']['environment_filter']:
        lines.append(f"**Environment Filter:** {data['export_metadata']['environment_filter']}")
    if data['export_metadata'].get('status_filter'):
        lines.append(f"**Status Filter:** {data['export_metadata']['status_filter']}")
    if data['export_metadata'].get('since_filter'):
        lines.append(f"**Since Filter:** {data['export_metadata']['since_filter']}")
    lines.append("")
    
    lines.append("## Environment Status")
    lines.append("")
    lines.append("| Environment | Current Version | Updated At |")
    lines.append("|-------------|-----------------|------------|")
    for env in data["environment_status"]:
        lines.append(f"| {env['name']} | {env['current_version'] or 'None'} | {env['updated_at']} |")
    lines.append("")
    
    lines.append("## Environment Lock Status")
    lines.append("")
    lines.append("| Environment | Status | Lock Reason | Locked By | Locked At | Conflict Reason |")
    lines.append("|-------------|--------|-------------|-----------|-----------|-----------------|")
    for lock in data["environment_locks"]:
        status = "LOCKED" if lock["is_locked"] else "UNLOCKED"
        reason = lock.get("lock_reason") or "N/A"
        locked_by = lock.get("locked_by") or "N/A"
        locked_at = lock.get("locked_at") or "N/A"
        conflict = lock.get("conflict_reason") or "N/A"
        lines.append(f"| {lock['environment']} | {status} | {reason} | {locked_by} | {locked_at} | {conflict} |")
    lines.append("")
    
    if data["approvals"]:
        lines.append("## Approvals")
        lines.append("")
        lines.append("| ID | Version | Env | Status | Requested By | Requested At | Approved By | Approved At | Conflict Reason |")
        lines.append("|----|---------|-----|--------|--------------|--------------|-------------|-------------|-----------------|")
        for app in data["approvals"]:
            approved_by = app.get("approved_by") or "N/A"
            approved_at = app.get("approved_at") or "N/A"
            conflict = app.get("conflict_reason") or "N/A"
            lines.append(f"| {app['id']} | {app['version']} | {app['environment']} | {app['status']} | {app['requested_by']} | {app['requested_at']} | {approved_by} | {approved_at} | {conflict} |")
        lines.append("")
    
    lines.append("## Deployment Plan Summaries")
    lines.append("")
    lines.append("| ID | Version | Env | Status | Operator | Approved By | Applied At | Changes | Conflict Reason |")
    lines.append("|----|---------|-----|--------|----------|-------------|------------|---------|-----------------|")
    for plan in data["plan_summaries"]:
        summary = plan["plan_summary"]
        changes = f"+{summary.get('added_count', 0)} -{summary.get('removed_count', 0)} ~{summary.get('modified_count', 0)}"
        approved_by = plan.get("approved_by") or "N/A"
        conflict = plan.get("conflict_reason") or "N/A"
        lines.append(f"| {plan['release_id']} | {plan['version']} | {plan['environment']} | {plan['status']} | {plan['applied_by']} | {approved_by} | {plan['applied_at']} | {changes} | {conflict} |")
    lines.append("")
    
    lines.append("## Audit Log")
    lines.append("")
    lines.append("| ID | Action | Env | Version | Status | Operator | Timestamp | Error Reason | Conflict Reason | Details |")
    lines.append("|----|--------|-----|---------|--------|----------|-----------|--------------|-----------------|---------|")
    for audit in data["audit_logs"]:
        error = audit.get("error_reason") or ""
        conflict = audit.get("conflict_reason") or "N/A"
        details = audit.get("details")
        if details is not None:
            details_str = json.dumps(details, ensure_ascii=False)
            if len(details_str) > 50:
                details_str = details_str[:47] + "..."
        else:
            details_str = "N/A"
        lines.append(f"| {audit['id']} | {audit['action']} | {audit['environment'] or 'N/A'} | {audit['version'] or 'N/A'} | {audit['status']} | {audit['operator']} | {audit['timestamp']} | {error} | {conflict} | {details_str} |")
    lines.append("")
    
    if data["error_logs"]:
        lines.append("## Error Logs")
        lines.append("")
        lines.append("| ID | Command | Error Code | Message | Env | Version | Operator | Timestamp |")
        lines.append("|----|---------|------------|---------|-----|---------|----------|-----------|")
        for err in data["error_logs"]:
            msg = (err["error_message"][:50] + "...") if len(err["error_message"]) > 50 else err["error_message"]
            lines.append(f"| {err['id']} | {err['command']} | {err['error_code']} | {msg} | {err['environment'] or 'N/A'} | {err['version'] or 'N/A'} | {err['operator']} | {err['timestamp']} |")
        lines.append("")
    
    if data["rollbacks"]:
        lines.append("## Rollbacks")
        lines.append("")
        lines.append("| ID | Env | From | To | Reason | Operator | Timestamp |")
        lines.append("|----|-----|------|----|--------|----------|-----------|")
        for rb in data["rollbacks"]:
            reason = rb.get("reason") or "N/A"
            lines.append(f"| {rb['id']} | {rb['environment']} | {rb['from_version']} | {rb['to_version']} | {reason} | {rb['operator']} | {rb['timestamp']} |")
        lines.append("")
    
    return "\n".join(lines)
