import click
import json
import re

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
    check_release_window,
    EnvironmentError,
    VersionNotFoundError,
    DuplicateVersionError,
    StagingRequiredError,
    NoChangesError,
    EnvironmentLockedError,
    ApprovalRequiredError,
    PermissionDeniedError,
    PreviewDriftError,
    ReleaseWindowError,
    OverridePermissionDeniedError,
    InvalidWindowTimeError,
    PackageNotSignedError,
    VALID_ENVIRONMENTS,
    compute_diff,
    has_changes,
    format_diff,
    generate_plan_summary,
    check_package_signoff,
    is_version_in_signed_package,
)


STEP_PATTERN = re.compile(r'^([a-z]+):(.+)$')


def parse_step(step_str):
    """Parse a step string like 'dev:2.0.0' into (environment, version)."""
    match = STEP_PATTERN.match(step_str)
    if not match:
        raise click.BadParameter(
            f"Invalid step format '{step_str}'. Expected format: 'environment:version' (e.g., 'dev:2.0.0')"
        )
    env, version = match.groups()
    if env not in VALID_ENVIRONMENTS:
        raise click.BadParameter(
            f"Invalid environment '{env}'. Must be one of: {', '.join(VALID_ENVIRONMENTS)}"
        )
    return env, version


def pre_apply_checks(version, environment, cli_role=None, override_window=False, override_reason=None):
    """Run all pre-apply validation checks."""
    if environment not in VALID_ENVIRONMENTS:
        raise EnvironmentError(environment, VALID_ENVIRONMENTS)

    _, window_info, override_info = check_release_window(
        environment,
        version=version,
        override=override_window,
        override_reason=override_reason,
        cli_role=cli_role,
        action="batch_apply"
    )

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

    is_valid, pkg_name, error_msg = check_package_signoff(version, environment)
    if not is_valid:
        raise PackageNotSignedError(pkg_name or "unknown", version, environment)

    return _, window_info, override_info


def apply_single_step(version, environment, cli_role=None, yes=False, previous_successful_envs=None, override_window=False, override_reason=None):
    """Apply a single version to an environment, with drift checking.
    
    Args:
        previous_successful_envs: Set of environments that were successfully updated
            in previous steps of the same batch. Drift caused by these steps will be ignored.
    
    Returns (success, error_message, drift_reasons, override_info)
    """
    current_role = get_role(cli_role)
    previous_successful_envs = previous_successful_envs or set()

    preview_data = get_latest_preview(version=version, environment=environment)

    if preview_data:
        drift_reasons = check_preview_drift(preview_data)
        if drift_reasons:
            filtered_drift = []
            for reason in drift_reasons:
                ignore = False
                for prev_env in previous_successful_envs:
                    if f"'{prev_env}' pointer changed" in reason:
                        ignore = True
                        break
                if not ignore:
                    filtered_drift.append(reason)
            
            if filtered_drift:
                err = PreviewDriftError(filtered_drift)
                log_error("batch_apply", err.code, err.message, environment=environment, version=version)
                log_audit(
                    "batch_apply",
                    "drift_detected",
                    environment=environment,
                    version=version,
                    error_reason=err.message,
                    details={
                        "drift_reasons": filtered_drift,
                        "all_drift_reasons": drift_reasons,
                        "ignored_drift_reasons": [r for r in drift_reasons if r not in filtered_drift],
                        "role": current_role,
                        "step": f"{environment}:{version}"
                    }
                )
                return False, err.message, filtered_drift, None

    override_info = None
    try:
        _, window_info, override_info = pre_apply_checks(
            version, environment, cli_role=cli_role,
            override_window=override_window,
            override_reason=override_reason
        )
    except (EnvironmentError, VersionNotFoundError, DuplicateVersionError, StagingRequiredError, EnvironmentLockedError, ApprovalRequiredError, PermissionDeniedError, ReleaseWindowError, OverridePermissionDeniedError, InvalidWindowTimeError, PackageNotSignedError) as e:
        if isinstance(e, ReleaseWindowError) or isinstance(e, OverridePermissionDeniedError):
            pass
        else:
            log_error("batch_apply", e.code, e.message, environment=environment, version=version)
            log_audit(
                "batch_apply",
                "failed",
                environment=environment,
                version=version,
                error_reason=e.message,
                details={
                    "conflict_reason": e.message,
                    "role": current_role,
                    "step": f"{environment}:{version}",
                    "override_window": override_window,
                    "override_reason": override_reason
                }
            )
        return False, f"{e.message} [{e.code}]", None, None

    try:
        target_config_data = get_config(version)
        target_config = json.loads(target_config_data["config_json"])
    except Exception as e:
        log_error("batch_apply", "CONFIG_READ_ERROR", str(e), environment=environment, version=version)
        log_audit("batch_apply", "failed", environment=environment, version=version, error_reason=str(e))
        return False, f"Failed to read target config: {e}", None, None

    current_version = get_current_version(environment)
    current_config = None

    if current_version:
        current_release = get_release(current_version, environment)
        if current_release:
            current_config = json.loads(current_release["config_json"])

    diff = compute_diff(current_config, target_config)

    if not has_changes(diff):
        err = NoChangesError()
        log_error("batch_apply", err.code, err.message, environment=environment, version=version)
        log_audit("batch_apply", "failed", environment=environment, version=version, error_reason=err.message)
        return False, err.message, None, None

    plan_summary = generate_plan_summary(diff)

    window_override_reason_str = None
    if override_info:
        window_override_reason_str = json.dumps(override_info)
        if not yes:
            click.echo(click.style(f"! Release window overridden: {override_info['override_reason']}", fg="yellow"))
            click.echo(f"  Overridden by: {override_info['overridden_by']}")

    if not yes:
        click.echo("=" * 60)
        click.echo(f"STEP: {environment}:{version}")
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
            conflict_reason=json.dumps({"from_preview": preview_data is not None}) if preview_data else None,
            window_override_reason=window_override_reason_str
        )

        set_current_version(environment, version)

        if preview_data:
            delete_preview(version, environment)

        success_details = {
            **plan_summary,
            "role": current_role,
            "approved_by": approved_by,
            "from_preview": preview_data is not None,
            "step": f"{environment}:{version}"
        }
        if override_info:
            success_details["window_override"] = override_info
        log_audit(
            "batch_apply",
            "success",
            environment=environment,
            version=version,
            details=success_details
        )

        return True, None, None, override_info

    except Exception as e:
        insert_release(
            version,
            environment,
            target_config,
            "failed",
            plan_summary=json.dumps(plan_summary),
            conflict_reason=str(e),
            window_override_reason=window_override_reason_str
        )

        log_error(
            "batch_apply",
            "APPLY_ERROR",
            str(e),
            environment=environment,
            version=version,
            details={
                **plan_summary,
                "conflict_reason": str(e),
                "role": current_role,
                "step": f"{environment}:{version}",
                "override_window": override_window,
                "override_reason": override_reason
            }
        )
        log_audit(
            "batch_apply",
            "failed",
            environment=environment,
            version=version,
            error_reason=str(e),
            details={
                **plan_summary,
                "conflict_reason": str(e),
                "role": current_role,
                "step": f"{environment}:{version}",
                "override_window": override_window,
                "override_reason": override_reason
            }
        )
        return False, f"Failed to apply configuration: {e}", None, None


@click.group()
def batch():
    """Manage batch deployment operations."""
    pass


@batch.command(name="apply")
@click.argument("steps", nargs=-1, required=True)
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--yes", is_flag=True, help="Skip confirmation prompts")
@click.option("--override-window", is_flag=True, help="Override closed release window (release-manager only)")
@click.option("--override-reason", type=click.STRING, default=None, help="Reason for overriding the release window")
def batch_apply(steps, role, yes, override_window, override_reason):
    """Apply multiple versions to environments in batch.
    
    Steps should be in format 'environment:version' (e.g., 'dev:2.0.0 staging:2.0.0').
    
    Each step automatically checks for preview drift and release windows. 
    If drift is detected or release window is closed, the step fails and 
    subsequent steps are skipped. Successful steps are preserved.
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("batch_apply", e.code, e.message)
        log_audit("batch_apply", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    parsed_steps = []
    for step_str in steps:
        try:
            env, version = parse_step(step_str)
            parsed_steps.append((env, version, step_str))
        except click.BadParameter as e:
            log_error("batch_apply", "INVALID_STEP_FORMAT", str(e), details={"step": step_str})
            log_audit("batch_apply", "failed", error_reason=str(e), details={"step": step_str})
            raise e

    click.echo("=" * 60)
    click.echo(f"BATCH APPLY - {len(parsed_steps)} step(s)")
    click.echo("=" * 60)
    for i, (env, version, step_str) in enumerate(parsed_steps):
        click.echo(f"  Step {i+1}: {step_str}")
    click.echo("-" * 60)

    results = []
    failed = False
    batch_success = True
    previous_successful_envs = set()

    for i, (env, version, step_str) in enumerate(parsed_steps):
        step_num = i + 1
        
        if failed:
            click.echo("")
            click.echo(f"  [{step_num}/{len(parsed_steps)}] SKIP: {step_str}")
            click.echo(f"      Reason: Previous step failed")
            results.append({
                "step": step_str,
                "status": "skipped",
                "error_reason": "Previous step failed",
                "drift_reasons": None
            })
            log_audit(
                "batch_apply",
                "skipped",
                environment=env,
                version=version,
                details={"step": step_str, "reason": "Previous step failed"}
            )
            continue

        click.echo("")
        click.echo(f"  [{step_num}/{len(parsed_steps)}] EXECUTING: {step_str}")

        success, error_msg, drift_reasons, override_info = apply_single_step(
            version, env, cli_role=role, yes=yes,
            previous_successful_envs=previous_successful_envs,
            override_window=override_window,
            override_reason=override_reason
        )

        if success:
            click.echo(f"  [{step_num}/{len(parsed_steps)}] SUCCESS: {step_str}")
            click.echo(f"      Environment {env} is now at version {version}")
            if override_info:
                click.echo(f"      Window overridden: {override_info['override_reason']}")
            previous_successful_envs.add(env)
            results.append({
                "step": step_str,
                "status": "success",
                "error_reason": None,
                "drift_reasons": None,
                "override_info": override_info
            })
        else:
            failed = True
            batch_success = False
            click.echo(f"  [{step_num}/{len(parsed_steps)}] FAILED: {step_str}")
            click.echo(f"      Reason: {error_msg}")
            if drift_reasons:
                click.echo(f"      Drift detected:")
                for reason in drift_reasons:
                    click.echo(f"        ! {reason}")
            results.append({
                "step": step_str,
                "status": "failed",
                "error_reason": error_msg,
                "drift_reasons": drift_reasons,
                "override_info": None
            })

    click.echo("")
    click.echo("=" * 60)
    batch_details = {
        "steps": len(parsed_steps),
        "role": current_role,
        "override_window": override_window,
        "override_reason": override_reason
    }
    if batch_success:
        click.echo("BATCH COMPLETED SUCCESSFULLY")
        log_audit(
            "batch_apply",
            "batch_success",
            details=batch_details
        )
    else:
        click.echo("BATCH COMPLETED WITH FAILURES")
        log_audit(
            "batch_apply",
            "batch_failed",
            details=batch_details
        )
    click.echo("=" * 60)

    for r in results:
        if r["status"] == "success":
            status_symbol = "OK"
        elif r["status"] == "failed":
            status_symbol = "FAIL"
        else:
            status_symbol = "SKIP"
        click.echo(f"  [{status_symbol}] {r['step']}: {r['status'].upper()}")
        if r["error_reason"]:
            click.echo(f"      {r['error_reason']}")

    if not batch_success:
        raise click.ClickException("Batch apply completed with failures")
