import click
from tabulate import tabulate

from ..utils import (
    log_audit,
    log_error,
    get_role,
    create_pending_approval,
    get_pending_approvals,
    get_all_approvals,
    get_approval,
    config_exists,
    requires_approval,
    has_successful_release,
    EnvironmentError,
    VersionNotFoundError,
    StagingRequiredError,
    PendingApprovalExistsError,
    VALID_ENVIRONMENTS,
)


def validate_environment(env):
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


@click.command(name="pending")
@click.argument("version")
@click.argument("environment")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--notes", type=click.STRING, default=None, help="Request notes")
def pending_request(version, environment, role, notes):
    """Mark a version as pending approval for release to production."""
    try:
        validate_environment(environment)
    except EnvironmentError as e:
        log_error("pending", e.code, e.message, environment=environment, version=version)
        log_audit("pending", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    if not requires_approval(environment):
        msg = f"Environment {environment} does not require approval. Use 'pipeline apply' directly."
        log_error("pending", "APPROVAL_NOT_REQUIRED", msg, environment=environment, version=version)
        log_audit("pending", "failed", environment=environment, version=version, error_reason=msg)
        raise click.ClickException(msg)

    if not config_exists(version):
        err = VersionNotFoundError(version)
        log_error("pending", err.code, err.message, environment=environment, version=version)
        log_audit("pending", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    if not has_successful_release(version, "staging"):
        err = StagingRequiredError(version)
        log_error("pending", err.code, err.message, environment=environment, version=version)
        log_audit("pending", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("pending", e.code, e.message, environment=environment, version=version)
        log_audit("pending", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    existing_approval = get_approval(version, environment)
    if existing_approval:
        if existing_approval["status"] == "pending":
            err = PendingApprovalExistsError(version, environment)
            log_error("pending", err.code, err.message, environment=environment, version=version)
            log_audit("pending", "failed", environment=environment, version=version, error_reason=err.message)
            raise click.ClickException(err.message)
        elif existing_approval["status"] == "approved":
            msg = f"Version {version} is already approved for {environment}. Use 'pipeline apply' to release."
            log_error("pending", "ALREADY_APPROVED", msg, environment=environment, version=version)
            log_audit("pending", "failed", environment=environment, version=version, error_reason=msg)
            raise click.ClickException(msg)

    success = create_pending_approval(version, environment, notes=notes)
    if not success:
        msg = f"Failed to create pending approval for version {version} in {environment}"
        log_error("pending", "PENDING_FAILED", msg, environment=environment, version=version)
        log_audit("pending", "failed", environment=environment, version=version, error_reason=msg)
        raise click.ClickException(msg)

    click.echo("=" * 60)
    click.echo(f"PENDING APPROVAL CREATED")
    click.echo("=" * 60)
    click.echo(f"Version:        {version}")
    click.echo(f"Environment:    {environment}")
    click.echo(f"Requested by:   current user")
    if notes:
        click.echo(f"Notes:          {notes}")
    click.echo(f"Status:         awaiting approval from release-manager")
    click.echo("=" * 60)

    log_audit(
        "pending",
        "success",
        environment=environment,
        version=version,
        details={"role": current_role, "notes": notes}
    )


@click.command(name="pending-list")
@click.option("--env", type=click.STRING, default=None, help="Filter by environment")
@click.option("--all", "show_all", is_flag=True, help="Show all approvals (not just pending)")
def pending_list(env, show_all):
    """List pending approvals or all approval history."""
    if env is not None:
        try:
            validate_environment(env)
        except EnvironmentError as e:
            log_error("pending-list", e.code, e.message, environment=env)
            log_audit("pending-list", "failed", environment=env, error_reason=e.message)
            raise click.ClickException(e.message)

    try:
        if show_all:
            approvals = get_all_approvals(environment=env, limit=100)
        else:
            approvals = get_pending_approvals(environment=env)
    except Exception as e:
        log_error("pending-list", "DATA_READ_ERROR", str(e), environment=env)
        log_audit("pending-list", "failed", environment=env, error_reason=str(e))
        raise click.ClickException(f"Failed to read approvals: {e}")

    title = "ALL APPROVALS" if show_all else "PENDING APPROVALS"
    click.echo("=" * 100)
    click.echo(title)
    click.echo("=" * 100)

    if approvals:
        table = []
        for app in approvals:
            table.append([
                app["id"],
                app["version"],
                app["environment"],
                app["status"],
                app["requested_by"],
                app["requested_at"],
                app.get("approved_by") or "N/A",
                app.get("approved_at") or "N/A",
                app.get("conflict_reason") or "N/A",
            ])
        click.echo(tabulate(
            table,
            headers=["ID", "Version", "Env", "Status", "Requested By", "Requested At", "Approved By", "Approved At", "Conflict Reason"],
            tablefmt="simple"
        ))
    else:
        click.echo("No approvals found.")
    click.echo()

    log_audit(
        "pending-list",
        "success",
        environment=env,
        details={"show_all": show_all, "approval_count": len(approvals)}
    )
