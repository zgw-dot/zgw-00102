import click
import json
from tabulate import tabulate

from ..utils import (
    log_audit,
    log_error,
    get_audit_logs,
    get_releases,
    get_rollbacks,
    get_environment_status,
    EnvironmentError,
    VALID_ENVIRONMENTS,
)


def validate_environment(env):
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


@click.command()
@click.option("--env", type=click.STRING, default=None, help="Filter by environment")
@click.option("--type", "history_type", type=click.Choice(["all", "releases", "rollbacks", "audit"]), default="all", help="Type of history to show")
@click.option("--limit", type=click.INT, default=50, help="Maximum number of entries to show")
def history(env, history_type, limit):
    """Show deployment and audit history."""
    if env is not None:
        try:
            validate_environment(env)
        except EnvironmentError as e:
            log_error("history", e.code, e.message, environment=env)
            log_audit("history", "failed", environment=env, error_reason=e.message)
            raise click.ClickException(e.message)

    env_status = get_environment_status()
    audits = get_audit_logs(limit=limit)
    releases = get_releases(environment=env, limit=limit)
    rollbacks = get_rollbacks(environment=env, limit=limit)

    if env:
        audits = [a for a in audits if a["environment"] == env]

    click.echo("=" * 80)
    click.echo("ENVIRONMENT STATUS")
    click.echo("=" * 80)
    env_table = []
    for env_data in env_status:
        env_table.append([
            env_data["name"],
            env_data["current_version"] or "None",
            env_data["updated_at"]
        ])
    click.echo(tabulate(env_table, headers=["Environment", "Current Version", "Updated At"], tablefmt="simple"))
    click.echo()

    if history_type in ["all", "releases"]:
        click.echo("=" * 80)
        click.echo("RELEASE HISTORY")
        click.echo("=" * 80)
        if releases:
            release_table = []
            for rel in releases:
                summary = json.loads(rel["plan_summary"]) if rel["plan_summary"] else {}
                release_table.append([
                    rel["id"],
                    rel["version"],
                    rel["environment"],
                    rel["status"],
                    summary.get("total_changes", "N/A"),
                    rel["created_by"],
                    rel["created_at"]
                ])
            click.echo(tabulate(
                release_table,
                headers=["ID", "Version", "Env", "Status", "Changes", "User", "Created At"],
                tablefmt="simple"
            ))
        else:
            click.echo("No releases found.")
        click.echo()

    if history_type in ["all", "rollbacks"]:
        click.echo("=" * 80)
        click.echo("ROLLBACK HISTORY")
        click.echo("=" * 80)
        if rollbacks:
            rollback_table = []
            for rb in rollbacks:
                rollback_table.append([
                    rb["id"],
                    rb["environment"],
                    rb["from_version"],
                    rb["to_version"],
                    rb.get("reason", "N/A"),
                    rb["created_by"],
                    rb["created_at"]
                ])
            click.echo(tabulate(
                rollback_table,
                headers=["ID", "Env", "From", "To", "Reason", "User", "Created At"],
                tablefmt="simple"
            ))
        else:
            click.echo("No rollbacks found.")
        click.echo()

    if history_type in ["all", "audit"]:
        click.echo("=" * 80)
        click.echo("AUDIT LOG")
        click.echo("=" * 80)
        if audits:
            audit_table = []
            for audit in audits:
                audit_table.append([
                    audit["id"],
                    audit["action"],
                    audit["environment"] or "N/A",
                    audit["version"] or "N/A",
                    audit["status"],
                    audit["created_by"],
                    audit["created_at"],
                    (audit["error_reason"] or "")[:50]
                ])
            click.echo(tabulate(
                audit_table,
                headers=["ID", "Action", "Env", "Version", "Status", "User", "Created At", "Error"],
                tablefmt="simple"
            ))
        else:
            click.echo("No audit logs found.")
        click.echo()

    log_audit(
        "history",
        "success",
        environment=env,
        details={"type": history_type, "limit": limit}
    )
