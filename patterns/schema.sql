CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    compiler TEXT NOT NULL,
    asm_pattern TEXT NOT NULL,
    c_code TEXT NOT NULL,
    match_score REAL NOT NULL,
    scratch_url TEXT,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    tags TEXT
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
