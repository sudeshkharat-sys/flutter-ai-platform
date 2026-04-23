"""
PostgreSQL State Database Connector
Manages connection and state management for Flutter AI Studio
"""

import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from contextlib import contextmanager

from app.config import get_settings
from app.queries import CommonQueries, DatabaseQueries, QueryValidator
from app.connectors.table_creation import metadata

logger = logging.getLogger(__name__)

class StateDBConnector:
    """
    Manages PostgreSQL database connections
    """

    def __init__(self):
        """Initialize PostgreSQL connection"""
        self.settings = get_settings()
        self.engine = None
        self.SessionLocal = None
        self._connect()

    def _connect(self):
        """Establish connection to PostgreSQL database"""
        try:
            # Ensure UTF-8 client encoding
            url = self.settings.postgres_url
            if "?" in url:
                url += "&client_encoding=utf8"
            else:
                url += "?client_encoding=utf8"

            self.engine = create_engine(
                url,
                poolclass=QueuePool,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                echo=False,
            )

            self.SessionLocal = sessionmaker(
                autocommit=False, autoflush=False, bind=self.engine
            )

            # Test connection
            with self.engine.connect() as conn:
                conn.execute(text(CommonQueries.TEST_CONNECTION))

            logger.info(f"Successfully connected to PostgreSQL at {self.settings.POSTGRES_HOST}")
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise

    @contextmanager
    def get_session(self) -> Session:
        """Context manager for database sessions"""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Session error: {e}")
            raise
        finally:
            session.close()

    def execute_query(self, query: str, params: Dict[str, Any] = None) -> List[Any]:
        """Execute a SQL SELECT query and return results"""
        try:
            with self.get_session() as session:
                if params:
                    result = session.execute(text(query), params)
                else:
                    result = session.execute(text(query))

                rows = result.fetchall()
                logger.debug(f"Query returned {len(rows)} rows")
                # Convert rows to dicts for easier consumption
                return [dict(row._mapping) for row in rows]

        except Exception as e:
            logger.error(f"Error executing query: {e}")
            raise

    def execute_insert(self, query: str, params: Dict[str, Any] = None) -> Any:
        """Execute an INSERT query and return the inserted ID"""
        try:
            with self.get_session() as session:
                result = session.execute(text(query), params)
                
                # Check for returning ID
                try:
                    row = result.fetchone()
                    return row[0] if row else None
                except Exception:
                    return None

        except Exception as e:
            logger.error(f"Error executing insert: {e}")
            raise

    def execute_update(self, query: str, params: Dict[str, Any] = None) -> int:
        """Execute an UPDATE or DELETE query and return number of affected rows"""
        try:
            with self.get_session() as session:
                result = session.execute(text(query), params)
                return result.rowcount

        except Exception as e:
            logger.error(f"Error executing update/delete: {e}")
            raise

    def test_connection(self) -> bool:
        """Test if the connection to PostgreSQL is working"""
        try:
            result = self.execute_query(CommonQueries.TEST_CONNECTION)
            return len(result) > 0
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    def close(self):
        """Close the PostgreSQL connection"""
        if self.engine:
            try:
                self.engine.dispose()
                logger.info("PostgreSQL connection closed")
            except Exception as e:
                logger.error(f"Error closing connection: {e}")

class StateDBManager:
    """
    Manages database initialization and table creation for PostgreSQL
    """

    def __init__(self):
        self.settings = get_settings()
        self.engine = None

    def _get_engine(self, database: str = None):
        if database:
            url = f"postgresql://{self.settings.POSTGRES_USER}:{self.settings.POSTGRES_PASSWORD}@{self.settings.POSTGRES_HOST}:{self.settings.POSTGRES_PORT}/{database}?client_encoding=utf8"
        else:
            url = f"postgresql://{self.settings.POSTGRES_USER}:{self.settings.POSTGRES_PASSWORD}@{self.settings.POSTGRES_HOST}:{self.settings.POSTGRES_PORT}/postgres?client_encoding=utf8"

        return create_engine(url, isolation_level="AUTOCOMMIT")

    def initialize_database(self):
        """Create database if it doesn't exist."""
        try:
            db_name = self.settings.POSTGRES_DB
            QueryValidator.validate_identifier(db_name, "database name")

            engine = self._get_engine()
            with engine.connect() as conn:
                result = conn.execute(
                    text(DatabaseQueries.CHECK_DATABASE_EXISTS), {"db_name": db_name}
                )
                exists = result.fetchone()

                if not exists:
                    logger.info(f"Creating database: {db_name}")
                    create_query = DatabaseQueries.get_create_database_query(db_name)
                    conn.execute(text(create_query))
                    logger.info(f"Database '{db_name}' created successfully")
                else:
                    logger.info(f"Database '{db_name}' already exists")

            engine.dispose()

        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise

    def create_tables_if_not_exists(self):
        """Create all tables defined in table_creation.py if they don't exist"""
        try:
            self.engine = self._get_engine(self.settings.POSTGRES_DB)
            inspector = inspect(self.engine)
            existing_tables = inspector.get_table_names()

            logger.info("Creating tables if not exists...")
            metadata.create_all(self.engine, checkfirst=True)

            inspector = inspect(self.engine)
            new_tables = inspector.get_table_names()
            created_tables = set(new_tables) - set(existing_tables)

            if created_tables:
                logger.info(f"Created new tables: {created_tables}")
            else:
                logger.info("All tables already exist")

            self.engine.dispose()

        except Exception as e:
            logger.error(f"Error creating tables: {e}")
            raise
