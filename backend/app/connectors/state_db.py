"""
PostgreSQL State Database Connector
"""

import logging
from typing import Any, Dict, List
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from contextlib import contextmanager

from app.config import get_settings
from app.queries import CommonQueries, DatabaseQueries, QueryValidator
from app.connectors.table_creation import metadata

logger = logging.getLogger(__name__)


class StateDBConnector:
    def __init__(self):
        self.settings = get_settings()
        self.engine = None
        self.SessionLocal = None
        self._connect()

    def _connect(self):
        try:
            url = self.settings.postgres_url + "?client_encoding=utf8"
            self.engine = create_engine(
                url,
                poolclass=QueuePool,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                echo=False,
            )
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
            with self.engine.connect() as conn:
                conn.execute(text(CommonQueries.TEST_CONNECTION))
            logger.info(f"Connected to PostgreSQL at {self.settings.POSTGRES_HOST}:{self.settings.POSTGRES_PORT}")
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise

    @contextmanager
    def get_session(self) -> Session:
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
        try:
            with self.get_session() as session:
                result = session.execute(text(query), params or {})
                return [dict(row._mapping) for row in result.fetchall()]
        except Exception as e:
            logger.error(f"Query error: {e}")
            raise

    def execute_insert(self, query: str, params: Dict[str, Any] = None) -> Any:
        try:
            with self.get_session() as session:
                session.execute(text(query), params or {})
        except Exception as e:
            logger.error(f"Insert error: {e}")
            raise

    def execute_update(self, query: str, params: Dict[str, Any] = None) -> int:
        try:
            with self.get_session() as session:
                result = session.execute(text(query), params or {})
                return result.rowcount
        except Exception as e:
            logger.error(f"Update error: {e}")
            raise

    def test_connection(self) -> bool:
        try:
            self.execute_query(CommonQueries.TEST_CONNECTION)
            return True
        except Exception:
            return False

    def close(self):
        if self.engine:
            try:
                self.engine.dispose()
            except Exception as e:
                logger.error(f"Error closing: {e}")


class StateDBManager:
    def __init__(self):
        self.settings = get_settings()

    def _get_engine(self, database: str = "postgres"):
        url = (
            f"postgresql://{self.settings.POSTGRES_USER}"
            f"@{self.settings.POSTGRES_HOST}:{self.settings.POSTGRES_PORT}/{database}"
            f"?client_encoding=utf8"
        )
        return create_engine(url, isolation_level="AUTOCOMMIT")

    def initialize_database(self):
        try:
            db_name = self.settings.POSTGRES_DB
            QueryValidator.validate_identifier(db_name, "database name")
            engine = self._get_engine()
            with engine.connect() as conn:
                exists = conn.execute(
                    text(DatabaseQueries.CHECK_DATABASE_EXISTS), {"db_name": db_name}
                ).fetchone()
                if not exists:
                    logger.info(f"Creating database: {db_name}")
                    conn.execute(text(DatabaseQueries.get_create_database_query(db_name)))
            engine.dispose()
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise

    def create_tables_if_not_exists(self):
        try:
            engine = self._get_engine(self.settings.POSTGRES_DB)
            metadata.create_all(engine, checkfirst=True)
            engine.dispose()
            logger.info("Database tables ready")
        except Exception as e:
            logger.error(f"Error creating tables: {e}")
            raise
