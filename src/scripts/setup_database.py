"""
Database setup script.

Creates the database and tables for AI SDLC Automation.
"""

import os
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Database connection parameters
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "ai_sdlc")


def create_database():
    """Create the database if it doesn't exist."""
    try:
        # Connect to PostgreSQL server (default postgres database)
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database="postgres",  # Connect to default database first
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()

        # Check if database exists
        cursor.execute(
            sql.SQL("SELECT 1 FROM pg_database WHERE datname = {}").format(sql.Literal(DB_NAME))
        )

        if cursor.fetchone():
            print(f"Database '{DB_NAME}' already exists.")
        else:
            # Create database
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB_NAME)))
            print(f"Database '{DB_NAME}' created successfully.")

        cursor.close()
        conn.close()
        return True

    except psycopg2.Error as e:
        print(f"Error creating database: {e}")
        return False


def create_tables():
    """Create the database tables using SQLAlchemy."""
    try:
        from src.core.database.base import Base, engine

        # Create all tables
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully.")
        return True

    except Exception as e:
        print(f"Error creating tables: {e}")
        return False


def main():
    """Set up the AI SDLC Automation database."""
    print("Setting up AI SDLC Automation database...")
    print(f"Host: {DB_HOST}:{DB_PORT}")
    print(f"User: {DB_USER}")
    print(f"Database: {DB_NAME}")
    print()

    # Create database
    if not create_database():
        sys.exit(1)

    # Create tables
    if not create_tables():
        sys.exit(1)

    print()
    print("Database setup completed successfully!")


if __name__ == "__main__":
    main()
