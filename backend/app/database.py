"""Database engine. Points at Supabase Postgres.

DATABASE_URL comes from Supabase → Project Settings → Database → Connection
string → URI. Use the **Session pooler** (port 5432) with SQLAlchemy:

    postgresql+psycopg2://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres

The transaction pooler (port 6543) also works but doesn't support prepared
statements, so it needs NullPool - handled automatically below.

SQLite is still accepted so local development works without a network, but
it is no longer the default.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

from .config import settings

url = settings.database_url

if url.startswith("sqlite"):
    engine = create_engine(url, connect_args={"check_same_thread": False})
elif ":6543" in url:
    # Transaction pooler: no prepared statements, no client-side pooling.
    engine = create_engine(url, poolclass=NullPool, pool_pre_ping=True)
else:
    # Session pooler / direct connection.
    engine = create_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,   # drops dead connections instead of erroring
        pool_recycle=1800,    # Supabase closes idle connections
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()