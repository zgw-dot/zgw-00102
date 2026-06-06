import click
import json
import os

from .apply_cmd import pre_apply_checks
from ..utils import (
    log_audit,
    log_error,
    get_role,
    check_permission,
    get_current_time,
    create_batch,
    get_batch,
    get_all_batches,
    batch_name_exists,
    update_batch_status,
    update_batch_notes,
    create_batch_step,
    update_batch_step,
    reset_failed_batch_steps,
    get_first_pending_step,
    set_remaining_steps_skipped,
    compute_batch_status,
    export_batch,
    import_batch,
    config_exists,
    get_config,
    get_current_version,
    get_release,
    has_successful_release,
    set_current_version,
    insert_release,
    get_approval,
    compute_diff,
    has_changes,
    format_diff,
    generate_plan_summary,
    EnvironmentError,
    VersionNotFoundError,
    DuplicateVersionError,
    StagingRequiredError,
    EnvironmentLockedError,
    ApprovalRequiredError,
    PermissionDeniedError,
    BatchNotFoundError,
    BatchNameExistsError,
    BatchEmptyError,
    BatchStepInvalidError,
    BatchImportConflictError,
    VALID_ENVIRONMENTS,
    BATCH_STATUSES,
    STEP_STATUSES,
)


def _parse_step_arg(step_arg, idx):
    """Parse a step argument of form 'ENV:VERSION'."""
    if ":" not in step_arg:
        raise BatchStepInvalidError(idx, f"invalid format '{step_arg}'. Use 'ENV:VERSION'")
    
    env, version = step_arg.split(":", 1)
    if env not in VALID_ENVIRONMENTS:
        raise BatchStepInvalidError(idx, f"invalid environment '{env}'. Must be one of: {', '.join(VALID_ENVIRONMENTS)}")
    
    if not config_exists(version):
        raise BatchStepInvalidError(idx, f"version '{version}' does not exist")
    
    return env, version.strip()


def _get_batch_identifier(ctx, batch_ref):
    """Get batch by name or ID."""
    if batch_ref.isdigit():
        batch = get_batch(batch_id=int(batch_ref))
        if not batch:
            raise BatchNotFoundError(batch_id=int(batch_ref))
    else:
        batch = get_batch(batch_name=batch_ref)
        if not batch:
            raise BatchNotFoundError(batch_name=batch_ref)
    return batch


def _status_color(status):
    """Get color for a status."""
    colors = {
        "pending": "yellow",
        "running": "blue",
        "success": "green",
        "failed": "red",
        "skipped": "bright_black",
        "partial": "magenta",
    }
    return colors.get(status, "white")


def _print_batch_summary(batch):
    """Print a batch summary."""
    status = batch["status"]
    click.echo("=" * 80)
    click.echo(f"BATCH #{batch['id']}: {batch['name']}")
    click.echo("=" * 80)
    click.echo(f"Status:         {click.style(status.upper(), fg=_status_color(status), bold=True)}")
    if batch.get("description"):
        click.echo(f"Description:    {batch['description']}")
    click.echo(f"Created by:     {batch['created_by']}")
    click.echo(f"Created at:     {batch['created_at']}")
    if batch.get("started_at"):
        click.echo(f"Started at:     {batch['started_at']}")
    if batch.get("completed_at"):
        click.echo(f"Completed at:   {batch['completed_at']}")
    if batch.get("notes"):
        click.echo(f"Notes:          {batch['notes']}")
    click.echo("-" * 80)
    click.echo(f"{'#':<4} {'Env':<10} {'Version':<12} {'Status':<12} {'Updated At':<25} {'Error'}")
    click.echo("-" * 80)
    for step in batch["steps"]:
        status = step["status"]
        error = step.get("error_reason", "") or ""
        if len(error) > 40:
            error = error[:37] + "..."
        click.echo(
            f"{step['step_index']:<4} {step['environment']:<10} {step['version']:<12} "
            f"{click.style(status.upper(), fg=_status_color(status)):<20} "
            f"{step['updated_at']:<25} {error}"
        )


@click.group()
def batch():
    """Manage release batches - sequential multi-step deployments across environments."""
    pass


@batch.command(name="create")
@click.argument("name")
@click.argument("steps", nargs=-1)
@click.option("--description", type=click.STRING, default=None, help="Batch description")
@click.option("--notes", type=click.STRING, default=None, help="Batch notes")
def batch_create(name, steps, description, notes):
    """Create a new release batch.
    
    STEPS are specified as ENV:VERSION pairs in execution order.
    
    Example:
      pipeline batch create release-1.0.0 dev:1.0.0 staging:1.0.0 prod:1.0.0
    """
    try:
        current_role = get_role()
    except Exception as e:
        log_error("batch_create", e.code, e.message)
        log_audit("batch_create", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    if batch_name_exists(name):
        err = BatchNameExistsError(name)
        log_error("batch_create", err.code, err.message, details={"batch_name": name})
        log_audit("batch_create", "failed", error_reason=err.message, details={"batch_name": name})
        raise click.ClickException(err.message)

    if not steps:
        err = BatchEmptyError(name)
        log_error("batch_create", err.code, err.message, details={"batch_name": name})
        log_audit("batch_create", "failed", error_reason=err.message, details={"batch_name": name})
        raise click.ClickException(err.message)

    parsed_steps = []
    try:
        for idx, step_arg in enumerate(steps):
            env, version = _parse_step_arg(step_arg, idx)
            parsed_steps.append((idx, env, version))
    except BatchStepInvalidError as e:
        log_error("batch_create", e.code, e.message, details={"batch_name": name})
        log_audit("batch_create", "failed", error_reason=e.message, details={"batch_name": name})
        raise click.ClickException(e.message)

    try:
        batch_id = create_batch(name, description=description, notes=notes)
        if not batch_id:
            raise click.ClickException(f"Failed to create batch '{name}'")

        for idx, env, version in parsed_steps:
            create_batch_step(batch_id, idx, env, version)

        click.echo(f"SUCCESS: Batch '{name}' (#{batch_id}) created with {len(parsed_steps)} steps")
        click.echo("  Steps:")
        for idx, env, version in parsed_steps:
            click.echo(f"    {idx}: {env} -> {version}")

        log_audit(
            "batch_create",
            "success",
            details={
                "batch_id": batch_id,
                "batch_name": name,
                "step_count": len(parsed_steps),
                "steps": [{"env": e, "version": v} for _, e, v in parsed_steps],
                "role": current_role,
            }
        )

    except Exception as e:
        log_error("batch_create", "CREATE_ERROR", str(e), details={"batch_name": name})
        log_audit("batch_create", "failed", error_reason=str(e), details={"batch_name": name})
        raise click.ClickException(f"Failed to create batch: {e}")


@batch.command(name="list")
@click.option("--limit", type=click.INT, default=50, help="Maximum number of batches to show")
def batch_list(limit):
    """List all release batches."""
    try:
        current_role = get_role()
    except Exception as e:
        log_error("batch_list", e.code, e.message)
        raise click.ClickException(e.message)

    try:
        batches = get_all_batches(limit=limit)
        if not batches:
            click.echo("No batches found")
            return

        click.echo("=" * 80)
        click.echo(f"{'ID':<5} {'Name':<25} {'Status':<12} {'Steps':<8} {'Created At':<20} {'By':<15}")
        click.echo("-" * 80)
        for b in batches:
            step_count = len(b["steps"])
            success_count = sum(1 for s in b["steps"] if s["status"] == "success")
            status = b["status"]
            click.echo(
                f"{b['id']:<5} {b['name']:<25} "
                f"{click.style(status.upper(), fg=_status_color(status)):<20} "
                f"{success_count}/{step_count:<10} "
                f"{b['created_at']:<20} {b['created_by']:<15}"
            )

        log_audit("batch_list", "success", details={"count": len(batches), "role": current_role})

    except Exception as e:
        log_error("batch_list", "LIST_ERROR", str(e))
        log_audit("batch_list", "failed", error_reason=str(e))
        raise click.ClickException(f"Failed to list batches: {e}")


@batch.command(name="show")
@click.argument("batch_ref")
def batch_show(batch_ref):
    """Show details of a specific batch.
    
    BATCH_REF can be a batch name or numeric ID.
    """
    try:
        current_role = get_role()
    except Exception as e:
        log_error("batch_show", e.code, e.message)
        raise click.ClickException(e.message)

    try:
        batch = _get_batch_identifier(None, batch_ref)
        _print_batch_summary(batch)

        log_audit(
            "batch_show",
            "success",
            details={"batch_id": batch["id"], "batch_name": batch["name"], "role": current_role}
        )

    except BatchNotFoundError as e:
        log_error("batch_show", e.code, e.message)
        log_audit("batch_show", "failed", error_reason=e.message)
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("batch_show", "SHOW_ERROR", str(e))
        log_audit("batch_show", "failed", error_reason=str(e))
        raise click.ClickException(f"Failed to show batch: {e}")


@batch.command(name="apply")
@click.argument("batch_ref")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
@click.option("--retry", is_flag=True, help="Reset failed/skipped steps and retry")
def batch_apply(batch_ref, role, yes, retry):
    """Apply a batch, executing pending steps sequentially.
    
    Resumes from first pending step if batch was previously interrupted.
    Stops at first failure; successful steps remain.
    
    BATCH_REF can be a batch name or numeric ID.
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("batch_apply", e.code, e.message)
        log_audit("batch_apply", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        batch = _get_batch_identifier(None, batch_ref)
    except BatchNotFoundError as e:
        log_error("batch_apply", e.code, e.message)
        log_audit("batch_apply", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    if not batch["steps"]:
        err = BatchEmptyError(batch["name"])
        log_error("batch_apply", err.code, err.message)
        log_audit("batch_apply", "failed", error_reason=err.message, details={"batch_name": batch["name"]})
        raise click.ClickException(err.message)

    if retry:
        reset_count = reset_failed_batch_steps(batch["id"])
        if reset_count > 0:
            click.echo(f"Reset {reset_count} failed/skipped steps to pending")
        batch = get_batch(batch_id=batch["id"])

    pending_steps = [s for s in batch["steps"] if s["status"] == "pending"]
    if not pending_steps:
        current_status = compute_batch_status(batch["id"])
        if current_status == "success":
            click.echo(f"Batch '{batch['name']}' already completed successfully")
        else:
            click.echo(f"Batch '{batch['name']}' has no pending steps. Status: {current_status}")
            click.echo("Use --retry to reset failed/skipped steps")
        return

    if not yes:
        click.echo("=" * 80)
        click.echo(f"BATCH: {batch['name']}")
        click.echo("=" * 80)
        click.echo(f"Pending steps: {len(pending_steps)}")
        for s in pending_steps:
            click.echo(f"  {s['step_index']}: {s['environment']} -> {s['version']}")
        
        confirm = click.confirm(
            f"\nAre you sure you want to apply batch '{batch['name']}'?",
            default=False
        )
        if not confirm:
            click.echo("Batch apply cancelled.")
            log_audit(
                "batch_apply",
                "cancelled",
                details={"batch_id": batch["id"], "batch_name": batch["name"], "role": current_role}
            )
            return

    update_batch_status(batch["id"], "running", started_at=get_current_time())
    log_audit(
        "batch_apply",
        "started",
        details={"batch_id": batch["id"], "batch_name": batch["name"], "role": current_role}
    )

    click.echo("")
    click.echo("=" * 80)
    click.echo(f"EXECUTING BATCH: {batch['name']}")
    click.echo("=" * 80)

    failed = False
    failed_step_idx = None
    batch_ended = False

    try:
        while True:
            step = get_first_pending_step(batch["id"])
            if not step:
                break

            step_idx = step["step_index"]
            env = step["environment"]
            version = step["version"]

            click.echo("")
            click.echo(f"--- Step {step_idx}: {env} -> {version} ---")

            update_batch_step(step["id"], "running")

            try:
                _execute_batch_step(step, role=role)
                update_batch_step(step["id"], "success")
                click.echo(f"  {click.style('SUCCESS', fg='green')}: Step {step_idx} completed")
                log_audit(
                    "batch_step",
                    "success",
                    environment=env,
                    version=version,
                    details={"batch_id": batch["id"], "step_index": step_idx, "role": current_role}
                )

            except (EnvironmentError, VersionNotFoundError, DuplicateVersionError,
                    StagingRequiredError, EnvironmentLockedError, ApprovalRequiredError,
                    PermissionDeniedError) as e:
                update_batch_step(step["id"], "failed", error_reason=e.message)
                click.echo(f"  {click.style('FAILED', fg='red')}: {e.message}")
                log_error(
                    "batch_step", e.code, e.message,
                    environment=env, version=version,
                    details={"batch_id": batch["id"], "step_index": step_idx}
                )
                log_audit(
                    "batch_step",
                    "failed",
                    environment=env,
                    version=version,
                    error_reason=e.message,
                    details={"batch_id": batch["id"], "step_index": step_idx, "role": current_role}
                )
                failed = True
                failed_step_idx = step_idx
                break

            except Exception as e:
                update_batch_step(step["id"], "failed", error_reason=str(e))
                click.echo(f"  {click.style('FAILED', fg='red')}: {e}")
                log_error(
                    "batch_step", "STEP_ERROR", str(e),
                    environment=env, version=version,
                    details={"batch_id": batch["id"], "step_index": step_idx}
                )
                log_audit(
                    "batch_step",
                    "failed",
                    environment=env,
                    version=version,
                    error_reason=str(e),
                    details={"batch_id": batch["id"], "step_index": step_idx, "role": current_role}
                )
                failed = True
                failed_step_idx = step_idx
                break

        if failed and failed_step_idx is not None:
            skipped = set_remaining_steps_skipped(batch["id"], failed_step_idx)
            if skipped > 0:
                click.echo(f"  Skipped {skipped} remaining steps due to failure")

        final_status = compute_batch_status(batch["id"])
        update_batch_status(batch["id"], final_status, completed_at=get_current_time())
        batch_ended = True

        click.echo("")
        click.echo("=" * 80)
        if final_status == "success":
            click.echo(f"{click.style('BATCH COMPLETED', fg='green', bold=True)}: {batch['name']}")
            log_audit(
                "batch_apply",
                "success",
                details={"batch_id": batch["id"], "batch_name": batch["name"], "role": current_role}
            )
        else:
            click.echo(f"{click.style('BATCH STOPPED', fg='red', bold=True)}: {batch['name']}")
            click.echo(f"Status: {click.style(final_status.upper(), fg=_status_color(final_status))}")
            click.echo("Fix the issue and re-run 'batch apply' with --retry")
            log_audit(
                "batch_apply",
                final_status,
                details={"batch_id": batch["id"], "batch_name": batch["name"], "role": current_role}
            )
        click.echo("=" * 80)

        final_batch = get_batch(batch_id=batch["id"])
        _print_batch_summary(final_batch)

    except Exception as e:
        if not batch_ended:
            final_status = compute_batch_status(batch["id"])
            update_batch_status(batch["id"], final_status, completed_at=get_current_time())
        log_error("batch_apply", "APPLY_ERROR", str(e), details={"batch_name": batch["name"]})
        log_audit(
            "batch_apply",
            "failed",
            error_reason=str(e),
            details={"batch_id": batch["id"], "batch_name": batch["name"], "role": current_role}
        )
        raise click.ClickException(f"Batch execution failed: {e}")


def _execute_batch_step(step, role=None):
    """Execute a single batch step by applying the version to the environment.
    
    Reuses existing apply logic and checks.
    """
    env = step["environment"]
    version = step["version"]

    pre_apply_checks(version, env, cli_role=role)

    try:
        target_config_data = get_config(version)
        target_config = json.loads(target_config_data["config_json"])
    except Exception as e:
        raise click.ClickException(f"Failed to read target config: {e}")

    current_version = get_current_version(env)
    current_config = None

    if current_version:
        current_release = get_release(current_version, env)
        if current_release:
            current_config = json.loads(current_release["config_json"])

    diff = compute_diff(current_config, target_config)

    if not has_changes(diff):
        pass

    plan_summary = generate_plan_summary(diff)

    approval = get_approval(version, env)
    approved_by = approval["approved_by"] if approval and approval.get("approved_by") else None

    insert_release(
        version,
        env,
        target_config,
        "success",
        plan_summary=json.dumps(plan_summary),
        approved_by=approved_by,
    )

    set_current_version(env, version)


@batch.command(name="export")
@click.argument("batch_ref")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output JSON file path")
def batch_export(batch_ref, output):
    """Export a batch to a JSON file.
    
    BATCH_REF can be a batch name or numeric ID.
    """
    try:
        current_role = get_role()
    except Exception as e:
        log_error("batch_export", e.code, e.message)
        raise click.ClickException(e.message)

    try:
        batch = _get_batch_identifier(None, batch_ref)
        export_data = export_batch(batch["id"])

        with open(output, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        click.echo(f"SUCCESS: Batch '{batch['name']}' exported to {output}")
        click.echo(f"  Steps: {len(batch['steps'])}")
        click.echo(f"  Status: {batch['status']}")

        log_audit(
            "batch_export",
            "success",
            details={
                "batch_id": batch["id"],
                "batch_name": batch["name"],
                "output": output,
                "role": current_role,
            }
        )

    except BatchNotFoundError as e:
        log_error("batch_export", e.code, e.message)
        log_audit("batch_export", "failed", error_reason=e.message)
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("batch_export", "EXPORT_ERROR", str(e), details={"output": output})
        log_audit("batch_export", "failed", error_reason=str(e), details={"output": output})
        raise click.ClickException(f"Failed to export batch: {e}")


@batch.command(name="import")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--force", is_flag=True, help="Override name conflicts and state conflicts")
def batch_import(file_path, role, force):
    """Import a batch from a JSON file.
    
    By default, rejects if:
    - Batch name already exists
    - Step status conflicts with database state
    
    Use --force to override these checks.
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("batch_import", e.code, e.message)
        log_audit("batch_import", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            export_data = json.load(f)
    except Exception as e:
        log_error("batch_import", "READ_ERROR", str(e), details={"file": file_path})
        log_audit("batch_import", "failed", error_reason=str(e), details={"file": file_path})
        raise click.ClickException(f"Failed to read import file: {e}")

    try:
        success, message, details = import_batch(export_data, force=force, role=current_role)

        if not success:
            log_error(
                "batch_import",
                "IMPORT_REJECTED",
                message,
                details={"file": file_path, "force": force, **details}
            )
            log_audit(
                "batch_import",
                "failed",
                error_reason=message,
                details={"file": file_path, "force": force, "role": current_role, **details}
            )
            raise click.ClickException(message)

        click.echo(f"SUCCESS: {message}")
        click.echo(f"  Steps imported: {details.get('steps_imported', 0)}")
        if details.get("conflicts_overridden"):
            click.echo(f"  Name conflicts overridden: {len(details['conflicts_overridden'])}")
        if details.get("state_conflicts_overridden"):
            click.echo(f"  State conflicts overridden: {len(details['state_conflicts_overridden'])}")

    except PermissionDeniedError as e:
        log_error("batch_import", e.code, e.message)
        log_audit("batch_import", "failed", error_reason=e.message, details={"role": current_role})
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("batch_import", "IMPORT_ERROR", str(e), details={"file": file_path, "force": force})
        log_audit("batch_import", "failed", error_reason=str(e), details={"file": file_path, "force": force, "role": current_role})
        raise click.ClickException(f"Failed to import batch: {e}")
