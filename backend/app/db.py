from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB_PATH = Path(os.environ.get("PAPER_RADAR_DB", "data/paper_radar.sqlite"))


def get_db_path() -> Path:
    return Path(os.environ.get("PAPER_RADAR_DB", str(DEFAULT_DB_PATH)))


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS zotero_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            abstract TEXT NOT NULL DEFAULT '',
            authors TEXT NOT NULL DEFAULT '',
            year INTEGER,
            tags TEXT NOT NULL DEFAULT '[]',
            source_key TEXT NOT NULL UNIQUE,
            embedding TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS conference_papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL,
            abstract TEXT NOT NULL DEFAULT '',
            authors TEXT NOT NULL DEFAULT '',
            conference TEXT NOT NULL,
            year INTEGER NOT NULL,
            decision TEXT NOT NULL DEFAULT '',
            eventtype TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL DEFAULT '',
            keywords TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            pdf_url TEXT NOT NULL DEFAULT '',
            embedding TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(external_id, conference, year)
        );

        CREATE TABLE IF NOT EXISTS interest_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '[]',
            centroid_embedding TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS match_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conference TEXT NOT NULL,
            year INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            settings TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS match_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            paper_id INTEGER NOT NULL,
            profile_id INTEGER NOT NULL,
            score REAL NOT NULL,
            embedding_score REAL NOT NULL,
            bm25_score REAL NOT NULL,
            tag_score REAL NOT NULL,
            feedback_score REAL NOT NULL,
            matched_zotero_items TEXT NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(run_id) REFERENCES match_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(paper_id) REFERENCES conference_papers(id) ON DELETE CASCADE,
            FOREIGN KEY(profile_id) REFERENCES interest_profiles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id INTEGER NOT NULL,
            profile_id INTEGER,
            action TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(paper_id) REFERENCES conference_papers(id) ON DELETE CASCADE,
            FOREIGN KEY(profile_id) REFERENCES interest_profiles(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_conference_papers_run ON conference_papers(conference, year);
        CREATE INDEX IF NOT EXISTS idx_match_results_lookup ON match_results(profile_id, score DESC);
        CREATE INDEX IF NOT EXISTS idx_match_results_run_score ON match_results(run_id, score DESC);
        CREATE INDEX IF NOT EXISTS idx_match_results_paper ON match_results(paper_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_lookup ON feedback(paper_id, profile_id, created_at);
        """
    )
    conn.commit()


def upsert_many(conn: sqlite3.Connection, sql: str, rows: Iterable[tuple[Any, ...]]) -> int:
    count = 0
    with conn:
        for row in rows:
            conn.execute(sql, row)
            count += 1
    return count


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}
