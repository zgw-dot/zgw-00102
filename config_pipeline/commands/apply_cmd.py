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
    set_current_version,
    insert_release,
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


def pre_apply_checks(version, environment):
    """Run all pre-apply validation checks."""
    validate_environment(environment)

    if not config_exists(version):
        raise VersionNotFoundError(version)

    if has_successful_release(version, environment):
        raise DuplicateVersionError(version, environment)

    if environment == "prod":
        if not has_successful_release(version, "staging"):
            raise StagingRequiredError(version)


@click.command()
@click.argument("version")
@click.argument("environment")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def apply(version, environment, yes):
    """Apply a configuration version to an environment."""
    try:
        pre_apply_checks(version, environment)
    except (EnvironmentError, VersionNotFoundError, DuplicateVersionError, StagingRequiredError) as e:
        log_error("apply", e.code, e.message, environment=environment, version=version)
        log_audit("apply", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        target_config_data = get_config(version)
        target_config = json.loads(target_config_data["config_json"])
    except Exception as e:
        log_error("apply", "CONFIG_READ_ERROR", str(e), environment=environment, version=version)
        log_audit("apply", "failed", environment=environment, version=version, error_reason=str(e))
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
        log_error("apply", err.code, err.message, environment=environment, version=version)
        log_audit("apply", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    plan_summary = generate_plan_summary(diff)

    click.echo("=" * 60)
    click.echo(f"APPLYING CONFIGURATION")
    click.echo("=" * 60)
    click.echo(f"Version:        {version}")
    click.echo(f"Environment:    {environment}")
    click.echo(f"Current:        {current_version or 'None'}")
    click.echo(f"Total changes:  {plan_summary['total_changes']}")
    click.echo("-" * 60)
    
    diff_lines = format_diff(diff)
    for line in diff_lines:
        click.echo(line)
    
    click.echo("-" * 60)

    if not yes:
        confirm = click.confirm(
            f"Are you sure you want to apply version {version} to {environment}?",
            default=False
        )
        if not confirm:
            click.echo("Apply cancelled.")
            log_audit(
                "apply",
                "cancelled",
                environment=environment,
                version=version,
                details=plan_summary
            )
            return

    try:
        insert_release(
            version,
            environment,
            target_config,
            "success",
            plan_summary=json.dumps(plan_summary)
        )

        set_current_version(environment, version)

        click.echo("")
        click.echo("=" * 60)
        click.echo(f"SUCCESS: Version {version} applied to {environment}")
        click.echo("=" * 60)
        click.echo(f"Environment {environment} is now at version {version}")

        log_audit(
            "apply",
            "success",
            environment=environment,
            version=version,
            details=plan_summary
        )

    except Exception as e:
        insert_release(
            version,
            environment,
            target_config,
            "failed",
            plan_summary=json.dumps(plan_summary)
        )

        log_error(
            "apply",
            "APPLY_ERROR",
            str(e),
            environment=environment,
            version=version,
            details=plan_summary
        )
        log_audit(
            "apply",
            "failed",
            environment=environment,
            version=version,
            error_reason=str(e),
            details=plan_summary
        )
        raise click.ClickException(f"Failed to apply configuration: {e}")
