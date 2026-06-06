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
    create_archive,
    archive_exists,
    get_archive,
    get_all_archives,
    verify_archive,
    revoke_archive,
    export_archive,
    import_archive,
    get_config,
    config_exists,
    get_package,
    EnvironmentError,
    ArchiveAlreadyExistsError,
    ArchiveNotFoundError,
    ArchiveNotSuccessfulReleaseError,
    ArchiveMissingApprovalError,
    ArchiveSummaryMismatchError,
    ArchiveRevokedError,
    ArchiveImportConflictError,
    InvalidArchiveFormatError,
    PackageNotFoundError,
    VALID_ENVIRONMENTS,
)


@click.group()
def archive():
    """Manage release evidence archives for auditable evidence packages."""
    pass


def _format_archive(archive_data, show_details=True):
    """Format an archive for display."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"ARCHIVE: {archive_data['archive_name']}")
    lines.append("=" * 70)
    lines.append(f"Environment:    {archive_data['environment']}")
    lines.append(f"Version:        {archive_data['version']}")
    lines.append(f"Status:         {archive_data['status']}")
    lines.append(f"Created By:     {archive_data['created_by']}")
    lines.append(f"Created At:     {archive_data['created_at']}")

    release_result = archive_data['release_result']
    lines.append("")
    lines.append("RELEASE RESULT:")
    lines.append(f"  Status:       {release_result.get('status')}")
    if release_result.get('release_id'):
        lines.append(f"  Release ID:   {release_result['release_id']}")
    if release_result.get('approved_by'):
        lines.append(f"  Approved By:  {release_result['approved_by']}")
    if release_result.get('released_at'):
        lines.append(f"  Released At:  {release_result['released_at']}")

    if archive_data.get('linked_approval_id'):
        lines.append(f"Linked Approval ID: {archive_data['linked_approval_id']}")
    if archive_data.get('linked_package_id'):
        lines.append(f"Linked Package ID: {archive_data['linked_package_id']}")

    if archive_data['status'] == 'revoked':
        lines.append("")
        lines.append(f"Revoked By:     {archive_data['revoked_by']}")
        lines.append(f"Revoked At:     {archive_data['revoked_at']}")
        if archive_data.get('revoke_reason'):
            lines.append(f"Revoke Reason:  {archive_data['revoke_reason']}")

    if show_details:
        lines.append("")
        lines.append("CONFIG SUMMARY:")
        cfg = archive_data['config_summary']
        lines.append(f"  App Name:     {cfg.get('app_name')}")
        lines.append(f"  Config Hash:  {cfg['config_hash'][:24]}...")
        lines.append(f"  Created By:   {cfg['created_by']}")
        lines.append(f"  Created At:   {cfg['created_at']}")
        if cfg.get('features'):
            lines.append(f"  Features ({len(cfg['features'])}):")
            for feature in cfg['features']:
                lines.append(f"    - {feature}")

        lines.append("")
        lines.append(f"Summary Hash:   {archive_data['summary_hash'][:32]}...")

    lines.append("=" * 70)
    return "\n".join(lines)


@archive.command(name="create")
@click.argument("archive_name")
@click.argument("version")
@click.argument("environment")
@click.option("--linked-package", type=click.STRING, default=None, help="Name of linked change package")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
def archive_create(archive_name, version, environment, linked_package, role):
    """Create a new release evidence archive.

    ARCHIVE_NAME: Unique name for the archive
    VERSION: Configuration version to archive
    ENVIRONMENT: Environment where the version was released (dev/staging/prod)

    Examples:
        pipeline archive create release-2024-01-prod 2.0.0 prod --role release-manager
        pipeline archive create staging-1.0.0 1.0.0 staging
    """
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("archive.create", e.code, e.message)
        log_audit("archive.create", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        if environment not in VALID_ENVIRONMENTS:
            raise EnvironmentError(environment, VALID_ENVIRONMENTS)

        archive_data = create_archive(
            archive_name, version, environment,
            linked_package_name=linked_package,
            cli_role=role
        )

        click.echo(_format_archive(archive_data))
        click.echo(f"SUCCESS: Archive '{archive_name}' created for {version} in {environment}")

    except (EnvironmentError, ArchiveAlreadyExistsError,
            ArchiveNotSuccessfulReleaseError, ArchiveMissingApprovalError,
            PackageNotFoundError) as e:
        log_error("archive.create", e.code, e.message, environment=environment, version=version)
        log_audit(
            "archive.create",
            "failed",
            environment=environment,
            version=version,
            error_reason=e.message,
            details={
                "archive_name": archive_name,
                "linked_package": linked_package,
                "role": current_role,
            }
        )
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("archive.create", "CREATE_ERROR", str(e), environment=environment, version=version)
        log_audit("archive.create", "failed", environment=environment, version=version, error_reason=str(e))
        raise click.ClickException(f"Failed to create archive: {e}")


@archive.command(name="show")
@click.argument("archive_name")
def archive_show(archive_name):
    """Show details of a release evidence archive."""
    try:
        archive_data = get_archive(archive_name)
        if not archive_data:
            raise ArchiveNotFoundError(archive_name)

        click.echo(_format_archive(archive_data, show_details=True))

        is_valid, issues = verify_archive(archive_name)
        if is_valid:
            click.echo("VERIFICATION: OK - Archive integrity verified")
        else:
            click.echo("VERIFICATION: FAILED")
            for issue in issues:
                click.echo(f"  ! {issue}")

    except ArchiveNotFoundError as e:
        log_error("archive.show", e.code, e.message)
        log_audit("archive.show", "failed", error_reason=e.message)
        raise click.ClickException(e.message)


@archive.command(name="list")
@click.option("--env", type=click.STRING, default=None, help="Filter by environment")
@click.option("--status", type=click.Choice(['active', 'revoked']), default=None, help="Filter by status")
@click.option("--limit", type=click.INT, default=50, help="Maximum number of archives to show")
def archive_list(env, status, limit):
    """List all release evidence archives."""
    try:
        archives = get_all_archives(environment=env, status=status, limit=limit)

        if not archives:
            click.echo("No archives found.")
            return

        click.echo("=" * 100)
        click.echo(f"{'NAME':<30} {'ENV':<10} {'VERSION':<12} {'STATUS':<10} {'CREATED BY':<15} {'CREATED AT':<20}")
        click.echo("-" * 100)

        for arch in archives:
            click.echo(
                f"{arch['archive_name']:<30} "
                f"{arch['environment']:<10} "
                f"{arch['version']:<12} "
                f"{arch['status']:<10} "
                f"{arch['created_by']:<15} "
                f"{arch['created_at'][:19]:<20}"
            )

        click.echo("=" * 100)
        click.echo(f"Total: {len(archives)} archive(s)")

    except Exception as e:
        log_error("archive.list", "LIST_ERROR", str(e))
        log_audit("archive.list", "failed", error_reason=str(e))
        raise click.ClickException(f"Failed to list archives: {e}")


@archive.command(name="verify")
@click.argument("archive_name")
def archive_verify(archive_name):
    """Verify an archive's integrity and version hashes."""
    try:
        archive_data = get_archive(archive_name)
        if not archive_data:
            raise ArchiveNotFoundError(archive_name)

        click.echo(f"Verifying archive: {archive_name}")
        click.echo(f"Environment: {archive_data['environment']}")
        click.echo(f"Version: {archive_data['version']}")
        click.echo(f"Status: {archive_data['status']}")
        click.echo("")

        is_valid, issues = verify_archive(archive_name)

        click.echo("VERSION CHECK:")
        version = archive_data['version']
        expected_hash = archive_data['config_summary']['config_hash']
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
        click.echo(f"Summary Hash: {archive_data['summary_hash'][:24]}...")
        click.echo("")

        if is_valid:
            click.echo("=" * 60)
            click.echo("RESULT: VALID - Archive integrity verified")
            click.echo("=" * 60)
            log_audit(
                "archive.verify",
                "success",
                environment=archive_data['environment'],
                version=archive_data['version'],
                details={"archive_name": archive_name}
            )
        else:
            click.echo("=" * 60)
            click.echo("RESULT: FAILED - Issues found:")
            click.echo("=" * 60)
            for issue in issues:
                click.echo(f"  ! {issue}")

            log_error(
                "archive.verify",
                "VERIFY_FAILED",
                f"Archive verification failed for '{archive_name}'",
                details={"issues": issues}
            )
            log_audit(
                "archive.verify",
                "failed",
                environment=archive_data['environment'],
                version=archive_data['version'],
                error_reason="; ".join(issues),
                details={"archive_name": archive_name, "issues": issues}
            )
            raise click.ClickException(f"Archive verification failed: {'; '.join(issues)}")

    except ArchiveNotFoundError as e:
        log_error("archive.verify", e.code, e.message)
        log_audit("archive.verify", "failed", error_reason=e.message)
        raise click.ClickException(e.message)


@archive.command(name="revoke")
@click.argument("archive_name")
@click.option("--role", type=click.STRING, default=None, help="User role (must be release-manager)")
@click.option("--reason", type=click.STRING, default=None, help="Reason for revoking the archive")
def archive_revoke(archive_name, role, reason):
    """Revoke an archive (release-manager only)."""
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("archive.revoke", e.code, e.message)
        log_audit("archive.revoke", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        archive_data = get_archive(archive_name)
        if not archive_data:
            raise ArchiveNotFoundError(archive_name)

        check_permission("archive.revoke", "release-manager", role)

        revoke_archive(archive_name, cli_role=role, reason=reason)

        updated_archive = get_archive(archive_name)
        click.echo(_format_archive(updated_archive, show_details=False))
        click.echo(f"SUCCESS: Archive '{archive_name}' revoked")
        if reason:
            click.echo(f"Reason: {reason}")

    except (ArchiveNotFoundError, ArchiveRevokedError) as e:
        log_error("archive.revoke", e.code, e.message)
        log_audit(
            "archive.revoke",
            "failed",
            error_reason=e.message,
            details={"archive_name": archive_name, "role": current_role, "reason": reason}
        )
        raise click.ClickException(e.message)


@archive.command(name="export")
@click.argument("archive_name")
@click.option("--output", "-o", type=click.STRING, default=None, help="Output file path (stdout if not specified)")
def archive_export(archive_name, output):
    """Export an archive to JSON format."""
    try:
        archive_data = export_archive(archive_name)

        json_str = json.dumps(archive_data, indent=2, ensure_ascii=False)

        if output:
            with open(output, 'w', encoding='utf-8') as f:
                f.write(json_str)
            click.echo(f"SUCCESS: Archive '{archive_name}' exported to {output}")
        else:
            click.echo(json_str)

        log_audit(
            "archive.export",
            "success",
            details={"archive_name": archive_name, "output": output}
        )

    except ArchiveNotFoundError as e:
        log_error("archive.export", e.code, e.message)
        log_audit("archive.export", "failed", error_reason=e.message)
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("archive.export", "EXPORT_ERROR", str(e))
        log_audit("archive.export", "failed", error_reason=str(e))
        raise click.ClickException(f"Failed to export archive: {e}")


@archive.command(name="import")
@click.argument("input_file")
@click.option("--role", type=click.STRING, default=None, help="User role (developer or release-manager)")
@click.option("--force", is_flag=True, help="Overwrite existing archive")
def archive_import(input_file, role, force):
    """Import an archive from a JSON file."""
    try:
        current_role = get_role(role)
    except Exception as e:
        log_error("archive.import", e.code, e.message)
        log_audit("archive.import", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    try:
        if not os.path.exists(input_file):
            raise click.ClickException(f"Input file not found: {input_file}")

        with open(input_file, 'r', encoding='utf-8') as f:
            archive_data = json.load(f)

        arch = import_archive(archive_data, cli_role=role, force=force)

        click.echo(_format_archive(arch, show_details=True))
        click.echo(f"SUCCESS: Archive '{arch['archive_name']}' imported successfully")

    except json.JSONDecodeError as e:
        err = InvalidArchiveFormatError(f"Invalid JSON: {e}")
        log_error("archive.import", err.code, err.message, details={"input_file": input_file})
        log_audit(
            "archive.import",
            "failed",
            error_reason=err.message,
            details={"input_file": input_file, "role": current_role}
        )
        raise click.ClickException(err.message)
    except (InvalidArchiveFormatError, ArchiveImportConflictError,
            ArchiveNotSuccessfulReleaseError, ArchiveMissingApprovalError,
            ArchiveSummaryMismatchError) as e:
        log_error("archive.import", e.code, e.message, details={"input_file": input_file})
        log_audit(
            "archive.import",
            "failed",
            error_reason=e.message,
            details={"input_file": input_file, "role": current_role}
        )
        raise click.ClickException(e.message)
    except Exception as e:
        log_error("archive.import", "IMPORT_ERROR", str(e), details={"input_file": input_file})
        log_audit("archive.import", "failed", error_reason=str(e), details={"input_file": input_file})
        raise click.ClickException(f"Failed to import archive: {e}")
