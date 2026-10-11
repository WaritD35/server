"""Worker process entrypoint and ad-hoc task runner.

Run migrations:
    python src/main_worker.py migrate

Or via docker compose:
    docker compose run --rm worker python src/main_worker.py migrate
"""

import sys


def run_migrations() -> None:
    """Run Alembic database migrations to upgrade head."""
    from alembic.config import main as alembic_main

    alembic_main(argv=["upgrade", "head"])


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        run_migrations()
    else:
        print("Worker ready. Pass a task command (e.g. 'migrate').")


if __name__ == "__main__":
    main()
