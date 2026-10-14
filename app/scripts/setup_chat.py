"""Initialize LangGraph's own tables after application migrations."""

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import Engine, text

from app.db import engine

SETUP_LOCK = 72841901


def setup_checkpoints(database_engine: Engine) -> None:
    # DDL is coordinated across replicas; package setup manages its own versions.
    with database_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": SETUP_LOCK})
        try:
            PostgresSaver(connection.connection.driver_connection).setup()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SETUP_LOCK})


if __name__ == "__main__":
    setup_checkpoints(engine)
    print("Chat checkpoint tables are ready.")
