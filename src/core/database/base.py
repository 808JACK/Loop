"""Database base configuration and session management."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.settings import settings


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models using SQLAlchemy 2.0 pattern."""

    pass


# Global variables for lazy initialization
engine = None
SessionLocal = None
_db_initialized = False


def initialize_database():
    """Initialize database connection and create tables."""
    global engine, SessionLocal, _db_initialized
    
    if _db_initialized:
        return True
    
    try:
        # Create SQLAlchemy engine
        engine = create_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            echo=False,
        )

        # Create SessionLocal class for dependency injection
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        # Import all models to ensure they are attached to Base.metadata 
        # before we attempt to create tables
        import src.models  # noqa: F401

        # Auto-create tables
        Base.metadata.create_all(bind=engine)
        
        _db_initialized = True
        return True
    except Exception as e:
        print(f"Warning: Database initialization failed: {e}")
        print("Auth endpoints will work, but workflow features require database connection.")
        return False


# Try to initialize database on import, but don't fail if it doesn't work
initialize_database()


def get_db() -> Generator[Session, None, None]:
    """
    Dependency for getting database sessions.

    Yields:
        Session: SQLAlchemy session
    """
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Check your DATABASE_URL configuration.")
    
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
