import click
import json
import os
import hashlib
import getpass

from ..utils import (
    log_audit,
    log_error,
    get_role,
    check_permission,
    create_package,
    package_exists,
    get_package,
    get_all_packages,
    sign_package,
    revoke_package_signoff,
    verify_package,
    export_package,
    import_package,
    get_config,
    config_exists,
    EnvironmentError,
    PackageAlreadyExistsError,
    PackageNotFoundError,
    PackageVersionNotFoundError,
    PackageSummaryMismatchError,
    PackageAlreadySignedError,
    PackageNotSignedForRevokeError,
    InvalidPackageFormatError,
    VALID_ENVIRONMENTS,
)


@click.group()
def package():
    """Manage change packages for signoff and release tracking."""
    pass


def _validate_versions(versions):
    """Validate that all versions exist in configs."""
    missing = []
    for version in versions:
        if not config_exists(version):
            missing.append(version)
    if missing:
        raise PackageVersionNotFoundError(missing[0])
    return True


def _format_package(pkg, show_details=True):
    """Format a package for display."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"PACKAGE: {pkg['package_name']}")
    lines.append("=" * 60)
    lines.append(f"Target Env:     {pkg['target_environment']}")
    lines.append(f"Status:         {pkg['signoff_status']}")
    lines.append(f"Created By:     {pkg['created_by']}")
    lines.append(f"Created At:     {pkg['created_at']}")
    lines.append(f"Versions ({len(pkg['versions'])}):")
    for version in pkg['versions']:
        lines.append(f"  - {version}")

    if pkg['signoff_status'] == 'signed':
        lines.append(f"Signed By:      {pkg['signed_by']}")
        lines.append(f"Signed At:      {pkg['signed_at']}")

    if pkg.get('revoked_by'):
        lines.append(f"Revoked By:     {pkg['revoked_by']}")
        lines.append(f"Revoked At:     {pkg['revoked_at']}")
        if pkg.get('revoke_reason'):
            lines.append(f"Revoke Reason:  {pkg['revoke_reason']}")

    if show_details:
        lines.append("")
        lines.append("CONFIG SUMMARY:")
        for item in pkg['config_summary']:
            lines.append(f"  {item['version']}:")
            lines.append(f"    Hash:    {item['config_hash'][:16]}...")
            lines.append(f"    By:      {item['created_by']}")
            lines.append(f"    At:      {item['created_at']}")

        lines.append("")
        lines.append(f"Summary Hash:   {pkg['summary_hash'][:24]}...")

    lines.append("=" * 60)
    return "\n".join(lines)


@package.command(name="create")
@click.argument("package_name")
@click.argument("target_environment")
@click.argument("versions", nargs=-1, required=True)
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def package_create(package_name, target_environment, versions, role):
    """Create a new change package.

    PACKAGE_NAME: Unique name for the package
    TARGET_ENVIRONMENT: Target environment (dev/staging/prod)
    VERSIONS: One or more configuration versions to include

    Examples:
        pipeline package create release-2024-01 prod 2.0.0 2.1.0
        pipeline package create hotfix-001 staging 1.9.5
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("package.create", e.code, e.message)
        log_audit("package.create", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        if target_environment not in VALID_ENVIRONMENTS:
            raise EnvironmentError(target_environment, VALID_ENVIRONMENTS)

        _validate_versions(versions)

        pkg = create_package(package_name, target_environment, list(versions), cli_role=role)

        click.echo(_format_package(pkg))
        click.echo(f"SUCCESS: Package '{package_name}' created for {target_environment}")

    except (EnvironmentError, PackageAlreadyExistsError, PackageVersionNotFoundError) as e:
        log_error("package.create", e.code, e.message, environment=target_environment)
        log_audit(
            "package.create",
            "failed",
            environment=target_environment,
            error_reason=e.message,
            details={
                "package_name": package_name,
                "versions": list(versions),
                "role": current_role,
            }
        )
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("package.create", "CREATE_ERROR", str(e), environment=target_environment)
        log_audit("package.create", "failed", environment=target_environment, error_reason=str(e))
        raise click.ClickException(f"Failed to create package: {e}")


@package.command(name="show")
@click.argument("package_name")
def package_show(package_name):
    """Show details of a change package."""
    try:
        pkg = get_package(package_name)
        if not pkg:
            raise PackageNotFoundError(package_name)

        click.echo(_format_package(pkg, show_details=True))

        is_valid, issues = verify_package(package_name)
        if is_valid:
            click.echo("VERIFICATION: OK - All versions intact")
        else:
            click.echo("VERIFICATION: FAILED")
            for issue in issues:
                click.echo(f"  ! {issue}")

    except PackageNotFoundError as e:
        log_error("package.show", e.code, e.message)
        log_audit("package.show", "failed", error_reason=e.message)
        raise click.ClickException(e.message)


@package.command(name="list")
@click.option("--env", type=click.STRING, default=None, help="Filter by target environment")
@click.option("--limit", type=click.INT, default=50, help="Maximum number of packages to show")
def package_list(env, limit):
    """List all change packages."""
    try:
        packages = get_all_packages(environment=env, limit=limit)

        if not packages:
            click.echo("No packages found.")
            return

        click.echo("=" * 90)
        click.echo(f"{'NAME':<25} {'ENV':<10} {'STATUS':<10} {'VERSIONS':<10} {'CREATED BY':<15} {'CREATED AT':<20}")
        click.echo("-" * 90)

        for pkg in packages:
            click.echo(
                f"{pkg['package_name']:<25} "
                f"{pkg['target_environment']:<10} "
                f"{pkg['signoff_status']:<10} "
                f"{len(pkg['versions']):<10} "
                f"{pkg['created_by']:<15} "
                f"{pkg['created_at'][:19]:<20}"
            )

        click.echo("=" * 90)
        click.echo(f"Total: {len(packages)} package(s)")

    except Exception as e:
        log_error("package.list", "LIST_ERROR", str(e))
        log_audit("package.list", "failed", error_reason=str(e))
        raise click.ClickException(f"Failed to list packages: {e}")


@package.command(name="verify")
@click.argument("package_name")
def package_verify(package_name):
    """Verify a package's integrity and version hashes."""
    try:
        pkg = get_package(package_name)
        if not pkg:
            raise PackageNotFoundError(package_name)

        click.echo(f"Verifying package: {package_name}")
        click.echo(f"Target environment: {pkg['target_environment']}")
        click.echo("")

        is_valid, issues = verify_package(package_name)

        click.echo("VERSION CHECKS:")
        for item in pkg['config_summary']:
            version = item['version']
            expected_hash = item['config_hash']
            if config_exists(version):
                cfg = get_config(version)
                config_data = json.loads(cfg['config_json'])
                actual_hash = hashlib.sha256(
                    json.dumps(config_data, sort_keys=True).encode("utf-8")
                ).hexdigest()
                status = "OK" if actual_hash == expected_hash else "MISMATCH"
                click.echo(f"  {version:<10} {status} ({expected_hash[:12]}...)")
            else:
                click.echo(f"  {version:<10} MISSING")

        click.echo("")

        if is_valid:
            click.echo("=" * 60)
            click.echo("RESULT: VALID - Package integrity verified")
            click.echo("=" * 60)
        else:
            click.echo("=" * 60)
            click.echo("RESULT: FAILED - Issues found:")
            click.echo("=" * 60)
            for issue in issues:
                click.echo(f"  ! {issue}")

            log_error(
                "package.verify",
                "VERIFY_FAILED",
                f"Package verification failed for '{package_name}'",
                details={"issues": issues}
            )
            log_audit(
                "package.verify",
                "failed",
                environment=pkg['target_environment'],
                error_reason="; ".join(issues),
                details={"package_name": package_name, "issues": issues}
            )
            raise click.ClickException(f"Package verification failed: {'; '.join(issues)}")

    except PackageNotFoundError as e:
        log_error("package.verify", e.code, e.message)
        log_audit("package.verify", "failed", error_reason=e.message)
        raise click.ClickException(e.message)


@package.command(name="sign")
@click.argument("package_name")
@click.option("--role", type=click.STRING, default=None, help="User role (must be release-manager)")
@click.option("--notes", type=click.STRING, default=None, help="Optional signoff notes")
def package_sign(package_name, role, notes):
    """Sign off a package for release (release-manager only)."""
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("package.sign", e.code, e.message)
        log_audit("package.sign", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        pkg = get_package(package_name)
        if not pkg:
            raise PackageNotFoundError(package_name)

        check_permission("package.sign", "release-manager", role)

        is_valid, issues = verify_package(package_name)
        if not is_valid:
            log_error(
                "package.sign",
                "VERIFY_FAILED",
                f"Cannot sign package '{package_name}' - verification failed",
                details={"issues": issues}
            )
            log_audit(
                "package.sign",
                "failed",
                environment=pkg['target_environment'],
                error_reason=f"Verification failed: {'; '.join(issues)}",
                details={"package_name": package_name, "issues": issues, "role": current_role}
            )
            raise click.ClickException(
                f"Cannot sign package. Verification failed: {'; '.join(issues)}"
            )

        sign_package(package_name, cli_role=role, notes=notes)

        updated_pkg = get_package(package_name)
        click.echo(_format_package(updated_pkg, show_details=False))
        click.echo(f"SUCCESS: Package '{package_name}' signed by {getpass.getuser()}")

    except (PackageNotFoundError, PackageAlreadySignedError) as e:
        log_error("package.sign", e.code, e.message)
        log_audit(
            "package.sign",
            "failed",
            error_reason=e.message,
            details={"package_name": package_name, "role": current_role}
        )
        raise click.ClickException(e.message)


@package.command(name="revoke")
@click.argument("package_name")
@click.option("--role", type=click.STRING, default=None, help="User role (must be release-manager)")
@click.option("--reason", type=click.STRING, default=None, help="Reason for revoking signoff")
def package_revoke(package_name, role, reason):
    """Revoke a package signoff (release-manager only)."""
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("package.revoke", e.code, e.message)
        log_audit("package.revoke", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        pkg = get_package(package_name)
        if not pkg:
            raise PackageNotFoundError(package_name)

        check_permission("package.revoke", "release-manager", role)

        revoke_package_signoff(package_name, cli_role=role, reason=reason)

        updated_pkg = get_package(package_name)
        click.echo(_format_package(updated_pkg, show_details=False))
        click.echo(f"SUCCESS: Package '{package_name}' signoff revoked")
        if reason:
            click.echo(f"Reason: {reason}")

    except (PackageNotFoundError, PackageNotSignedForRevokeError) as e:
        log_error("package.revoke", e.code, e.message)
        log_audit(
            "package.revoke",
            "failed",
            error_reason=e.message,
            details={"package_name": package_name, "role": current_role, "reason": reason}
        )
        raise click.ClickException(e.message)


@package.command(name="export")
@click.argument("package_name")
@click.option("--output", "-o", type=click.STRING, default=None, help="Output file path (stdout if not specified)")
def package_export(package_name, output):
    """Export a package to JSON format."""
    try:
        pkg_data = export_package(package_name)

        json_str = json.dumps(pkg_data, indent=2, ensure_ascii=False)

        if output:
            with open(output, 'w', encoding='utf-8') as f:
                f.write(json_str)
            click.echo(f"SUCCESS: Package '{package_name}' exported to {output}")
        else:
            click.echo(json_str)

        log_audit(
            "package.export",
            "success",
            details={"package_name": package_name, "output": output}
        )

    except PackageNotFoundError as e:
        log_error("package.export", e.code, e.message)
        log_audit("package.export", "failed", error_reason=e.message)
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("package.export", "EXPORT_ERROR", str(e))
        log_audit("package.export", "failed", error_reason=str(e))
        raise click.ClickException(f"Failed to export package: {e}")


@package.command(name="import")
@click.argument("input_file")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--force", is_flag=True, help="Overwrite existing package")
def package_import(input_file, role, force):
    """Import a package from a JSON file."""
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("package.import", e.code, e.message)
        log_audit("package.import", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        if not os.path.exists(input_file):
            raise click.ClickException(f"Input file not found: {input_file}")

        with open(input_file, 'r', encoding='utf-8') as f:
            package_data = json.load(f)

        pkg = import_package(package_data, cli_role=role, force=force)

        click.echo(_format_package(pkg, show_details=True))
        click.echo(f"SUCCESS: Package '{pkg['package_name']}' imported successfully")

    except json.JSONDecodeError as e:
        err = InvalidPackageFormatError(f"Invalid JSON: {e}")
        log_error("package.import", err.code, err.message, details={"input_file": input_file})
        log_audit(
            "package.import",
            "failed",
            error_reason=err.message,
            details={"input_file": input_file, "role": current_role}
        )
        raise click.ClickException(err.message)
    except (InvalidPackageFormatError, PackageAlreadyExistsError,
            PackageVersionNotFoundError, PackageSummaryMismatchError) as e:
        log_error("package.import", e.code, e.message, details={"input_file": input_file})
        log_audit(
            "package.import",
            "failed",
            error_reason=e.message,
            details={"input_file": input_file, "role": current_role}
        )
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("package.import", "IMPORT_ERROR", str(e), details={"input_file": input_file})
        log_audit("package.import", "failed", error_reason=str(e), details={"input_file": input_file})
        raise click.ClickException(f"Failed to import package: {e}")
