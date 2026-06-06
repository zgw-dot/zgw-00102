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
    get_role,
    is_environment_locked,
    get_environment_lock,
    is_approved,
    requires_approval,
    insert_preview,
    get_latest_preview,
    get_preview_by_id,
    get_all_previews,
    EnvironmentError,
    VersionNotFoundError,
    DuplicateVersionError,
    StagingRequiredError,
    EnvironmentLockedError,
    ApprovalRequiredError,
    PreviewNotFoundError,
    PreviewNoChangesError,
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


def run_preview_checks(version, environment, cli_role=None):
    """Run preview validation checks without raising blocking errors.
    
    Returns a dict with check results for display purposes.
    """
    checks = {
        "valid_version": True,
        "already_released": False,
        "staging_ok": True,
        "is_locked": False,
        "lock_info": None,
        "is_approved": True,
        "requires_approval": requires_approval(environment),
        "requires_staging": environment == "prod",
        "errors": [],
    }

    validate_environment(environment)

    if not config_exists(version):
        checks["valid_version"] = False
        checks["errors"].append(VersionNotFoundError(version))

    if has_successful_release(version, environment):
        checks["already_released"] = True
        checks["errors"].append(DuplicateVersionError(version, environment))

    if environment == "prod":
        if not has_successful_release(version, "staging"):
            checks["staging_ok"] = False
            checks["errors"].append(StagingRequiredError(version))

    if is_environment_locked(environment):
        checks["is_locked"] = True
        checks["lock_info"] = get_environment_lock(environment)
        checks["errors"].append(
            EnvironmentLockedError(
                environment,
                lock_reason=checks["lock_info"]["lock_reason"],
                locked_by=checks["lock_info"]["locked_by"]
            )
        )

    if not is_approved(version, environment):
        checks["is_approved"] = False
        if checks["requires_approval"]:
            checks["errors"].append(ApprovalRequiredError(version, environment))

    return checks


@click.group()
def preview():
    """Preview configuration changes before applying."""
    pass


@preview.command(name="run")
@click.argument("version")
@click.argument("environment")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def preview_run(version, environment, role):
    """Preview what changes would be made by applying a version."""
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("preview", e.code, e.message, environment=environment, version=version)
        log_audit("preview", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        checks = run_preview_checks(version, environment, cli_role=role)
    except EnvironmentError as e:
        log_error("preview", e.code, e.message, environment=environment, version=version)
        log_audit("preview", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    if not checks["valid_version"]:
        err = checks["errors"][0]
        log_error("preview", err.code, err.message, environment=environment, version=version)
        log_audit("preview", "failed", environment=environment, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    try:
        target_config_data = get_config(version)
        target_config = json.loads(target_config_data["config_json"])
    except Exception as e:
        log_error("preview", "CONFIG_READ_ERROR", str(e), environment=environment, version=version)
        log_audit("preview", "failed", environment=environment, version=version, error_reason=str(e))
        raise click.ClickException(f"Failed to read target config: {e}")

    current_version = get_current_version(environment)
    current_config = None

    if current_version:
        current_release = get_release(current_version, environment)
        if current_release:
            current_config = json.loads(current_release["config_json"])

    diff = compute_diff(current_config, target_config)
    plan_summary = generate_plan_summary(diff)
    has_changes_flag = has_changes(diff)

    try:
        insert_preview(
            version=version,
            environment=environment,
            target_config=target_config,
            current_version=current_version,
            current_config=current_config,
            plan_summary=plan_summary,
            diff=diff,
            requires_approval=checks["requires_approval"] and not checks["is_approved"],
            requires_staging=not checks["staging_ok"],
            is_locked=checks["is_locked"],
            has_changes=has_changes_flag
        )
    except Exception as e:
        log_error("preview", "PREVIEW_SAVE_ERROR", str(e), environment=environment, version=version)
        log_audit("preview", "failed", environment=environment, version=version, error_reason=str(e))
        raise click.ClickException(f"Failed to save preview: {e}")

    click.echo("=" * 60)
    click.echo(f"RELEASE PREVIEW")
    click.echo("=" * 60)
    click.echo(f"Version:        {version}")
    click.echo(f"Environment:    {environment}")
    click.echo(f"Current:        {current_version or 'None'}")
    click.echo(f"Role:           {current_role}")
    click.echo("-" * 60)

    click.echo("STATUS CHECKS:")
    click.echo(f"  Config exists:     {'YES' if checks['valid_version'] else 'NO'}")
    click.echo(f"  Already released:  {'YES' if checks['already_released'] else 'NO'}")
    if checks["requires_staging"]:
        click.echo(f"  Staging deployed:  {'YES' if checks['staging_ok'] else 'NO'}")
    click.echo(f"  Environment locked:{'YES' if checks['is_locked'] else 'NO'}")
    if checks["requires_approval"]:
        click.echo(f"  Approved:          {'YES' if checks['is_approved'] else 'NO'}")

    if checks["is_locked"] and checks["lock_info"]:
        click.echo(f"    Lock reason: {checks['lock_info']['lock_reason']}")
        if checks["lock_info"]["locked_by"]:
            click.echo(f"    Locked by:   {checks['lock_info']['locked_by']}")

    click.echo("-" * 60)

    if not has_changes_flag:
        click.echo("! NO CHANGES detected between current and target configuration")
        click.echo("-" * 60)
    else:
        click.echo(f"CHANGES SUMMARY:")
        click.echo(f"  Total changes:  {plan_summary['total_changes']}")
        click.echo(f"  Added keys:     {plan_summary['added_count']}")
        click.echo(f"  Removed keys:   {plan_summary['removed_count']}")
        click.echo(f"  Modified keys:  {plan_summary['modified_count']}")
        click.echo("-" * 60)
        click.echo("DETAILED DIFF:")
        diff_lines = format_diff(diff)
        for line in diff_lines:
            click.echo(line)
        click.echo("-" * 60)

    if checks["errors"]:
        click.echo("! BLOCKING ISSUES (apply will fail):")
        for err in checks["errors"]:
            click.echo(f"  ! {err.message}")
        click.echo("-" * 60)

    click.echo("Preview saved. Use 'pipeline preview show' to view again.")

    if not has_changes_flag:
        log_audit(
            "preview",
            "no_changes",
            environment=environment,
            version=version,
            details={**plan_summary, "role": current_role, "checks": _serialize_checks(checks)}
        )
    elif checks["errors"]:
        log_audit(
            "preview",
            "blocked",
            environment=environment,
            version=version,
            details={**plan_summary, "role": current_role, "checks": _serialize_checks(checks)}
        )
    else:
        log_audit(
            "preview",
            "success",
            environment=environment,
            version=version,
            details={**plan_summary, "role": current_role, "checks": _serialize_checks(checks)}
        )


@preview.command(name="show")
@click.argument("version", required=False)
@click.argument("environment", required=False)
@click.option("--id", type=click.INT, default=None, help="Show a specific preview by ID")
@click.option("--all", is_flag=True, help="Show all saved previews")
def preview_show(version, environment, id, all):
    """Show saved preview results."""
    try:
        current_role = get_role()
    except Exception as e:
        log_error("preview_show", e.code, e.message)
        raise click.ClickException(e.message)

    if all:
        previews = get_all_previews()
        if not previews:
            raise click.ClickException("No saved previews found")

        click.echo("=" * 80)
        click.echo(f"{'ID':<5} {'Version':<12} {'Env':<10} {'Changes':<10} {'Created At':<20} {'By':<15}")
        click.echo("-" * 80)
        for p in previews:
            changes = p["plan_summary"]["total_changes"] if p["has_changes"] else 0
            click.echo(f"{p['id']:<5} {p['version']:<12} {p['environment']:<10} {changes:<10} {p['created_at']:<20} {p['created_by']:<15}")
        log_audit("preview_show", "success", details={"count": len(previews), "role": current_role})
        return

    if id is not None:
        preview_data = get_preview_by_id(id)
        if not preview_data:
            err = PreviewNotFoundError()
            log_error("preview_show", err.code, err.message)
            log_audit("preview_show", "failed", error_reason=err.message)
            raise click.ClickException(err.message)
    else:
        preview_data = get_latest_preview(version=version, environment=environment)
        if not preview_data:
            err = PreviewNotFoundError(version=version, environment=environment)
            log_error("preview_show", err.code, err.message, environment=environment, version=version)
            log_audit("preview_show", "failed", environment=environment, version=version, error_reason=err.message)
            raise click.ClickException(err.message)

    _print_preview(preview_data)
    log_audit(
        "preview_show",
        "success",
        environment=preview_data["environment"],
        version=preview_data["version"],
        details={"preview_id": preview_data["id"], "role": current_role}
    )


def _serialize_checks(checks):
    """Serialize checks dict for audit logging."""
    return {
        "valid_version": checks["valid_version"],
        "already_released": checks["already_released"],
        "staging_ok": checks["staging_ok"],
        "is_locked": checks["is_locked"],
        "is_approved": checks["is_approved"],
        "requires_approval": checks["requires_approval"],
        "requires_staging": checks["requires_staging"],
        "error_messages": [e.message for e in checks["errors"]],
    }


def _print_preview(p):
    """Print a preview record in a human-readable format."""
    click.echo("=" * 60)
    click.echo(f"PREVIEW #{p['id']}")
    click.echo("=" * 60)
    click.echo(f"Version:        {p['version']}")
    click.echo(f"Environment:    {p['environment']}")
    click.echo(f"Current:        {p['current_version'] or 'None'}")
    click.echo(f"Created at:     {p['created_at']}")
    click.echo(f"Created by:     {p['created_by']}")
    click.echo("-" * 60)

    click.echo("SNAPSHOT STATE:")
    click.echo("  Environment pointers:")
    for env, ver in sorted(p["env_pointer_snapshot"].items()):
        click.echo(f"    {env:<10}: {ver or 'None'}")
    click.echo("  Lock status:")
    for env, locked in sorted(p["lock_snapshot"].items()):
        click.echo(f"    {env:<10}: {'LOCKED' if locked else 'unlocked'}")
    click.echo("-" * 60)

    click.echo("REQUIREMENTS:")
    click.echo(f"  Requires approval:  {'YES' if p['requires_approval'] else 'NO'}")
    click.echo(f"  Requires staging:   {'YES' if p['requires_staging'] else 'NO'}")
    click.echo(f"  Environment locked: {'YES' if p['is_locked'] else 'NO'}")
    click.echo("-" * 60)

    if not p["has_changes"]:
        click.echo("! NO CHANGES detected")
    else:
        click.echo(f"CHANGES SUMMARY:")
        click.echo(f"  Total changes:  {p['plan_summary']['total_changes']}")
        click.echo(f"  Added keys:     {p['plan_summary']['added_count']}")
        click.echo(f"  Removed keys:   {p['plan_summary']['removed_count']}")
        click.echo(f"  Modified keys:  {p['plan_summary']['modified_count']}")
        if p["plan_summary"]["added_keys"]:
            click.echo(f"  Added:          {', '.join(p['plan_summary']['added_keys'])}")
        if p["plan_summary"]["removed_keys"]:
            click.echo(f"  Removed:        {', '.join(p['plan_summary']['removed_keys'])}")
        if p["plan_summary"]["modified_keys"]:
            click.echo(f"  Modified:       {', '.join(p['plan_summary']['modified_keys'])}")
        click.echo("-" * 60)
        click.echo("DETAILED DIFF:")
        diff_lines = format_diff(p["diff"])
        for line in diff_lines:
            click.echo(line)
