"""SQLite-backed store for decompilation patterns.

Cada patrón se guarda con su ensamblador original y una versión normalizada
donde los registros e inmediatos se sustituyen por placeholders (REG, IMM).
La búsqueda usa FTS5 sobre el ensamblador normalizado, lo que permite encontrar
patrones estructuralmente iguales aunque usen registros distintos.

Ejemplo:
  original:   ldrsh r3, [r0, #0x10]  →  normalizado: ldrsh REG, [REG, #IMM]
  búsqueda:   ldrsh r1, [r4, #0x8]   →  normalizada: ldrsh REG, [REG, #IMM]  ✓ match
"""

import os
import re
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


# ─── Normalización ───

# Registros ARM (r0-r15, sp, lr, pc, fp, ip, sl, sb)
_ARM_REGS = re.compile(
    r'\b(r1[0-5]|r[0-9]|sp|lr|pc|fp|ip|sl|sb)\b', re.IGNORECASE
)
# Registros de punto flotante ARM/VFP (s0-s31, d0-d31, q0-q15)
_ARM_FREGS = re.compile(
    r'\b([sdq][12][0-9]|[sdq]3[01]|[sdq][0-9])\b', re.IGNORECASE
)
# Registros PPC (r0-r31, f0-f31, cr0-cr7)
_PPC_REGS = re.compile(
    r'\b(r[12][0-9]|r3[01]|r[0-9]|f[12][0-9]|f3[01]|f[0-9]|cr[0-7])\b',
    re.IGNORECASE,
)
# Registros MIPS ($t0-$t9, $s0-$s7, $a0-$a3, $v0-$v1, $zero, $ra, etc.)
_MIPS_REGS = re.compile(r'\$\w+')
# Inmediatos hexadecimales (positivos y negativos)
_HEX_IMM = re.compile(r'-?0[xX][0-9a-fA-F]+')
# Inmediatos decimales (no parte de identificadores como "sub_8001234")
_DEC_IMM = re.compile(r'(?<![a-zA-Z_\$])\b\d+\b')


def normalize_asm(asm: str) -> str:
    """Reemplaza registros e inmediatos por placeholders REG/IMM.

    Preserva los mnemonics de instrucciones (la parte semánticamente importante)
    y la estructura de los operandos (corchetes, comas, etc.).
    """
    asm = _MIPS_REGS.sub('REG', asm)   # MIPS primero (usa $, no hay ambigüedad)
    asm = _ARM_FREGS.sub('FREG', asm)  # VFP antes que ARM enteros
    asm = _ARM_REGS.sub('REG', asm)
    asm = _PPC_REGS.sub('REG', asm)
    asm = _HEX_IMM.sub('IMM', asm)
    asm = _DEC_IMM.sub('IMM', asm)
    return asm


def _fts_query(fragment: str) -> str:
    """Convierte un fragmento de búsqueda en una query FTS5 válida.

    Normaliza el fragmento y escapa caracteres especiales de FTS5,
    luego construye una búsqueda AND de todos los tokens.
    """
    normalized = normalize_asm(fragment)
    # Escapar caracteres especiales de FTS5: " * ^ ( ) { } [ ]
    tokens = re.findall(r'[A-Za-z0-9_#,\[\]]+', normalized)
    if not tokens:
        return '""'
    # Buscar cada token por separado (AND implícito en FTS5)
    return ' '.join(f'"{t}"' for t in tokens)


# ─── Inicialización y migraciones ───

def init_db() -> sqlite3.Connection:
    """Crea las tablas si no existen, aplica migraciones y devuelve la conexión."""
    conn = _connect()
    schema = _SCHEMA_PATH.read_text()
    conn.executescript(schema)

    # Migración: añadir asm_normalized si la DB existía antes de esta versión
    cols = {row[1] for row in conn.execute("PRAGMA table_info(patterns)")}
    if 'asm_normalized' not in cols:
        conn.execute(
            "ALTER TABLE patterns ADD COLUMN asm_normalized TEXT NOT NULL DEFAULT ''"
        )
        # Rellenar filas existentes con su versión normalizada
        rows = conn.execute("SELECT id, asm_pattern FROM patterns").fetchall()
        for row in rows:
            conn.execute(
                "UPDATE patterns SET asm_normalized = ? WHERE id = ?",
                (normalize_asm(row[1]), row[0]),
            )
        conn.commit()

    return conn


# ─── CRUD ───

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
    """Inserta un patrón y devuelve su id."""
    conn = init_db()
    try:
        cur = conn.execute(
            """INSERT INTO patterns
               (platform, compiler, asm_pattern, asm_normalized,
                c_code, match_score, scratch_url, notes, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                platform, compiler,
                asm_pattern, normalize_asm(asm_pattern),
                c_code, match_score, scratch_url, notes, tags,
            ),
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
    """Busca patrones combinando FTS5 (fragmento ASM) y filtros exactos.

    Si se pasa asm_fragment, se normaliza y se busca con FTS5 MATCH,
    lo que encuentra patrones con la misma estructura aunque usen
    registros o inmediatos distintos.
    """
    conn = init_db()
    try:
        if asm_fragment is not None:
            # Búsqueda vía FTS5: join con el índice de texto completo
            fts_q = _fts_query(asm_fragment)
            base_query = """
                SELECT p.* FROM patterns p
                WHERE p.id IN (
                    SELECT rowid FROM patterns_fts WHERE patterns_fts MATCH ?
                )
            """
            params: list = [fts_q]
        else:
            base_query = "SELECT * FROM patterns WHERE 1=1"
            params = []

        # Filtros adicionales
        if platform is not None:
            base_query += " AND platform = ?"
            params.append(platform)
        if compiler is not None:
            base_query += " AND compiler = ?"
            params.append(compiler)
        if tags is not None:
            base_query += " AND tags LIKE ?"
            params.append(f"%{tags}%")

        base_query += " ORDER BY match_score DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(base_query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pattern(pattern_id: int) -> dict | None:
    """Devuelve un patrón por id, o None si no existe."""
    conn = init_db()
    try:
        row = conn.execute(
            "SELECT * FROM patterns WHERE id = ?", (pattern_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_match(
    pattern_id: int,
    asm_input: str,
    c_output: str,
    match_score: float,
) -> int:
    """Registra una aplicación exitosa de un patrón y devuelve su id."""
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
