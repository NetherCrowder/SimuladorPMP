CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    account_role TEXT NOT NULL DEFAULT 'user'
        CHECK (account_role IN ('user', 'owner', 'admin')),
    first_name TEXT NOT NULL,
    second_name TEXT,
    first_surname TEXT NOT NULL,
    second_surname TEXT,
    document_id TEXT,
    primary_phone TEXT NOT NULL
        CHECK (primary_phone ~ '^[+][1-9][0-9]{7,14}$'),
    secondary_phone TEXT
        CHECK (secondary_phone IS NULL OR secondary_phone ~ '^[+][1-9][0-9]{7,14}$')
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS account_role TEXT NOT NULL DEFAULT 'user'
    CHECK (account_role IN ('user', 'owner', 'admin'));

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
    id SERIAL PRIMARY KEY,
    prompt TEXT NOT NULL,
    options_json TEXT NOT NULL,
    option_feedback_json TEXT NOT NULL DEFAULT '[]',
    correct_option INTEGER NOT NULL CHECK (correct_option >= 0),
    question_type TEXT NOT NULL DEFAULT 'multiple_choice'
        CHECK (question_type IN (
            'case_set', 'visual_based', 'multiple_choice', 'multiple_responses',
            'drag_drop', 'hotspot', 'pull_down'
        )),
    content_json TEXT NOT NULL DEFAULT '{}',
    correct_answer_json TEXT NOT NULL DEFAULT '0',
    explanation TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'general'
        CHECK (source_type IN ('general', 'automated')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at DOUBLE PRECISION NOT NULL
);
ALTER TABLE questions ADD COLUMN IF NOT EXISTS question_type TEXT NOT NULL DEFAULT 'multiple_choice';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS content_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE questions ADD COLUMN IF NOT EXISTS correct_answer_json TEXT NOT NULL DEFAULT '0';
UPDATE questions
SET correct_answer_json = correct_option::TEXT
WHERE question_type = 'multiple_choice' AND correct_answer_json = '0' AND correct_option <> 0;

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

CREATE OR REPLACE FUNCTION check_question_tag_compatibility() RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM question_eco_domains qeco
        WHERE qeco.question_id = NEW.question_id
          AND NOT EXISTS (
              SELECT 1
              FROM valid_combinations valid
              WHERE valid.methodology_id = NEW.methodology_id
                AND valid.eco_domain_id = qeco.eco_domain_id
          )
    ) THEN
        RAISE EXCEPTION 'incompatible methodology and ECO domain';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION check_question_eco_compatibility() RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM question_methodologies qmethod
        WHERE qmethod.question_id = NEW.question_id
          AND NOT EXISTS (
              SELECT 1
              FROM valid_combinations valid
              WHERE valid.methodology_id = qmethod.methodology_id
                AND valid.eco_domain_id = NEW.eco_domain_id
          )
    ) THEN
        RAISE EXCEPTION 'incompatible methodology and ECO domain';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS enforce_question_methodology_compatibility
    ON question_methodologies;
CREATE TRIGGER enforce_question_methodology_compatibility
BEFORE INSERT OR UPDATE ON question_methodologies
FOR EACH ROW
EXECUTE FUNCTION check_question_tag_compatibility();

DROP TRIGGER IF EXISTS enforce_question_eco_compatibility
    ON question_eco_domains;
CREATE TRIGGER enforce_question_eco_compatibility
BEFORE INSERT OR UPDATE ON question_eco_domains
FOR EACH ROW
EXECUTE FUNCTION check_question_eco_compatibility();

CREATE TABLE IF NOT EXISTS user_question_states (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    pending INTEGER NOT NULL DEFAULT 0 CHECK (pending IN (0, 1)),
    seen_count INTEGER NOT NULL DEFAULT 0,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (user_id, question_id)
);

CREATE TABLE IF NOT EXISTS exams (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_count INTEGER NOT NULL CHECK (requested_count IN (30, 60, 75, 180)),
    filters_json TEXT NOT NULL,
    started_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    ended_at DOUBLE PRECISION,
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
    response_json TEXT,
    answered_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (exam_id, question_id),
    FOREIGN KEY (exam_id, question_id)
        REFERENCES exam_questions(exam_id, question_id) ON DELETE CASCADE
);
ALTER TABLE exam_answers ADD COLUMN IF NOT EXISTS response_json TEXT;

CREATE INDEX IF NOT EXISTS idx_exams_user_started
    ON exams(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_exam_questions_position
    ON exam_questions(exam_id, position);

CREATE TABLE IF NOT EXISTS user_profiles (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    age INTEGER,
    birth_date DATE,
    registration_date DOUBLE PRECISION NOT NULL,
    last_login_at DOUBLE PRECISION,
    email TEXT,
    country TEXT,
    state_or_department TEXT,
    city TEXT,
    education TEXT,
    question_history_json TEXT NOT NULL DEFAULT
        '{"new":[],"incorrect":[],"favorites":[],"correct":[],"mixed":[]}',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS super_users (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    country TEXT,
    state_or_department TEXT,
    city TEXT,
    business_id TEXT,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_super_users_single_owner
    ON super_users(role) WHERE role = 'Owner';

CREATE TABLE IF NOT EXISTS manual_question_bank (
    id SERIAL PRIMARY KEY,
    question_id INTEGER UNIQUE REFERENCES questions(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    official_domains_json TEXT NOT NULL DEFAULT '[]',
    methodological_approaches_json TEXT NOT NULL DEFAULT '[]',
    performance_domains_json TEXT NOT NULL DEFAULT '[]',
    answer TEXT NOT NULL,
    feedback TEXT NOT NULL,
    added_at DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS automated_question_bank (
    id SERIAL PRIMARY KEY,
    question_id INTEGER UNIQUE REFERENCES questions(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    official_domains_json TEXT NOT NULL DEFAULT '[]',
    methodological_approaches_json TEXT NOT NULL DEFAULT '[]',
    performance_domains_json TEXT NOT NULL DEFAULT '[]',
    answer TEXT NOT NULL,
    feedback TEXT NOT NULL,
    added_at DOUBLE PRECISION NOT NULL,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (validation_status IN ('approved', 'rejected', 'pending')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive'))
);

ALTER TABLE manual_question_bank
    ADD COLUMN IF NOT EXISTS question_id INTEGER UNIQUE
    REFERENCES questions(id) ON DELETE SET NULL;

ALTER TABLE automated_question_bank
    ADD COLUMN IF NOT EXISTS question_id INTEGER UNIQUE
    REFERENCES questions(id) ON DELETE SET NULL;
