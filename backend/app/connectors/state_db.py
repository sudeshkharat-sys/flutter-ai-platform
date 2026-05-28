"""
SQLite State Database Connector
"""

import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import create_engine, inspect, text, event
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

from app.config import get_settings
from app.queries import CommonQueries
from app.connectors.table_creation import metadata

logger = logging.getLogger(__name__)


def _enable_wal(dbapi_conn, connection_record):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


class StateDBConnector:
    def __init__(self):
        self.settings = get_settings()
        self.engine = None
        self.SessionLocal = None
        self._connect()

    def _connect(self):
        try:
            self.engine = create_engine(
                self.settings.database_url,
                connect_args={"check_same_thread": False},
                echo=False,
            )
            event.listen(self.engine, "connect", _enable_wal)
            self.SessionLocal = sessionmaker(
                autocommit=False, autoflush=False, bind=self.engine
            )
            with self.engine.connect() as conn:
                conn.execute(text(CommonQueries.TEST_CONNECTION))
            logger.info("Connected to SQLite database")
        except Exception as e:
            logger.error(f"Failed to connect to SQLite: {e}")
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
                rows = result.fetchall()
                return [dict(row._mapping) for row in rows]
        except Exception as e:
            logger.error(f"Error executing query: {e}")
            raise

    def execute_insert(self, query: str, params: Dict[str, Any] = None) -> Any:
        try:
            with self.get_session() as session:
                session.execute(text(query), params or {})
                return None
        except Exception as e:
            logger.error(f"Error executing insert: {e}")
            raise

    def execute_update(self, query: str, params: Dict[str, Any] = None) -> int:
        try:
            with self.get_session() as session:
                result = session.execute(text(query), params or {})
                return result.rowcount
        except Exception as e:
            logger.error(f"Error executing update/delete: {e}")
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
                logger.error(f"Error closing connection: {e}")


class StateDBManager:
    def __init__(self):
        self.settings = get_settings()

    def initialize_database(self):
        pass  # SQLite creates the file automatically

    def create_tables_if_not_exists(self):
        try:
            engine = create_engine(
                self.settings.database_url,
                connect_args={"check_same_thread": False},
            )
            event.listen(engine, "connect", _enable_wal)
            metadata.create_all(engine, checkfirst=True)
            engine.dispose()
            logger.info("Database tables ready")
        except Exception as e:
            logger.error(f"Error creating tables: {e}")
            raise
