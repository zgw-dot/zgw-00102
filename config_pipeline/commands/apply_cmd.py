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
    get_role,
    check_permission,
    is_environment_locked,
    get_environment_lock,
    is_approved,
    get_approval,
    get_latest_preview,
    check_preview_drift,
    delete_preview,
    EnvironmentError,
    VersionNotFoundError,
    DuplicateVersionError,
    StagingRequiredError,
    NoChangesError,
    EnvironmentLockedError,
    ApprovalRequiredError,
    PermissionDeniedError,
    PreviewNotFoundError,
    PreviewDriftError,
    PreviewAckDeniedError,
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


def pre_apply_checks(version, environment, cli_role=None):
    """Run all pre-apply validation checks."""
    validate_environment(environment)

    if not config_exists(version):
        raise VersionNotFoundError(version)

    if has_successful_release(version, environment):
        raise DuplicateVersionError(version, environment)

    if environment == "prod":
        if not has_successful_release(version, "staging"):
            raise StagingRequiredError(version)
        check_permission("apply", "release-manager", cli_role)

    if is_environment_locked(environment):
        lock_info = get_environment_lock(environment)
        raise EnvironmentLockedError(
            environment,
            lock_reason=lock_info["lock_reason"],
            locked_by=lock_info["locked_by"]
        )

    if not is_approved(version, environment):
        raise ApprovalRequiredError(version, environment)


def _check_drift_ack_permissions(environment, drift_reasons, cli_role):
    """Check if drift acknowledgment is allowed for the current role.
    
    developer cannot acknowledge:
    - Prod environment drift of any kind
    - Lock status changes in any environment
    
    Returns (allowed, reason_if_denied)
    """
    current_role = get_role(cli_role)

    if current_role == "developer":
        if environment == "prod":
            return False, "developer cannot acknowledge drift in prod environment"

        for reason in drift_reasons:
            if "locked" in reason.lower() or "unlocked" in reason.lower():
                return False, "developer cannot acknowledge lock status changes"

    return True, None


@click.command()
@click.argument("version", required=False)
@click.argument("environment", required=False)
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
@click.option("--from-preview", is_flag=True, help="Apply from saved preview and check for drift")
@click.option("--ack-drift", is_flag=True, help="Acknowledge and proceed despite drift (release-manager only for prod/locks)")
def apply(version, environment, role, yes, from_preview, ack_drift):
    """Apply a configuration version to an environment."""
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("apply", e.code, e.message, environment=environment, version=version)
        log_audit("apply", "failed", environment=environment, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    preview_data = None
    if from_preview:
        preview_data = get_latest_preview(version=version, environment=environment)
        if not preview_data:
            err = PreviewNotFoundError(version=version, environment=environment)
            log_error("apply", err.code, err.message, environment=environment, version=version)
            log_audit(
                "apply",
                "failed",
                environment=environment,
                version=version,
                error_reason=err.message,
                details={"from_preview": True, "role": current_role}
            )
            raise click.ClickException(err.message)

        version = preview_data["version"]
        environment = preview_data["environment"]

        drift_reasons = check_preview_drift(preview_data)
        if drift_reasons:
            if ack_drift:
                ack_allowed, ack_reason = _check_drift_ack_permissions(environment, drift_reasons, role)
                if not ack_allowed:
                    err = PreviewAckDeniedError(ack_reason)
                    log_error("apply", err.code, err.message, environment=environment, version=version)
                    log_audit(
                        "apply",
                        "failed",
                        environment=environment,
                        version=version,
                        error_reason=err.message,
                        details={
                            "from_preview": True,
                            "ack_drift": True,
                            "drift_reasons": drift_reasons,
                            "role": current_role
                        }
                    )
                    raise click.ClickException(err.message)

                click.echo("! DRIFT DETECTED but acknowledged:")
                for reason in drift_reasons:
                    click.echo(f"  ! {reason}")
                click.echo(f"Proceeding with apply (acknowledged by {current_role})")
                log_audit(
                    "apply",
                    "drift_acknowledged",
                    environment=environment,
                    version=version,
                    details={
                        "drift_reasons": drift_reasons,
                        "acknowledged_by": current_role,
                        "from_preview": True
                    }
                )
            else:
                err = PreviewDriftError(drift_reasons)
                log_error("apply", err.code, err.message, environment=environment, version=version)
                log_audit(
                    "apply",
                    "drift_detected",
                    environment=environment,
                    version=version,
                    error_reason=err.message,
                    details={
                        "from_preview": True,
                        "drift_reasons": drift_reasons,
                        "role": current_role
                    }
                )
                raise click.ClickException(err.message)
        else:
            click.echo("OK No drift detected since preview")
            log_audit(
                "apply",
                "no_drift",
                environment=environment,
                version=version,
                details={"from_preview": True, "role": current_role}
            )

    if version is None or environment is None:
        raise click.ClickException("Usage: apply VERSION ENVIRONMENT [OPTIONS] or use --from-preview")

    try:
        pre_apply_checks(version, environment, cli_role=role)
    except (EnvironmentError, VersionNotFoundError, DuplicateVersionError, StagingRequiredError, EnvironmentLockedError, ApprovalRequiredError, PermissionDeniedError) as e:
        log_error("apply", e.code, e.message, environment=environment, version=version)
        log_audit(
            "apply",
            "failed",
            environment=environment,
            version=version,
            error_reason=e.message,
            details={
                "conflict_reason": e.message,
                "role": current_role,
                "from_preview": from_preview
            }
        )
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
    if from_preview:
        click.echo(f"From preview:   #{preview_data['id']}")
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
                details={
                    **plan_summary,
                    "from_preview": from_preview,
                    "role": current_role
                }
            )
            return

    approval = get_approval(version, environment)
    approved_by = approval["approved_by"] if approval and approval.get("approved_by") else None

    try:
        insert_release(
            version,
            environment,
            target_config,
            "success",
            plan_summary=json.dumps(plan_summary),
            approved_by=approved_by,
            conflict_reason=json.dumps({"from_preview": from_preview}) if from_preview else None
        )

        set_current_version(environment, version)

        if from_preview:
            delete_preview(version, environment)

        click.echo("")
        click.echo("=" * 60)
        click.echo(f"SUCCESS: Version {version} applied to {environment}")
        click.echo("=" * 60)
        click.echo(f"Environment {environment} is now at version {version}")
        if approved_by:
            click.echo(f"Approved by:    {approved_by}")

        log_audit(
            "apply",
            "success",
            environment=environment,
            version=version,
            details={
                **plan_summary,
                "role": current_role,
                "approved_by": approved_by,
                "from_preview": from_preview
            }
        )

    except Exception as e:
        insert_release(
            version,
            environment,
            target_config,
            "failed",
            plan_summary=json.dumps(plan_summary),
            conflict_reason=str(e)
        )

        log_error(
            "apply",
            "APPLY_ERROR",
            str(e),
            environment=environment,
            version=version,
            details={
                **plan_summary,
                "conflict_reason": str(e),
                "role": current_role,
                "from_preview": from_preview
            }
        )
        log_audit(
            "apply",
            "failed",
            environment=environment,
            version=version,
            error_reason=str(e),
            details={
                **plan_summary,
                "conflict_reason": str(e),
                "role": current_role,
                "from_preview": from_preview
            }
        )
        raise click.ClickException(f"Failed to apply configuration: {e}")
