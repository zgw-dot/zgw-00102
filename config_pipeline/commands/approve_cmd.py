import click

from ..utils import (
    log_audit,
    log_error,
    get_role,
    get_approval,
    get_pending_approvals,
    approve_version,
    reject_approval,
    config_exists,
    requires_approval,
    EnvironmentError,
    VersionNotFoundError,
    ApprovalNotFoundError,
    AlreadyApprovedError,
    VALID_ENVIRONMENTS,
)


def validate_environment(env):
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


@click.command()
@click.argument("version")
@click.argument("environment")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--notes", type=click.STRING, default=None, help="Approval notes")
def approve(version, environment, role, notes):
    """Approve a version for release to production."""
    try:
        validate_environment(environment)
    except EnvironmentError as e:
        log_error("approve", e.code, e.message, environment=environment, version=version)
        log_audit("approve", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    if not requires_approval(environment):
        msg = f"Environment {environment} does not require approval. Use 'pipeline apply' directly."
        log_error("approve", "APPROVAL_NOT_REQUIRED", msg, environment=environment, version=version)
        log_audit("approve", "failed", environment=environment, version=version, error_reason=msg)
        raise click.ClickException(msg)

    if not config_exists(version):
        err = VersionNotFoundError(version)
        log_error("approve", err.code, err.message, environment=environment, version=version)
        log_audit("approve", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("approve", e.code, e.message, environment=environment, version=version)
        log_audit("approve", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    approval = get_approval(version, environment)
    if not approval:
        err = ApprovalNotFoundError(version, environment)
        log_error("approve", err.code, err.message, environment=environment, version=version)
        log_audit("approve", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    if approval["status"] == "approved":
        err = AlreadyApprovedError(version, environment)
        log_error("approve", err.code, err.message, environment=environment, version=version)
        log_audit("approve", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    if approval["status"] != "pending":
        msg = f"Approval for version {version} in {environment} is in '{approval['status']}' state, not pending."
        log_error("approve", "INVALID_APPROVAL_STATE", msg, environment=environment, version=version)
        log_audit("approve", "failed", environment=environment, version=version, error_reason=msg)
        raise click.ClickException(msg)

    try:
        success = approve_version(version, environment, cli_role=role, notes=notes)
    except Exception as e:
        log_error("approve", e.code, e.message, environment=environment, version=version)
        log_audit("approve", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    if not success:
        msg = f"Failed to approve version {version} for {environment}"
        log_error("approve", "APPROVE_FAILED", msg, environment=environment, version=version)
        log_audit("approve", "failed", environment=environment, version=version, error_reason=msg)
        raise click.ClickException(msg)

    click.echo("=" * 60)
    click.echo(f"VERSION APPROVED")
    click.echo("=" * 60)
    click.echo(f"Version:        {version}")
    click.echo(f"Environment:    {environment}")
    click.echo(f"Approved by:    current user")
    if notes:
        click.echo(f"Notes:          {notes}")
    click.echo(f"Status:         ready for release")
    click.echo("=" * 60)

    log_audit(
        "approve",
        "success",
        environment=environment,
        version=version,
        details={"role": current_role, "notes": notes, "requested_by": approval["requested_by"]}
    )


@click.command()
@click.argument("version")
@click.argument("environment")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--reason", type=click.STRING, default=None, help="Rejection reason")
def reject(version, environment, role, reason):
    """Reject a pending approval."""
    try:
        validate_environment(environment)
    except EnvironmentError as e:
        log_error("reject", e.code, e.message, environment=environment, version=version)
        log_audit("reject", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    if not requires_approval(environment):
        msg = f"Environment {environment} does not require approval."
        log_error("reject", "APPROVAL_NOT_REQUIRED", msg, environment=environment, version=version)
        log_audit("reject", "failed", environment=environment, version=version, error_reason=msg)
        raise click.ClickException(msg)

    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("reject", e.code, e.message, environment=environment, version=version)
        log_audit("reject", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    approval = get_approval(version, environment)
    if not approval:
        err = ApprovalNotFoundError(version, environment)
        log_error("reject", err.code, err.message, environment=environment, version=version)
        log_audit("reject", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    if approval["status"] != "pending":
        msg = f"Approval for version {version} in {environment} is in '{approval['status']}' state, not pending."
        log_error("reject", "INVALID_APPROVAL_STATE", msg, environment=environment, version=version)
        log_audit("reject", "failed", environment=environment, version=version, error_reason=msg)
        raise click.ClickException(msg)

    try:
        success = reject_approval(version, environment, cli_role=role, conflict_reason=reason)
    except Exception as e:
        log_error("reject", e.code, e.message, environment=environment, version=version)
        log_audit("reject", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    if not success:
        msg = f"Failed to reject approval for version {version} in {environment}"
        log_error("reject", "REJECT_FAILED", msg, environment=environment, version=version)
        log_audit("reject", "failed", environment=environment, version=version, error_reason=msg)
        raise click.ClickException(msg)

    click.echo("=" * 60)
    click.echo(f"APPROVAL REJECTED")
    click.echo("=" * 60)
    click.echo(f"Version:        {version}")
    click.echo(f"Environment:    {environment}")
    click.echo(f"Rejected by:    current user")
    if reason:
        click.echo(f"Reason:         {reason}")
    click.echo("=" * 60)

    log_audit(
        "reject",
        "success",
        environment=environment,
        version=version,
        details={"role": current_role, "reason": reason, "requested_by": approval["requested_by"]}
    )
