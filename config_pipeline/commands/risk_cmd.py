import click
import json
import os
import hashlib

from ..utils import (
    log_audit,
    log_error,
    get_role,
    check_permission,
    scan_risk,
    get_risk_assessment,
    get_all_risk_assessments,
    verify_risk_assessment,
    approve_risk_assessment,
    revoke_risk_assessment,
    export_risk_assessment,
    import_risk_assessment,
    get_config,
    config_exists,
    EnvironmentError,
    VersionNotFoundError,
    PermissionDeniedError,
    RiskAssessmentNotFoundError,
    RiskAssessmentAlreadyExistsError,
    RiskBlockedReleaseError,
    RiskApprovalRequiredError,
    RiskAlreadyApprovedError,
    RiskAlreadyRevokedError,
    RiskNotApprovedError,
    RiskVerificationFailedError,
    RiskImportConflictError,
    InvalidRiskFormatError,
    RiskSummaryMismatchError,
    RiskHashMismatchError,
    VALID_ENVIRONMENTS,
)


@click.group()
def risk():
    """Manage risk assessments for configuration releases.

    Risk assessments evaluate the risk level of releasing a configuration version
    to an environment, checking for blocking items, required approvals, and
    high-risk features.
    """
    pass


def _format_risk_assessment(risk_data, show_details=True):
    """Format a risk assessment for display."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"RISK ASSESSMENT: {risk_data['version']} -> {risk_data['environment']}")
    lines.append("=" * 70)
    lines.append(f"ID:             {risk_data['id']}")
    lines.append(f"Version:        {risk_data['version']}")
    lines.append(f"Environment:    {risk_data['environment']}")

    level_colors = {
        "critical": "red",
        "high": "red",
        "medium": "yellow",
        "low": "yellow",
        "none": "green",
    }
    color = level_colors.get(risk_data["risk_level"], "white")
    risk_level_display = click.style(risk_data["risk_level"].upper(), fg=color, bold=True)
    lines.append(f"Risk Level:     {risk_level_display}")
    lines.append(f"Risk Score:     {risk_data['risk_score']}")
    lines.append(f"Approval:       {risk_data['approval_status']}")

    if risk_data["approval_status"] == "approved":
        lines.append(f"Approved By:    {risk_data['approved_by']}")
        lines.append(f"Approved At:    {risk_data['approved_at']}")
        if risk_data.get("approval_notes"):
            lines.append(f"Approval Notes: {risk_data['approval_notes']}")

    if risk_data["approval_status"] == "revoked":
        lines.append(f"Revoked By:     {risk_data['revoked_by']}")
        lines.append(f"Revoked At:     {risk_data['revoked_at']}")
        if risk_data.get("revoke_reason"):
            lines.append(f"Revoke Reason:  {risk_data['revoke_reason']}")

    lines.append(f"Created By:     {risk_data['created_by']}")
    lines.append(f"Created At:     {risk_data['created_at']}")

    if show_details:
        lines.append("")
        lines.append("BLOCKING ITEMS:")
        if risk_data["blocking_items"]:
            for item in risk_data["blocking_items"]:
                lines.append(f"  ! {item}")
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append("WARNING ITEMS:")
        if risk_data["warning_items"]:
            for item in risk_data["warning_items"]:
                lines.append(f"  * {item}")
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append("INFO ITEMS:")
        if risk_data["info_items"]:
            for item in risk_data["info_items"]:
                lines.append(f"  - {item}")
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append(f"Config Hash:    {risk_data['config_hash'][:24]}...")
        lines.append(f"Summary Hash:   {risk_data['summary_hash'][:32]}...")

        scan_details = risk_data.get("scan_details", {})
        checks = scan_details.get("checks", [])
        if checks:
            lines.append("")
            lines.append("SCAN CHECKS:")
            for check in checks:
                status = "PASS" if check.get("passed", True) else "FAIL"
                status_color = "green" if check.get("passed", True) else "red"
                status_display = click.style(status, fg=status_color)
                lines.append(f"  [{status_display}] {check.get('name', 'unknown')}: {check.get('details', '')}")

    lines.append("=" * 70)
    return "\n".join(lines)


@risk.command(name="scan")
@click.argument("version")
@click.argument("environment")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def risk_scan(version, environment, role):
    """Perform a risk assessment scan for a version in an environment.

    VERSION: Configuration version to assess
    ENVIRONMENT: Target environment (dev/staging/prod)

    Scans check for:
    - Required approvals
    - Staging verification (for prod)
    - Package signoff (for prod)
    - Release windows
    - Environment locks
    - High-risk features
    - Duplicate versions

    Examples:
        pipeline risk scan 1.0.0 dev
        pipeline risk scan 2.0.0 prod --role release-manager
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("risk.scan", e.code, e.message)
        log_audit("risk.scan", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        if environment not in VALID_ENVIRONMENTS:
            raise EnvironmentError(environment, VALID_ENVIRONMENTS)

        risk_data = scan_risk(version, environment, cli_role=role)

        click.echo(_format_risk_assessment(risk_data))

        if risk_data["blocking_items"]:
            click.echo("")
            click.echo(click.style("RESULT: BLOCKED - Cannot release with blocking items", fg="red", bold=True))
            click.echo(f"  Use 'pipeline risk approve' for high-risk approval if needed")
        elif risk_data["risk_level"] in ["high", "critical"]:
            click.echo("")
            click.echo(click.style(f"RESULT: {risk_data['risk_level'].upper()} RISK - Requires release-manager approval", fg="yellow", bold=True))
            click.echo(f"  Use 'pipeline risk approve {version} {environment} --role release-manager' to approve")
        else:
            click.echo("")
            click.echo(click.style("RESULT: OK - Ready for release", fg="green", bold=True))

    except (EnvironmentError, VersionNotFoundError, PermissionDeniedError) as e:
        log_error("risk.scan", e.code, e.message, environment=environment, version=version)
        log_audit(
            "risk.scan",
            "failed",
            environment=environment,
            version=version,
            error_reason=e.message,
            details={"role": current_role}
        )
        raise click.ClickException(f"{e.message} [{e.code}]")
    except Exception as e:
        log_error("risk.scan", "SCAN_ERROR", str(e), environment=environment, version=version)
        log_audit("risk.scan", "failed", environment=environment, version=version, error_reason=str(e))
        raise click.ClickException(f"Failed to perform risk scan: {e}")


@risk.command(name="view")
@click.argument("version")
@click.argument("environment")
def risk_view(version, environment):
    """View details of a risk assessment.

    VERSION: Configuration version
    ENVIRONMENT: Target environment (dev/staging/prod)

    Examples:
        pipeline risk view 1.0.0 dev
        pipeline risk view 2.0.0 prod
    """
    try:
        risk_data = get_risk_assessment(version=version, environment=environment)
        if not risk_data:
            raise RiskAssessmentNotFoundError(version=version, environment=environment)

        click.echo(_format_risk_assessment(risk_data, show_details=True))

        is_valid, issues = verify_risk_assessment(version=version, environment=environment)
        if is_valid:
            click.echo("VERIFICATION: OK - Risk assessment integrity verified")
        else:
            click.echo("VERIFICATION: FAILED")
            for issue in issues:
                click.echo(f"  ! {issue}")

    except RiskAssessmentNotFoundError as e:
        log_error("risk.view", e.code, e.message)
        log_audit("risk.view", "failed", error_reason=e.message)
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("risk.view", "VIEW_ERROR", str(e))
        log_audit("risk.view", "failed", error_reason=str(e))
        raise click.ClickException(f"Failed to view risk assessment: {e}")


@risk.command(name="list")
@click.option("--env", type=click.STRING, default=None, help="Filter by environment")
@click.option("--level", type=click.Choice(["critical", "high", "medium", "low", "none"]), default=None, help="Filter by risk level")
@click.option("--status", type=click.Choice(["pending", "requires_approval", "approved", "revoked"]), default=None, help="Filter by approval status")
@click.option("--limit", type=click.INT, default=50, help="Maximum number of assessments to show")
def risk_list(env, level, status, limit):
    """List all risk assessments.

    Examples:
        pipeline risk list
        pipeline risk list --env prod
        pipeline risk list --level high
        pipeline risk list --status requires_approval
    """
    try:
        if env is not None and env not in VALID_ENVIRONMENTS:
            raise EnvironmentError(env, VALID_ENVIRONMENTS)

        risks = get_all_risk_assessments(
            environment=env,
            risk_level=level,
            approval_status=status,
            limit=limit
        )

        if not risks:
            click.echo("No risk assessments found.")
            return

        click.echo("=" * 120)
        click.echo(f"{'ID':<5} {'VERSION':<12} {'ENV':<10} {'LEVEL':<10} {'SCORE':<6} {'STATUS':<18} {'CREATED BY':<15} {'CREATED AT':<20}")
        click.echo("-" * 120)

        level_colors = {
            "critical": "red",
            "high": "red",
            "medium": "yellow",
            "low": "yellow",
            "none": "green",
        }

        for r in risks:
            color = level_colors.get(r["risk_level"], "white")
            level_display = click.style(r["risk_level"], fg=color)

            status_colors = {
                "approved": "green",
                "revoked": "red",
                "requires_approval": "yellow",
                "pending": "white",
            }
            status_color = status_colors.get(r["approval_status"], "white")
            status_display = click.style(r["approval_status"], fg=status_color)

            click.echo(
                f"{r['id']:<5} "
                f"{r['version']:<12} "
                f"{r['environment']:<10} "
                f"{level_display:<10} "
                f"{r['risk_score']:<6} "
                f"{status_display:<18} "
                f"{r['created_by']:<15} "
                f"{r['created_at'][:19]:<20}"
            )

        click.echo("=" * 120)
        click.echo(f"Total: {len(risks)} risk assessment(s)")

        log_audit(
            "risk.list",
            "success",
            details={
                "filter_env": env,
                "filter_level": level,
                "filter_status": status,
                "count": len(risks),
            }
        )

    except EnvironmentError as e:
        log_error("risk.list", e.code, e.message)
        log_audit("risk.list", "failed", error_reason=e.message)
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("risk.list", "LIST_ERROR", str(e))
        log_audit("risk.list", "failed", error_reason=str(e))
        raise click.ClickException(f"Failed to list risk assessments: {e}")


@risk.command(name="verify")
@click.argument("version")
@click.argument("environment")
def risk_verify(version, environment):
    """Verify a risk assessment's integrity and hashes.

    VERSION: Configuration version
    ENVIRONMENT: Target environment (dev/staging/prod)

    Checks:
    1. Config version still exists
    2. Config content hasn't changed (hash matches)
    3. Assessment summary hash matches
    4. Approval status is valid (not revoked)

    Examples:
        pipeline risk verify 1.0.0 dev
        pipeline risk verify 2.0.0 prod
    """
    try:
        risk_data = get_risk_assessment(version=version, environment=environment)
        if not risk_data:
            raise RiskAssessmentNotFoundError(version=version, environment=environment)

        click.echo(f"Verifying risk assessment: {version} -> {environment}")
        click.echo(f"Risk Level: {risk_data['risk_level']}")
        click.echo(f"Approval Status: {risk_data['approval_status']}")
        click.echo("")

        click.echo("VERSION CHECK:")
        ver = risk_data["version"]
        expected_hash = risk_data["config_hash"]
        if config_exists(ver):
            cfg = get_config(ver)
            config_data = json.loads(cfg["config_json"])
            actual_hash = hashlib.sha256(
                json.dumps(config_data, sort_keys=True).encode("utf-8")
            ).hexdigest()
            status = "OK" if actual_hash == expected_hash else "MISMATCH"
            status_color = "green" if actual_hash == expected_hash else "red"
            status_display = click.style(status, fg=status_color)
            click.echo(f"  {ver:<10} {status_display} ({expected_hash[:12]}...)")
        else:
            click.echo(f"  {ver:<10} MISSING")

        click.echo("")
        click.echo(f"Summary Hash: {risk_data['summary_hash'][:24]}...")
        click.echo("")

        is_valid, issues = verify_risk_assessment(version=version, environment=environment)

        if is_valid:
            click.echo("=" * 60)
            click.echo("RESULT: VALID - Risk assessment integrity verified")
            click.echo("=" * 60)
            log_audit(
                "risk.verify",
                "success",
                environment=environment,
                version=version,
                details={"risk_level": risk_data["risk_level"]}
            )
        else:
            click.echo("=" * 60)
            click.echo("RESULT: FAILED - Issues found:")
            click.echo("=" * 60)
            for issue in issues:
                click.echo(f"  ! {issue}")

            log_error(
                "risk.verify",
                "RISK_VERIFICATION_FAILED",
                f"Risk verification failed for {version} in {environment}",
                details={"issues": issues}
            )
            log_audit(
                "risk.verify",
                "failed",
                environment=environment,
                version=version,
                error_reason="; ".join(issues),
                details={"issues": issues}
            )
            raise click.ClickException(f"Risk verification failed: {'; '.join(issues)}")

    except RiskAssessmentNotFoundError as e:
        log_error("risk.verify", e.code, e.message)
        log_audit("risk.verify", "failed", error_reason=e.message)
        raise click.ClickException(e.message)


@risk.command(name="approve")
@click.argument("version")
@click.argument("environment")
@click.option("--role", type=click.STRING, default=None, help="User role (must be release-manager)")
@click.option("--notes", type=click.STRING, default=None, help="Approval notes")
def risk_approve(version, environment, role, notes):
    """Approve a high/critical risk assessment (release-manager only).

    VERSION: Configuration version
    ENVIRONMENT: Target environment (dev/staging/prod)

    Examples:
        pipeline risk approve 2.0.0 prod --role release-manager
        pipeline risk approve 1.0.0 staging --role release-manager --notes "Reviewed and approved"
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("risk.approve", e.code, e.message)
        log_audit("risk.approve", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        if environment not in VALID_ENVIRONMENTS:
            raise EnvironmentError(environment, VALID_ENVIRONMENTS)

        check_permission("risk.approve", "release-manager", role)

        risk_data = approve_risk_assessment(version, environment, cli_role=role, notes=notes)

        click.echo(_format_risk_assessment(risk_data, show_details=False))
        click.echo(f"SUCCESS: Risk assessment for {version} in {environment} approved")
        if notes:
            click.echo(f"Notes: {notes}")

    except (EnvironmentError, RiskAssessmentNotFoundError,
            RiskAlreadyApprovedError, RiskAlreadyRevokedError,
            PermissionDeniedError) as e:
        log_error("risk.approve", e.code, e.message, environment=environment, version=version)
        log_audit(
            "risk.approve",
            "failed",
            environment=environment,
            version=version,
            error_reason=e.message,
            details={"role": current_role, "notes": notes}
        )
        raise click.ClickException(f"{e.message} [{e.code}]")
    except Exception as e:
        log_error("risk.approve", "APPROVE_ERROR", str(e), environment=environment, version=version)
        log_audit("risk.approve", "failed", environment=environment, version=version, error_reason=str(e))
        raise click.ClickException(f"Failed to approve risk assessment: {e}")


@risk.command(name="revoke")
@click.argument("version")
@click.argument("environment")
@click.option("--role", type=click.STRING, default=None, help="User role (must be release-manager)")
@click.option("--reason", type=click.STRING, default=None, help="Reason for revoking")
def risk_revoke(version, environment, role, reason):
    """Revoke an approved risk assessment (release-manager only).

    VERSION: Configuration version
    ENVIRONMENT: Target environment (dev/staging/prod)

    Revoking an approved assessment will block releases of that version
    until it is re-approved.

    Examples:
        pipeline risk revoke 2.0.0 prod --role release-manager --reason "Issues found"
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("risk.revoke", e.code, e.message)
        log_audit("risk.revoke", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        if environment not in VALID_ENVIRONMENTS:
            raise EnvironmentError(environment, VALID_ENVIRONMENTS)

        check_permission("risk.revoke", "release-manager", role)

        risk_data = revoke_risk_assessment(version, environment, cli_role=role, reason=reason)

        click.echo(_format_risk_assessment(risk_data, show_details=False))
        click.echo(f"SUCCESS: Risk assessment for {version} in {environment} revoked")
        if reason:
            click.echo(f"Reason: {reason}")
        click.echo(click.style("Releases of this version are now blocked until re-approved.", fg="yellow"))

    except (EnvironmentError, RiskAssessmentNotFoundError,
            RiskNotApprovedError, PermissionDeniedError) as e:
        log_error("risk.revoke", e.code, e.message, environment=environment, version=version)
        log_audit(
            "risk.revoke",
            "failed",
            environment=environment,
            version=version,
            error_reason=e.message,
            details={"role": current_role, "reason": reason}
        )
        raise click.ClickException(f"{e.message} [{e.code}]")
    except Exception as e:
        log_error("risk.revoke", "REVOKE_ERROR", str(e), environment=environment, version=version)
        log_audit("risk.revoke", "failed", environment=environment, version=version, error_reason=str(e))
        raise click.ClickException(f"Failed to revoke risk assessment: {e}")


@risk.command(name="export")
@click.argument("version")
@click.argument("environment")
@click.option("--output", "-o", type=click.STRING, default=None, help="Output file path (stdout if not specified)")
def risk_export(version, environment, output):
    """Export a risk assessment to JSON format.

    VERSION: Configuration version
    ENVIRONMENT: Target environment (dev/staging/prod)

    The exported JSON includes the summary hash for integrity verification
    during import.

    Examples:
        pipeline risk export 1.0.0 dev --output risk_dev_1.0.0.json
        pipeline risk export 2.0.0 prod --output risk_prod_2.0.0.json
    """
    try:
        risk_data = export_risk_assessment(version=version, environment=environment)

        json_str = json.dumps(risk_data, indent=2, ensure_ascii=False)

        if output:
            with open(output, 'w', encoding='utf-8') as f:
                f.write(json_str)
            click.echo(f"SUCCESS: Risk assessment for {version} in {environment} exported to {output}")
            click.echo(f"Summary Hash: {risk_data['summary_hash'][:32]}...")
        else:
            click.echo(json_str)

        log_audit(
            "risk.export",
            "success",
            environment=environment,
            version=version,
            details={"output": output, "summary_hash": risk_data["summary_hash"]}
        )

    except RiskAssessmentNotFoundError as e:
        log_error("risk.export", e.code, e.message)
        log_audit("risk.export", "failed", error_reason=e.message)
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("risk.export", "EXPORT_ERROR", str(e))
        log_audit("risk.export", "failed", error_reason=str(e))
        raise click.ClickException(f"Failed to export risk assessment: {e}")


@risk.command(name="import")
@click.argument("input_file")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--force", is_flag=True, help="Overwrite existing assessment")
def risk_import(input_file, role, force):
    """Import a risk assessment from a JSON file.

    INPUT_FILE: Path to the JSON file containing the exported risk assessment

    The import will verify the summary hash to ensure data integrity.
    Use --force to overwrite an existing assessment.

    Examples:
        pipeline risk import risk_dev_1.0.0.json
        pipeline risk import risk_prod_2.0.0.json --role release-manager --force
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("risk.import", e.code, e.message)
        log_audit("risk.import", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        if not os.path.exists(input_file):
            raise click.ClickException(f"Input file not found: {input_file}")

        with open(input_file, 'r', encoding='utf-8') as f:
            risk_data = json.load(f)

        risk = import_risk_assessment(risk_data, cli_role=role, force=force)

        click.echo(_format_risk_assessment(risk, show_details=True))
        click.echo(f"SUCCESS: Risk assessment for {risk['version']} in {risk['environment']} imported successfully")
        click.echo(f"Summary Hash verified: {risk['summary_hash'][:32]}...")

    except json.JSONDecodeError as e:
        err = InvalidRiskFormatError(f"Invalid JSON: {e}")
        log_error("risk.import", err.code, err.message, details={"input_file": input_file})
        log_audit(
            "risk.import",
            "failed",
            error_reason=err.message,
            details={"input_file": input_file, "role": current_role}
        )
        raise click.ClickException(err.message)
    except (InvalidRiskFormatError, RiskImportConflictError,
            RiskSummaryMismatchError, EnvironmentError,
            PermissionDeniedError) as e:
        log_error("risk.import", e.code, e.message, details={"input_file": input_file})
        log_audit(
            "risk.import",
            "failed",
            error_reason=e.message,
            details={"input_file": input_file, "role": current_role, "force": force}
        )
        raise click.ClickException(f"{e.message} [{e.code}]")
    except Exception as e:
        log_error("risk.import", "IMPORT_ERROR", str(e), details={"input_file": input_file})
        log_audit("risk.import", "failed", error_reason=str(e), details={"input_file": input_file})
        raise click.ClickException(f"Failed to import risk assessment: {e}")
