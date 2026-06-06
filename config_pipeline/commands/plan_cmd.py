import click
import json

from ..utils import (
    log_audit,
    log_error,
    get_config,
    get_current_version,
    get_release,
    config_exists,
    has_successful_release,
    EnvironmentError,
    VersionNotFoundError,
    DuplicateVersionError,
    StagingRequiredError,
    NoChangesError,
    VALID_ENVIRONMENTS,
    compute_diff,
    has_changes,
    format_diff,
    generate_plan_summary,
)


def validate_environment(env):
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


@click.command()
@click.argument("version")
@click.argument("environment")
def plan(version, environment):
    """Generate a deployment plan for a configuration version."""
    try:
        validate_environment(environment)
    except EnvironmentError as e:
        log_error("plan", e.code, e.message, environment=environment, version=version)
        log_audit("plan", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    if not config_exists(version):
        err = VersionNotFoundError(version)
        log_error("plan", err.code, err.message, environment=environment, version=version)
        log_audit("plan", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    if has_successful_release(version, environment):
        err = DuplicateVersionError(version, environment)
        log_error("plan", err.code, err.message, environment=environment, version=version)
        log_audit("plan", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    if environment == "prod":
        if not has_successful_release(version, "staging"):
            err = StagingRequiredError(version)
            log_error("plan", err.code, err.message, environment=environment, version=version)
            log_audit("plan", "failed", environment=environment, version=version, error_reason=err.message)
            raise click.ClickException(err.message)

    try:
        target_config_data = get_config(version)
        target_config = json.loads(target_config_data["config_json"])
    except Exception as e:
        log_error("plan", "CONFIG_READ_ERROR", str(e), environment=environment, version=version)
        log_audit("plan", "failed", environment=environment, version=version, error_reason=str(e))
        raise click.ClickException(f"Failed to read target config: {e}")

    current_version = get_current_version(environment)
    current_config = None

    if current_version:
        current_release = get_release(current_version, environment)
        if current_release:
            current_config = json.loads(current_release["config_json"])

    diff = compute_diff(current_config, target_config)

    if not has_changes(diff):
        err = NoChangesError()
        log_error("plan", err.code, err.message, environment=environment, version=version)
        log_audit("plan", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    plan_summary = generate_plan_summary(diff)

    click.echo("=" * 60)
    click.echo(f"DEPLOYMENT PLAN")
    click.echo("=" * 60)
    click.echo(f"Version:        {version}")
    click.echo(f"Environment:    {environment}")
    click.echo(f"Current:        {current_version or 'None'}")
    click.echo(f"Total changes:  {plan_summary['total_changes']}")
    click.echo("-" * 60)
    click.echo(f"  Added:    {plan_summary['added_count']}")
    click.echo(f"  Removed:  {plan_summary['removed_count']}")
    click.echo(f"  Modified: {plan_summary['modified_count']}")
    click.echo("-" * 60)
    click.echo("CHANGES:")
    click.echo("-" * 60)
    
    diff_lines = format_diff(diff)
    if diff_lines:
        for line in diff_lines:
            click.echo(line)
    else:
        click.echo("  (No changes detected)")
    
    click.echo("-" * 60)
    click.echo("To apply this plan, run:")
    click.echo(f"  pipeline apply {version} {environment}")
    click.echo("=" * 60)

    log_audit(
        "plan",
        "success",
        environment=environment,
        version=version,
        details=plan_summary
    )

    return {
        "version": version,
        "environment": environment,
        "current_version": current_version,
        "diff": diff,
        "summary": plan_summary,
    }
