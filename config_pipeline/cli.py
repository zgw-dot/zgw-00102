import click
import sys

from .commands.init_cmd import init
from .commands.import_cmd import import_config
from .commands.validate_cmd import validate
from .commands.plan_cmd import plan
from .commands.apply_cmd import apply
from .commands.history_cmd import history
from .commands.rollback_cmd import rollback
from .commands.export_cmd import export
from .utils import PipelineNotInitializedError


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """Configuration Pipeline CLI - Manage application configurations across environments."""
    pass


cli.add_command(init, name="init")
cli.add_command(import_config, name="import")
cli.add_command(validate, name="validate")
cli.add_command(plan, name="plan")
cli.add_command(apply, name="apply")
cli.add_command(history, name="history")
cli.add_command(rollback, name="rollback")
cli.add_command(export, name="export")


def main():
    try:
        cli(standalone_mode=False)
    except PipelineNotInitializedError as e:
        click.echo(f"Error: {e.message}", err=True)
        click.echo("Hint: Run 'pipeline init' to initialize the pipeline.", err=True)
        sys.exit(1)
    except click.ClickException as e:
        click.echo(f"Error: {e.message}", err=True)
        sys.exit(e.exit_code or 1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
