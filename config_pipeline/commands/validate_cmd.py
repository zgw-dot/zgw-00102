import click
import json

from ..utils import (
    log_audit,
    log_error,
    get_config,
    config_exists,
    ValidationError,
    EnvironmentError,
    VersionNotFoundError,
    REQUIRED_KEYS,
    VALID_ENVIRONMENTS,
)


def validate_environment(env):
    """Validate that the environment is valid."""
    if env not in VALID_ENVIRONMENTS:
        raise EnvironmentError(env, VALID_ENVIRONMENTS)
    return True


def validate_config_full(config):
    """Full validation of configuration including nested structures."""
    errors = []
    missing_keys = []
    invalid_keys = []

    for key in REQUIRED_KEYS:
        if key not in config:
            missing_keys.append(key)

    if missing_keys:
        errors.append(f"Missing required keys: {', '.join(missing_keys)}")

    if "version" in config:
        if not isinstance(config["version"], str):
            invalid_keys.append("version (must be string)")
            errors.append("'version' must be a string")

    if "features" in config:
        if not isinstance(config["features"], dict):
            invalid_keys.append("features (must be object)")
            errors.append("'features' must be an object")

    if "database" in config:
        if not isinstance(config["database"], dict):
            invalid_keys.append("database (must be object)")
            errors.append("'database' must be an object")
        else:
            if "host" not in config["database"]:
                missing_keys.append("database.host")
                errors.append("Missing 'database.host'")
            if "port" not in config["database"]:
                missing_keys.append("database.port")
                errors.append("Missing 'database.port'")

    if "api_endpoints" in config:
        if not isinstance(config["api_endpoints"], list):
            invalid_keys.append("api_endpoints (must be list)")
            errors.append("'api_endpoints' must be a list")

    if errors:
        raise ValidationError(
            " ; ".join(errors),
            missing_keys=missing_keys,
            invalid_keys=invalid_keys
        )

    return True


@click.command()
@click.argument("version")
@click.option("--env", type=click.STRING, default=None, help="Target environment for additional validation")
def validate(version, env):
    """Validate a configuration version."""
    if env is not None:
        try:
            validate_environment(env)
        except EnvironmentError as e:
            log_error("validate", e.code, e.message, environment=env)
            log_audit("validate", "failed", environment=env, version=version, error_reason=e.message)
            raise click.ClickException(e.message)

    if not config_exists(version):
        err = VersionNotFoundError(version)
        log_error("validate", err.code, err.message, version=version, environment=env)
        log_audit("validate", "failed", environment=env, version=version, error_reason=err.message)
        raise click.ClickException(err.message)

    try:
        config_data = get_config(version)
        config = json.loads(config_data["config_json"])
    except Exception as e:
        log_error("validate", "CONFIG_READ_ERROR", str(e), version=version)
        log_audit("validate", "failed", version=version, error_reason=str(e))
        raise click.ClickException(f"Failed to read config: {e}")

    try:
        validate_config_full(config)
    except ValidationError as e:
        log_error(
            "validate",
            e.code,
            e.message,
            version=version,
            environment=env,
            details={"missing_keys": e.missing_keys, "invalid_keys": e.invalid_keys}
        )
        log_audit("validate", "failed", environment=env, version=version, error_reason=e.message)
        raise click.ClickException(e.message)

    click.echo(f"Configuration version {version} is valid.")
    click.echo(f"  App name: {config.get('app_name')}")
    click.echo(f"  Features: {len(config.get('features', {}))} feature(s)")
    click.echo(f"  API endpoints: {len(config.get('api_endpoints', []))} endpoint(s)")
    if env:
        click.echo(f"  Target environment: {env}")

    log_audit(
        "validate",
        "success",
        environment=env,
        version=version,
        details={"app_name": config.get("app_name")}
    )
