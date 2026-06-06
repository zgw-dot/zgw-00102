import click
import json
import os
from datetime import datetime

from ..utils import (
    log_audit,
    log_error,
    get_audit_logs,
    get_releases,
    get_rollbacks,
    get_all_error_logs,
    get_environment_status,
    get_current_user,
    get_current_time,
    EnvironmentError,
    VALID_ENVIRONMENTS,
)


def validate_environment(env):
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


@click.command()
@click.option("--env", type=click.STRING, default=None, help="Filter by environment")
@click.option("--output", type=click.Path(), default=None, help="Output file path")
@click.option("--format", "output_format", type=click.Choice(["json", "markdown"]), default="json", help="Output format")
def export(env, output, output_format):
    """Export audit data and configuration history."""
    if env is not None:
        try:
            validate_environment(env)
        except EnvironmentError as e:
            log_error("export", e.code, e.message, environment=env)
            log_audit("export", "failed", environment=env, error_reason=e.message)
            raise click.ClickException(e.message)

    try:
        env_status = get_environment_status()
        audit_logs = get_audit_logs(limit=1000)
        releases = get_releases(environment=env, limit=1000)
        rollbacks = get_rollbacks(environment=env, limit=1000)
        error_logs = get_all_error_logs(limit=1000)
    except Exception as e:
        log_error("export", "DATA_READ_ERROR", str(e), environment=env)
        log_audit("export", "failed", environment=env, error_reason=str(e))
        raise click.ClickException(f"Failed to read data: {e}")

    if env:
        audit_logs = [a for a in audit_logs if a["environment"] == env]
        error_logs = [e for e in error_logs if e["environment"] == env]

    export_data = {
        "export_metadata": {
            "exported_at": get_current_time(),
            "exported_by": get_current_user(),
            "environment_filter": env,
            "record_counts": {
                "audit_logs": len(audit_logs),
                "releases": len(releases),
                "rollbacks": len(rollbacks),
                "error_logs": len(error_logs),
            }
        },
        "environment_status": env_status,
        "plan_summaries": [],
        "audit_logs": [],
        "releases": [],
        "rollbacks": [],
        "error_logs": [],
    }

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
            "applied_at": release["created_at"],
            "plan_summary": plan_summary,
        })

        export_data["releases"].append({
            "id": release["id"],
            "version": release["version"],
            "environment": release["environment"],
            "status": release["status"],
            "created_by": release["created_by"],
            "created_at": release["created_at"],
        })

    for audit in audit_logs:
        try:
            details = json.loads(audit["details"]) if audit["details"] else None
        except (json.JSONDecodeError, TypeError):
            details = None

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
        details={"format": output_format, "output": output, "record_count": len(audit_logs)}
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
    lines.append("")
    
    lines.append("## Environment Status")
    lines.append("")
    lines.append("| Environment | Current Version | Updated At |")
    lines.append("|-------------|-----------------|------------|")
    for env in data["environment_status"]:
        lines.append(f"| {env['name']} | {env['current_version'] or 'None'} | {env['updated_at']} |")
    lines.append("")
    
    lines.append("## Deployment Plan Summaries")
    lines.append("")
    lines.append("| ID | Version | Env | Status | Operator | Applied At | Changes |")
    lines.append("|----|---------|-----|--------|----------|------------|---------|")
    for plan in data["plan_summaries"]:
        summary = plan["plan_summary"]
        changes = f"+{summary.get('added_count', 0)} -{summary.get('removed_count', 0)} ~{summary.get('modified_count', 0)}"
        lines.append(f"| {plan['release_id']} | {plan['version']} | {plan['environment']} | {plan['status']} | {plan['applied_by']} | {plan['applied_at']} | {changes} |")
    lines.append("")
    
    lines.append("## Audit Log")
    lines.append("")
    lines.append("| ID | Action | Env | Version | Status | Operator | Timestamp | Error Reason |")
    lines.append("|----|--------|-----|---------|--------|----------|-----------|--------------|")
    for audit in data["audit_logs"]:
        error = audit.get("error_reason") or ""
        lines.append(f"| {audit['id']} | {audit['action']} | {audit['environment'] or 'N/A'} | {audit['version'] or 'N/A'} | {audit['status']} | {audit['operator']} | {audit['timestamp']} | {error} |")
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
