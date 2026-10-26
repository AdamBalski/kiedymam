from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
import re
import sqlite3
import time
import logging
from typing import Iterator

from app.config import Settings

MIGRATION_FILENAME = re.compile(r"^(\d{3,})_[a-z0-9_]+\.sql$")
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    sql: str
    checksum: str


class Database:
    def __init__(self, path: str, migrations_dir: str | Path | None = None,
                 migration_table: str = "schema_migrations"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", migration_table):
            raise ValueError("Invalid migrations table name")
        self.path = path
        self.migrations_dir = Path(migrations_dir) if migrations_dir else Path(__file__).with_name("migrations")
        self.migration_table = migration_table
        self.metrics = None
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def _record_operation(self, operation: str, started: float, outcome: str) -> None:
        if self.metrics is None:
            return
        attributes = {"operation.name": operation, "outcome": outcome}
        self.metrics.observe("sqlite.operation.duration", time.monotonic() - started, attributes)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level="DEFERRED")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        migrations = self._load_migrations(self.migrations_dir)
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                f"""CREATE TABLE IF NOT EXISTS {self.migration_table} (
                       version INTEGER PRIMARY KEY,
                       filename TEXT NOT NULL,
                       checksum TEXT NOT NULL,
                       applied_at TEXT NOT NULL
                   )"""
            )
            self._apply_migrations(connection, migrations, self.migration_table)
            connection.commit()
        os.chmod(self.path, 0o600)
        LOG.info("Database migrations ready database=%s migration_table=%s count=%d latest_version=%d",
                 self.path, self.migration_table, len(migrations), migrations[-1].version)

    @staticmethod
    def _load_migrations(directory: str | Path | None = None) -> list[Migration]:
        directory = Path(directory) if directory else Path(__file__).with_name("migrations")
        files = sorted(directory.glob("*.sql"))
        if not files:
            raise RuntimeError("No database migration files were found")

        migrations: list[Migration] = []
        for path in files:
            match = MIGRATION_FILENAME.fullmatch(path.name)
            if not match:
                raise RuntimeError(f"Invalid database migration filename: {path.name}")
            sql = path.read_text(encoding="utf-8")
            migrations.append(
                Migration(
                    version=int(match.group(1)),
                    filename=path.name,
                    sql=sql,
                    checksum=sha256(sql.encode("utf-8")).hexdigest(),
                )
            )

        expected = list(range(1, len(migrations) + 1))
        actual = [migration.version for migration in migrations]
        if actual != expected:
            raise RuntimeError(f"Database migrations must be numbered sequentially from 001; found {actual}")
        return migrations

    @staticmethod
    def _apply_migrations(connection: sqlite3.Connection, migrations: list[Migration],
                           migration_table: str = "schema_migrations") -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", migration_table):
            raise ValueError("Invalid migrations table name")
        applied = connection.execute(
            f"SELECT version,filename,checksum FROM {migration_table} ORDER BY version"
        ).fetchall()
        if len(applied) > len(migrations):
            raise RuntimeError("Database contains a migration version unknown to this application")

        for index, row in enumerate(applied):
            migration = migrations[index]
            if row["version"] != migration.version:
                raise RuntimeError("Database migrations were applied out of order")
            if row["filename"] != migration.filename:
                raise RuntimeError(
                    f"Applied migration {migration.version:03d} was renamed; restore its original filename"
                )
            if row["checksum"] != migration.checksum:
                raise RuntimeError(
                    f"Applied migration {migration.version:03d} has changed; add a new migration instead"
                )

        for migration in migrations[len(applied):]:
            applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            script = (
                "BEGIN IMMEDIATE;\n"
                f"{migration.sql}\n"
                f"INSERT INTO {migration_table}(version,filename,checksum,applied_at) VALUES("
                f"{migration.version},'{migration.filename}','{migration.checksum}','{applied_at}');\n"
                "COMMIT;"
            )
            try:
                connection.executescript(script)
            except Exception as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise RuntimeError(f"Database migration {migration.filename} failed") from exc

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        started = time.monotonic()
        outcome = "success"
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException as exc:
            outcome = "busy" if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower() else "failure"
            connection.rollback()
            raise
        finally:
            connection.close()
            self._record_operation("sqlite.transaction", started, outcome)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        started = time.monotonic()
        outcome = "success"
        connection = self.connect()
        try:
            yield connection
        except BaseException as exc:
            outcome = "busy" if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower() else "failure"
            raise
        finally:
            connection.close()
            self._record_operation("sqlite.read", started, outcome)


def create_database(settings: Settings) -> Database:
    return Database(settings.database_path)
