CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    compiler TEXT NOT NULL,
    asm_pattern TEXT NOT NULL,       -- ensamblador original tal cual
    asm_normalized TEXT NOT NULL DEFAULT '',  -- versión normalizada (REG/IMM)
    c_code TEXT NOT NULL,
    match_score REAL NOT NULL,
    scratch_url TEXT,
    notes TEXT,
    tags TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pattern_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER NOT NULL,
    asm_input TEXT NOT NULL,
    c_output TEXT NOT NULL,
    match_score REAL NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pattern_id) REFERENCES patterns(id)
);

CREATE INDEX IF NOT EXISTS idx_patterns_platform ON patterns(platform);
CREATE INDEX IF NOT EXISTS idx_patterns_compiler ON patterns(compiler);
CREATE INDEX IF NOT EXISTS idx_patterns_tags ON patterns(tags);

-- Tabla FTS5: búsqueda por tokens sobre el ensamblador normalizado.
-- content=patterns hace que no duplique datos; content_rowid enlaza con patterns.id.
CREATE VIRTUAL TABLE IF NOT EXISTS patterns_fts USING fts5(
    asm_normalized,
    content=patterns,
    content_rowid=id
);

-- Triggers para mantener el índice FTS sincronizado con la tabla principal.
CREATE TRIGGER IF NOT EXISTS patterns_ai AFTER INSERT ON patterns BEGIN
    INSERT INTO patterns_fts(rowid, asm_normalized) VALUES (new.id, new.asm_normalized);
END;

CREATE TRIGGER IF NOT EXISTS patterns_ad AFTER DELETE ON patterns BEGIN
    INSERT INTO patterns_fts(patterns_fts, rowid, asm_normalized)
    VALUES ('delete', old.id, old.asm_normalized);
END;

CREATE TRIGGER IF NOT EXISTS patterns_au AFTER UPDATE ON patterns BEGIN
    INSERT INTO patterns_fts(patterns_fts, rowid, asm_normalized)
    VALUES ('delete', old.id, old.asm_normalized);
    INSERT INTO patterns_fts(rowid, asm_normalized) VALUES (new.id, new.asm_normalized);
END;
