import click
from datetime import datetime, timedelta

from ..utils import (
    log_audit,
    log_error,
    get_role,
    check_permission,
    VALID_ENVIRONMENTS,
    create_release_window,
    disable_release_window,
    get_release_window,
    get_all_release_windows,
    get_active_release_window,
    EnvironmentError,
    InvalidWindowTimeError,
    OverlappingWindowError,
    WindowNotFoundError,
    ReleaseWindowError,
    OverridePermissionDeniedError,
    PermissionDeniedError,
)


def validate_environment(env):
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


def _check_prod_permission(action, environment, cli_role):
    """Check if user has permission for prod environment operations."""
    if environment == "prod":
        check_permission(action, "release-manager", cli_role)


@click.group()
def window():
    """Manage release windows for environment deployments.
    
    Release windows allow you to define periods when releases are blocked.
    Create windows to close deployments, disable them to re-open.
    """
    pass


@window.command(name="create")
@click.argument("environment")
@click.argument("start_time")
@click.argument("end_time")
@click.option("--reason", required=True, help="Reason for closing the release window")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def window_create(environment, start_time, end_time, reason, role):
    """Create a closed release window for an environment.
    
    START_TIME and END_TIME should be in ISO format (e.g., 2024-01-01T00:00:00).
    
    Examples:
        pipeline window create dev 2024-01-01T00:00:00 2024-01-02T00:00:00 --reason "Maintenance"
        pipeline window create prod 2024-12-24T18:00:00 2024-12-26T09:00:00 --reason "Holiday freeze" --role release-manager
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("window_create", e.code, e.message, environment=environment)
        log_audit("window_create", "failed", environment=environment, error_reason=e.message)
        raise click.ClickException(e.message)
    
    try:
        validate_environment(environment)
    except EnvironmentError as e:
        log_error("window_create", e.code, e.message, environment=environment)
        log_audit("window_create", "failed", environment=environment, error_reason=e.message)
        raise click.ClickException(f"{e.message} [{e.code}]")
    
    try:
        window_id = create_release_window(environment, start_time, end_time, reason, cli_role=role)
        
        click.echo("=" * 60)
        click.echo(f"RELEASE WINDOW CREATED")
        click.echo("=" * 60)
        click.echo(f"ID:             {window_id}")
        click.echo(f"Environment:    {environment}")
        click.echo(f"Start time:     {start_time}")
        click.echo(f"End time:       {end_time}")
        click.echo(f"Reason:         {reason}")
        click.echo(f"Created by:     {current_role}")
        click.echo("")
        click.echo(f"Releases to {environment} are blocked during this window.")
        
    except (InvalidWindowTimeError, OverlappingWindowError, OverridePermissionDeniedError, PermissionDeniedError) as e:
        log_error("window_create", e.code, e.message, environment=environment)
        log_audit(
            "window_create",
            "failed",
            environment=environment,
            error_reason=e.message,
            details={
                "start_time": start_time,
                "end_time": end_time,
                "reason": reason,
                "role": current_role
            }
        )
        raise click.ClickException(f"{e.message} [{e.code}]")
    except Exception as e:
        log_error("window_create", "CREATE_ERROR", str(e), environment=environment)
        log_audit("window_create", "failed", environment=environment, error_reason=str(e))
        raise click.ClickException(f"Failed to create release window: {e}")


@window.command(name="list")
@click.option("--env", type=click.STRING, default=None, help="Filter by environment")
@click.option("--all", "show_all", is_flag=True, help="Show disabled windows as well")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def window_list(env, show_all, role):
    """List all release windows.
    
    Examples:
        pipeline window list
        pipeline window list --env prod
        pipeline window list --all
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("window_list", e.code, e.message, environment=env)
        log_audit("window_list", "failed", environment=env, error_reason=e.message)
        raise click.ClickException(e.message)
    
    if env is not None and env not in VALID_ENVIRONMENTS:
        err = EnvironmentError(env, VALID_ENVIRONMENTS)
        log_error("window_list", err.code, err.message, environment=env)
        log_audit("window_list", "failed", environment=env, error_reason=err.message)
        raise click.ClickException(err.message)
    
    try:
        windows = get_all_release_windows(environment=env, include_disabled=show_all)
        
        click.echo("=" * 100)
        click.echo(f"RELEASE WINDOWS ({'all' if show_all else 'active only'})")
        click.echo("=" * 100)
        
        if not windows:
            click.echo("No release windows found.")
            log_audit(
                "window_list",
                "success",
                environment=env,
                details={"count": 0, "show_all": show_all, "role": current_role}
            )
            return
        
        click.echo(f"{'ID':<5} {'ENV':<10} {'STATUS':<10} {'START':<25} {'END':<25} {'REASON'}")
        click.echo("-" * 100)
        
        now = datetime.now()
        
        for w in windows:
            is_enabled = w["is_enabled"] == 1
            status = "ACTIVE" if is_enabled else "DISABLED"
            
            if is_enabled:
                try:
                    w_start = datetime.fromisoformat(w["start_time"])
                    w_end = datetime.fromisoformat(w["end_time"])
                    if w_start <= now <= w_end:
                        status = "CURRENT"
                    elif now < w_start:
                        status = "UPCOMING"
                    else:
                        status = "EXPIRED"
                except:
                    pass
            
            status_color = {"CURRENT": "red", "ACTIVE": "yellow", "UPCOMING": "blue", "EXPIRED": "white", "DISABLED": "white"}.get(status, "white")
            status_display = click.style(status, fg=status_color)
            
            click.echo(f"{w['id']:<5} {w['environment']:<10} {status_display:<10} {w['start_time']:<25} {w['end_time']:<25} {w['reason']}")
        
        click.echo("")
        click.echo(f"Total: {len(windows)} window(s)")
        
        log_audit(
            "window_list",
            "success",
            environment=env,
            details={"count": len(windows), "show_all": show_all, "role": current_role}
        )
        
    except Exception as e:
        log_error("window_list", "LIST_ERROR", str(e), environment=env)
        log_audit("window_list", "failed", environment=env, error_reason=str(e))
        raise click.ClickException(f"Failed to list release windows: {e}")


@window.command(name="status")
@click.argument("environment", required=False)
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def window_status(environment, role):
    """Check if an environment has an active (closed) release window.
    
    If no environment specified, checks all environments.
    
    Examples:
        pipeline window status
        pipeline window status prod
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("window_status", e.code, e.message, environment=environment)
        log_audit("window_status", "failed", environment=environment, error_reason=e.message)
        raise click.ClickException(e.message)
    
    envs_to_check = [environment] if environment else VALID_ENVIRONMENTS
    
    if environment and environment not in VALID_ENVIRONMENTS:
        err = EnvironmentError(environment, VALID_ENVIRONMENTS)
        log_error("window_status", err.code, err.message, environment=environment)
        log_audit("window_status", "failed", environment=environment, error_reason=err.message)
        raise click.ClickException(err.message)
    
    try:
        click.echo("=" * 80)
        click.echo(f"RELEASE WINDOW STATUS")
        click.echo("=" * 80)
        
        results = []
        for env in envs_to_check:
            active_window = get_active_release_window(env)
            if active_window:
                results.append((env, "CLOSED", active_window))
            else:
                results.append((env, "OPEN", None))
        
        for env, status, window in results:
            if status == "CLOSED":
                status_display = click.style("CLOSED", fg="red")
                click.echo(f"  {env:<10} {status_display}")
                click.echo(f"              Reason: {window['reason']}")
                click.echo(f"              Window: {window['start_time']} -> {window['end_time']}")
                click.echo(f"              Created by: {window['created_by']}")
            else:
                status_display = click.style("OPEN", fg="green")
                click.echo(f"  {env:<10} {status_display}")
        
        log_audit(
            "window_status",
            "success",
            environment=environment,
            details={"results": results, "role": current_role}
        )
        
    except Exception as e:
        log_error("window_status", "STATUS_ERROR", str(e), environment=environment)
        log_audit("window_status", "failed", environment=environment, error_reason=str(e))
        raise click.ClickException(f"Failed to check window status: {e}")


@window.command(name="disable")
@click.argument("window_id", type=int)
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def window_disable(window_id, role):
    """Disable (re-open) a release window.
    
    Examples:
        pipeline window disable 1
        pipeline window disable 2 --role release-manager
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("window_disable", e.code, e.message, details={"window_id": window_id})
        log_audit("window_disable", "failed", error_reason=e.message, details={"window_id": window_id})
        raise click.ClickException(e.message)
    
    try:
        window_info = get_release_window(window_id)
        if not window_info:
            raise WindowNotFoundError(window_id)
        
        success = disable_release_window(window_id, cli_role=role)
        
        if success:
            click.echo("=" * 60)
            click.echo(f"RELEASE WINDOW DISABLED")
            click.echo("=" * 60)
            click.echo(f"ID:             {window_id}")
            click.echo(f"Environment:    {window_info['environment']}")
            click.echo(f"Reason:         {window_info['reason']}")
            click.echo("")
            click.echo(f"Releases to {window_info['environment']} are now allowed.")
        else:
            click.echo(f"Window {window_id} is already disabled.")
        
    except (WindowNotFoundError, OverridePermissionDeniedError, PermissionDeniedError) as e:
        log_error("window_disable", e.code, e.message, details={"window_id": window_id})
        log_audit(
            "window_disable",
            "failed",
            error_reason=e.message,
            details={"window_id": window_id, "role": current_role}
        )
        raise click.ClickException(f"{e.message} [{e.code}]")
    except Exception as e:
        log_error("window_disable", "DISABLE_ERROR", str(e), details={"window_id": window_id})
        log_audit("window_disable", "failed", error_reason=str(e), details={"window_id": window_id})
        raise click.ClickException(f"Failed to disable release window: {e}")
