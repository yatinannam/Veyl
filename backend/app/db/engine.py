from sqlalchemy import Engine, create_engine


def create_db_engine(database_url: str) -> Engine:
    """Create a lazily-connecting engine. The short connect timeout keeps readiness
    checks fast when the database is down."""
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
