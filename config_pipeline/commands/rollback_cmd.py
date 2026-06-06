import click
import json

from ..utils import (
    log_audit,
    log_error,
    get_current_version,
    get_release,
    has_successful_release,
    set_current_version,
    insert_release,
    insert_rollback,
    get_role,
    is_environment_locked,
    get_environment_lock,
    EnvironmentError,
    VersionNotFoundError,
    EnvironmentLockedError,
    VALID_ENVIRONMENTS,
    compute_diff,
    format_diff,
    generate_plan_summary,
)


def validate_environment(env):
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


@click.command()
@click.argument("environment")
@click.argument("target_version")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--reason", type=click.STRING, default=None, help="Reason for rollback")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def rollback(environment, target_version, role, reason, yes):
    """Rollback an environment to a previous version."""
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("rollback", e.code, e.message, environment=environment, version=target_version)
        log_audit("rollback", "failed", environment=environment, version=target_version, error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        validate_environment(environment)
    except EnvironmentError as e:
        log_error("rollback", e.code, e.message, environment=environment, version=target_version)
        log_audit("rollback", "failed", environment=environment, version=target_version, error_reason=e.message)
        raise click.ClickException(e.message)

    if is_environment_locked(environment):
        lock_info = get_environment_lock(environment)
        err = EnvironmentLockedError(
            environment,
            lock_reason=lock_info["lock_reason"],
            locked_by=lock_info["locked_by"]
        )
        log_error("rollback", err.code, err.message, environment=environment, version=target_version)
        log_audit(
            "rollback",
            "failed",
            environment=environment,
            version=target_version,
            error_reason=err.message,
            details={"conflict_reason": err.message, "role": current_role}
        )
        raise click.ClickException(err.message)

    current_version = get_current_version(environment)

    if not current_version:
        msg = f"Environment {environment} has no current version to rollback from"
        log_error("rollback", "NO_CURRENT_VERSION", msg, environment=environment, version=target_version)
        log_audit(
            "rollback",
            "failed",
            environment=environment,
            version=target_version,
            error_reason=msg,
            details={"role": current_role}
        )
        raise click.ClickException(msg)

    if current_version == target_version:
        msg = f"Environment {environment} is already at version {target_version}"
        log_error("rollback", "ALREADY_AT_VERSION", msg, environment=environment, version=target_version)
        log_audit(
            "rollback",
            "failed",
            environment=environment,
            version=target_version,
            error_reason=msg,
            details={"role": current_role}
        )
        raise click.ClickException(msg)

    if not has_successful_release(target_version, environment):
        err = VersionNotFoundError(target_version, environment)
        log_error("rollback", err.code, err.message, environment=environment, version=target_version)
        log_audit(
            "rollback",
            "failed",
            environment=environment,
            version=target_version,
            error_reason=err.message,
            details={"role": current_role}
        )
        raise click.ClickException(err.message)

    try:
        target_release = get_release(target_version, environment)
        target_config = json.loads(target_release["config_json"])

        current_release = get_release(current_version, environment)
        current_config = json.loads(current_release["config_json"]) if current_release else None
    except Exception as e:
        log_error("rollback", "CONFIG_READ_ERROR", str(e), environment=environment, version=target_version)
        log_audit("rollback", "failed", environment=environment, version=target_version, error_reason=str(e))
        raise click.ClickException(f"Failed to read configuration: {e}")

    diff = compute_diff(current_config, target_config)
    plan_summary = generate_plan_summary(diff)

    click.echo("=" * 60)
    click.echo(f"ROLLBACK PLAN")
    click.echo("=" * 60)
    click.echo(f"Environment:    {environment}")
    click.echo(f"From version:   {current_version}")
    click.echo(f"To version:     {target_version}")
    if reason:
        click.echo(f"Reason:         {reason}")
    click.echo(f"Total changes:  {plan_summary['total_changes']}")
    click.echo("-" * 60)
    
    diff_lines = format_diff(diff)
    for line in diff_lines:
        click.echo(line)
    
    click.echo("-" * 60)

    if not yes:
        confirm = click.confirm(
            f"Are you sure you want to rollback {environment} from {current_version} to {target_version}?",
            default=False
        )
        if not confirm:
            click.echo("Rollback cancelled.")
            log_audit(
                "rollback",
                "cancelled",
                environment=environment,
                version=target_version,
                details={"from_version": current_version, "reason": reason}
            )
            return

    try:
        insert_release(
            target_version,
            environment,
            target_config,
            "success",
            plan_summary=json.dumps(plan_summary)
        )

        set_current_version(environment, target_version)

        insert_rollback(
            environment,
            current_version,
            target_version,
            reason
        )

        click.echo("")
        click.echo("=" * 60)
        click.echo(f"SUCCESS: Rollback to {target_version} completed in {environment}")
        click.echo("=" * 60)
        click.echo(f"Environment {environment} is now at version {target_version}")

        log_audit(
            "rollback",
            "success",
            environment=environment,
            version=target_version,
            details={"from_version": current_version, "reason": reason, "role": current_role, **plan_summary}
        )

    except Exception as e:
        log_error(
            "rollback",
            "ROLLBACK_ERROR",
            str(e),
            environment=environment,
            version=target_version,
            details={"from_version": current_version, "reason": reason, "conflict_reason": str(e), "role": current_role}
        )
        log_audit(
            "rollback",
            "failed",
            environment=environment,
            version=target_version,
            error_reason=str(e),
            details={"from_version": current_version, "reason": reason, "conflict_reason": str(e), "role": current_role}
        )
        raise click.ClickException(f"Failed to rollback: {e}")
