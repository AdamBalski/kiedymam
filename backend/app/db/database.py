from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
import re
import sqlite3
from typing import Iterator

from app.config import Settings

MIGRATION_FILENAME = re.compile(r"^(\d{3,})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    sql: str
    checksum: str


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level="DEFERRED")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        migrations = self._load_migrations()
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version INTEGER PRIMARY KEY,
                       filename TEXT NOT NULL,
                       checksum TEXT NOT NULL,
                       applied_at TEXT NOT NULL
                   )"""
            )
            self._apply_migrations(connection, migrations)
            connection.commit()
        os.chmod(self.path, 0o600)

    @staticmethod
    def _load_migrations() -> list[Migration]:
        directory = Path(__file__).with_name("migrations")
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
    def _apply_migrations(connection: sqlite3.Connection, migrations: list[Migration]) -> None:
        applied = connection.execute(
            "SELECT version,filename,checksum FROM schema_migrations ORDER BY version"
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
                "INSERT INTO schema_migrations(version,filename,checksum,applied_at) VALUES("
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
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()


def create_database(settings: Settings) -> Database:
    return Database(settings.database_path)
