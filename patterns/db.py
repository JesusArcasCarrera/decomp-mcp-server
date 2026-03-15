"""Simple SQLite-backed store for decompilation patterns."""

import os
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_DEFAULT_DB = Path(__file__).parent / "patterns.db"


def _db_path() -> str:
    return os.environ.get("DECOMP_PATTERNS_DB", str(_DEFAULT_DB))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> sqlite3.Connection:
    """Create tables (if needed) and return a connection."""
    conn = _connect()
    schema = _SCHEMA_PATH.read_text()
    conn.executescript(schema)
    return conn


def add_pattern(
    platform: str,
    compiler: str,
    asm_pattern: str,
    c_code: str,
    match_score: float,
    scratch_url: str | None = None,
    notes: str | None = None,
    tags: str | None = None,
) -> int:
    """Insert a pattern and return its id."""
    conn = init_db()
    try:
        cur = conn.execute(
            """INSERT INTO patterns
               (platform, compiler, asm_pattern, c_code, match_score, scratch_url, notes, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (platform, compiler, asm_pattern, c_code, match_score, scratch_url, notes, tags),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def search_patterns(
    asm_fragment: str | None = None,
    platform: str | None = None,
    compiler: str | None = None,
    tags: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search patterns using LIKE on asm_pattern and optional filters."""
    conn = init_db()
    try:
        clauses = []
        params: list = []

        if asm_fragment is not None:
            clauses.append("asm_pattern LIKE ?")
            params.append(f"%{asm_fragment}%")
        if platform is not None:
            clauses.append("platform = ?")
            params.append(platform)
        if compiler is not None:
            clauses.append("compiler = ?")
            params.append(compiler)
        if tags is not None:
            clauses.append("tags LIKE ?")
            params.append(f"%{tags}%")

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        rows = conn.execute(
            f"SELECT * FROM patterns {where} ORDER BY match_score DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pattern(pattern_id: int) -> dict | None:
    """Return a single pattern by id, or None if not found."""
    conn = init_db()
    try:
        row = conn.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_match(
    pattern_id: int,
    asm_input: str,
    c_output: str,
    match_score: float,
) -> int:
    """Record a pattern match and return its id."""
    conn = init_db()
    try:
        cur = conn.execute(
            """INSERT INTO pattern_matches
               (pattern_id, asm_input, c_output, match_score)
               VALUES (?, ?, ?, ?)""",
            (pattern_id, asm_input, c_output, match_score),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
