import click
import json
import os

from ..utils import (
    log_audit,
    log_error,
    insert_config,
    config_exists,
    DuplicateVersionError,
    ValidationError,
    REQUIRED_KEYS,
)


def validate_config(config):
    """Validate that the config has all required keys."""
    missing_keys = [key for key in REQUIRED_KEYS if key not in config]
    if missing_keys:
        raise ValidationError(
            f"Configuration is missing required keys: {', '.join(missing_keys)}",
            missing_keys=missing_keys
        )
    
    if "version" in config and not isinstance(config["version"], str):
        raise ValidationError(
            "'version' must be a string",
            invalid_keys=["version"]
        )
    
    return True


@click.command()
@click.argument("file_path", type=click.Path(exists=True, readable=True))
def import_config(file_path):
    """Import a configuration from a JSON file."""
    try:
        with open(file_path, "r") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        log_error("import", "INVALID_JSON", str(e), details={"file": file_path})
        log_audit("import", "failed", error_reason=f"Invalid JSON: {e}")
        raise click.ClickException(f"Invalid JSON file: {e}")

    try:
        validate_config(config)
    except ValidationError as e:
        log_error("import", e.code, e.message, details={"file": file_path, "missing_keys": e.missing_keys})
        log_audit("import", "failed", error_reason=e.message)
        raise click.ClickException(e.message)

    version = config["version"]

    if config_exists(version):
        err = DuplicateVersionError(version, "global")
        log_error("import", err.code, err.message, version=version)
        log_audit("import", "failed", version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    try:
        insert_config(version, config)
        click.echo(f"Successfully imported configuration version {version}")
        click.echo(f"  App name: {config.get('app_name', 'N/A')}")
        click.echo(f"  File: {os.path.abspath(file_path)}")
        log_audit(
            "import",
            "success",
            version=version,
            details={"app_name": config.get("app_name"), "file": file_path}
        )
    except Exception as e:
        log_error("import", "IMPORT_ERROR", str(e), version=version)
        log_audit("import", "failed", version=version, error_reason=str(e))
        raise
