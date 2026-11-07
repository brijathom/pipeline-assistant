from alembic import context

from app.database import Base, get_engine
from app import models  # noqa: F401 -- register models with Base.metadata

target_metadata = Base.metadata


def run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied_connection = context.config.attributes.get("connection")
    if supplied_connection is not None:
        run_migrations(supplied_connection)
    else:
        with get_engine().connect() as connection:
            run_migrations(connection)
