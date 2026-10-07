PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_domains (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS methodologies (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS eco_domains (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS valid_combinations (
    methodology_id TEXT NOT NULL REFERENCES methodologies(id),
    eco_domain_id TEXT NOT NULL REFERENCES eco_domains(id),
    PRIMARY KEY (methodology_id, eco_domain_id)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT NOT NULL,
    options_json TEXT NOT NULL,
    option_feedback_json TEXT NOT NULL DEFAULT '[]',
    correct_option INTEGER NOT NULL CHECK (correct_option >= 0),
    explanation TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'general'
        CHECK (source_type IN ('general', 'automated')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS question_performance_domains (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    domain_id TEXT NOT NULL REFERENCES performance_domains(id),
    PRIMARY KEY (question_id, domain_id)
);

CREATE TABLE IF NOT EXISTS question_methodologies (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    methodology_id TEXT NOT NULL REFERENCES methodologies(id),
    PRIMARY KEY (question_id, methodology_id)
);

CREATE TABLE IF NOT EXISTS question_eco_domains (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    eco_domain_id TEXT NOT NULL REFERENCES eco_domains(id),
    PRIMARY KEY (question_id, eco_domain_id)
);

CREATE TRIGGER IF NOT EXISTS enforce_question_methodology_compatibility
BEFORE INSERT ON question_methodologies
WHEN EXISTS (
    SELECT 1
    FROM question_eco_domains qeco
    WHERE qeco.question_id = NEW.question_id
      AND NOT EXISTS (
          SELECT 1
          FROM valid_combinations valid
          WHERE valid.methodology_id = NEW.methodology_id
            AND valid.eco_domain_id = qeco.eco_domain_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'incompatible methodology and ECO domain');
END;

CREATE TRIGGER IF NOT EXISTS enforce_question_eco_compatibility
BEFORE INSERT ON question_eco_domains
WHEN EXISTS (
    SELECT 1
    FROM question_methodologies qmethod
    WHERE qmethod.question_id = NEW.question_id
      AND NOT EXISTS (
          SELECT 1
          FROM valid_combinations valid
          WHERE valid.methodology_id = qmethod.methodology_id
            AND valid.eco_domain_id = NEW.eco_domain_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'incompatible methodology and ECO domain');
END;

CREATE TRIGGER IF NOT EXISTS enforce_question_methodology_compatibility_update
BEFORE UPDATE OF question_id, methodology_id ON question_methodologies
WHEN EXISTS (
    SELECT 1
    FROM question_eco_domains qeco
    WHERE qeco.question_id = NEW.question_id
      AND NOT EXISTS (
          SELECT 1
          FROM valid_combinations valid
          WHERE valid.methodology_id = NEW.methodology_id
            AND valid.eco_domain_id = qeco.eco_domain_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'incompatible methodology and ECO domain');
END;

CREATE TRIGGER IF NOT EXISTS enforce_question_eco_compatibility_update
BEFORE UPDATE OF question_id, eco_domain_id ON question_eco_domains
WHEN EXISTS (
    SELECT 1
    FROM question_methodologies qmethod
    WHERE qmethod.question_id = NEW.question_id
      AND NOT EXISTS (
          SELECT 1
          FROM valid_combinations valid
          WHERE valid.methodology_id = qmethod.methodology_id
            AND valid.eco_domain_id = NEW.eco_domain_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'incompatible methodology and ECO domain');
END;

CREATE TABLE IF NOT EXISTS user_question_states (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    pending INTEGER NOT NULL DEFAULT 0 CHECK (pending IN (0, 1)),
    seen_count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, question_id)
);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_count INTEGER NOT NULL CHECK (requested_count IN (30, 60, 75, 180)),
    filters_json TEXT NOT NULL,
    started_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    ended_at REAL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'expired'))
);

CREATE TABLE IF NOT EXISTS exam_questions (
    exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    position INTEGER NOT NULL,
    marked_saved INTEGER NOT NULL DEFAULT 0 CHECK (marked_saved IN (0, 1)),
    PRIMARY KEY (exam_id, question_id),
    UNIQUE (exam_id, position)
);

CREATE TABLE IF NOT EXISTS exam_answers (
    exam_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    selected_option INTEGER NOT NULL CHECK (selected_option >= 0),
    answered_at REAL NOT NULL,
    PRIMARY KEY (exam_id, question_id),
    FOREIGN KEY (exam_id, question_id)
        REFERENCES exam_questions(exam_id, question_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_exams_user_started
    ON exams(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_exam_questions_position
    ON exam_questions(exam_id, position);
