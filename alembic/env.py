"""Alembic env — reads DATABASE_URL from environment."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

try:  # make `alembic` CLI work standalone (the app loads .env elsewhere)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

# Import all models so Alembic detects them
import db.models  # noqa: E402, F401
from db.base import Base, get_engine  # noqa: E402

target_metadata = Base.metadata

# Both modes reuse db.base.get_engine so the DSN normalization (psycopg v3 driver +
# component parsing for special chars in the password) lives in exactly one place.
def run_migrations_offline() -> None:
    url = get_engine().url.render_as_string(hide_password=False)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
