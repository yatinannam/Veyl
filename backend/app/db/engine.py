from sqlalchemy import Engine, create_engine


def normalise_database_url(url: str) -> str:
    """Hosted providers (Neon, Render) hand out `postgresql://` URLs, which SQLAlchemy maps
    to psycopg2; this project uses psycopg 3."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def create_db_engine(database_url: str, *, connect_timeout_seconds: int = 3) -> Engine:
    """Create a lazily-connecting engine. The short default connect timeout keeps readiness
    checks fast when the database is down."""
    return create_engine(
        normalise_database_url(database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": connect_timeout_seconds},
    )
