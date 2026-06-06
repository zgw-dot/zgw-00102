import click
from tabulate import tabulate

from ..utils import (
    log_audit,
    log_error,
    check_permission,
    get_role,
    get_environment_lock,
    get_all_environment_locks,
    lock_environment,
    unlock_environment,
    EnvironmentError,
    AlreadyLockedError,
    EnvironmentNotLockedError,
    VALID_ENVIRONMENTS,
)


def validate_environment(env):
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


@click.command()
@click.argument("environment")
@click.option("--reason", type=click.STRING, default=None, help="Reason for locking")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def lock(environment, reason, role):
    """Lock an environment to prevent apply or rollback operations."""
    try:
        validate_environment(environment)
    except EnvironmentError as e:
        log_error("lock", e.code, e.message, environment=environment)
        log_audit("lock", "failed", environment=environment, error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("lock", e.code, e.message, environment=environment)
        log_audit("lock", "failed", environment=environment, error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        check_permission("lock", "release-manager", role)
    except Exception as e:
        log_error("lock", e.code, e.message, environment=environment)
        log_audit("lock", "failed", environment=environment, error_reason=e.message)
        raise click.ClickException(e.message)

    lock_info = get_environment_lock(environment)
    if lock_info and lock_info["is_locked"] == 1:
        err = AlreadyLockedError(environment, lock_info["lock_reason"], lock_info["locked_by"])
        log_error("lock", err.code, err.message, environment=environment)
        log_audit("lock", "failed", environment=environment, error_reason=err.message)
        raise click.ClickException(err.message)

    success = lock_environment(environment, reason=reason)
    if not success:
        msg = f"Failed to lock environment {environment}"
        log_error("lock", "LOCK_FAILED", msg, environment=environment)
        log_audit("lock", "failed", environment=environment, error_reason=msg)
        raise click.ClickException(msg)

    click.echo("=" * 60)
    click.echo(f"ENVIRONMENT LOCKED")
    click.echo("=" * 60)
    click.echo(f"Environment:    {environment}")
    click.echo(f"Locked by:      {lock_info['locked_by'] if lock_info else 'current user'}")
    if reason:
        click.echo(f"Reason:         {reason}")
    click.echo(f"Status:         apply and rollback operations are now blocked")
    click.echo("=" * 60)

    log_audit(
        "lock",
        "success",
        environment=environment,
        details={"reason": reason, "role": current_role}
    )


@click.command()
@click.argument("environment")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def unlock(environment, role):
    """Unlock an environment to allow apply and rollback operations."""
    try:
        validate_environment(environment)
    except EnvironmentError as e:
        log_error("unlock", e.code, e.message, environment=environment)
        log_audit("unlock", "failed", environment=environment, error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("unlock", e.code, e.message, environment=environment)
        log_audit("unlock", "failed", environment=environment, error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        check_permission("unlock", "release-manager", role)
    except Exception as e:
        log_error("unlock", e.code, e.message, environment=environment)
        log_audit("unlock", "failed", environment=environment, error_reason=e.message)
        raise click.ClickException(e.message)

    lock_info = get_environment_lock(environment)
    if not lock_info or lock_info["is_locked"] == 0:
        err = EnvironmentNotLockedError(environment)
        log_error("unlock", err.code, err.message, environment=environment)
        log_audit("unlock", "failed", environment=environment, error_reason=err.message)
        raise click.ClickException(err.message)

    success = unlock_environment(environment)
    if not success:
        msg = f"Failed to unlock environment {environment}"
        log_error("unlock", "UNLOCK_FAILED", msg, environment=environment)
        log_audit("unlock", "failed", environment=environment, error_reason=msg)
        raise click.ClickException(msg)

    click.echo("=" * 60)
    click.echo(f"ENVIRONMENT UNLOCKED")
    click.echo("=" * 60)
    click.echo(f"Environment:    {environment}")
    click.echo(f"Unlocked by:    current user")
    click.echo(f"Status:         apply and rollback operations are now allowed")
    click.echo("=" * 60)

    log_audit(
        "unlock",
        "success",
        environment=environment,
        details={"role": current_role}
    )


@click.command(name="lock-status")
@click.option("--env", type=click.STRING, default=None, help="Filter by environment")
def lock_status(env):
    """Show lock status for all environments or a specific environment."""
    if env is not None:
        try:
            validate_environment(env)
        except EnvironmentError as e:
            log_error("lock-status", e.code, e.message, environment=env)
            log_audit("lock-status", "failed", environment=env, error_reason=e.message)
            raise click.ClickException(e.message)

    try:
        locks = get_all_environment_locks()
    except Exception as e:
        log_error("lock-status", "DATA_READ_ERROR", str(e), environment=env)
        log_audit("lock-status", "failed", environment=env, error_reason=str(e))
        raise click.ClickException(f"Failed to read lock status: {e}")

    if env:
        locks = [l for l in locks if l["environment"] == env]

    click.echo("=" * 80)
    click.echo("ENVIRONMENT LOCK STATUS")
    click.echo("=" * 80)

    if locks:
        table = []
        for lock_info in locks:
            status = "LOCKED" if lock_info["is_locked"] == 1 else "UNLOCKED"
            table.append([
                lock_info["environment"],
                status,
                lock_info["lock_reason"] or "N/A",
                lock_info["locked_by"] or "N/A",
                lock_info["locked_at"] or "N/A",
                lock_info["conflict_reason"] or "N/A",
            ])
        click.echo(tabulate(
            table,
            headers=["Environment", "Status", "Reason", "Locked By", "Locked At", "Conflict Reason"],
            tablefmt="simple"
        ))
    else:
        click.echo("No lock status found.")
    click.echo()

    log_audit(
        "lock-status",
        "success",
        environment=env,
        details={"lock_count": len(locks)}
    )
