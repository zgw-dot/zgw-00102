import click
import os

from ..utils import init_db, is_initialized, log_audit, VALID_ENVIRONMENTS


@click.command()
def init():
    """Initialize the configuration pipeline database."""
    db_path = os.path.join(os.getcwd(), "pipeline.db")
    
    if is_initialized():
        click.echo(f"Pipeline already initialized at {db_path}")
        log_audit("init", "skipped", details={"reason": "already_initialized"})
        return

    try:
        init_db()
        click.echo(f"Pipeline initialized successfully at {db_path}")
        click.echo(f"Environments configured: {', '.join(VALID_ENVIRONMENTS)}")
        log_audit("init", "success")
    except Exception as e:
        log_audit("init", "failed", error_reason=str(e))
        raise
