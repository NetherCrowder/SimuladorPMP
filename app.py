import json
import math
import os
import re
import secrets
import sqlite3
import time
from functools import wraps

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash


PERFORMANCE_DOMAINS = {
    "governance": "Gobernanza (Governance)",
    "scope": "Alcance (Scope)",
    "schedule": "Cronograma (Schedule)",
    "finance": "Finanzas (Finance)",
    "stakeholders": "Interesados (Stakeholders)",
    "resources": "Recursos (Resources)",
    "risk": "Riesgo (Risk)",
}
METHODOLOGIES = {
    "predictive": "Predictiva",
    "agile": "Ágil",
    "hybrid": "Híbrida",
}
ECO_DOMAINS = {
    "processes": "Procesos",
    "people": "Personas",
    "business_environment": "Entorno Empresarial",
}
VALID_COMBINATIONS = tuple(
    (methodology, eco_domain)
    for methodology in METHODOLOGIES
    for eco_domain in ECO_DOMAINS
)
DEMO_QUESTIONS = (
    (
        "¿Cuánto es 4 × 9?",
        ["36", "32", "45", "49"],
        0,
        "4 multiplicado por 9 es 36.",
        [
            "4 grupos de 9 suman 36.",
            "32 corresponde a 4 grupos de 8, no de 9.",
            "45 corresponde a 5 grupos de 9.",
            "49 no es el producto de 4 por 9.",
        ],
        "governance",
    ),
    (
        "¿Qué planeta es conocido como el planeta rojo?",
        ["Venus", "Marte", "Júpiter", "Mercurio"],
        1,
        "Marte suele llamarse el planeta rojo por el aspecto de su superficie.",
        [
            "Venus no es el planeta conocido como el planeta rojo.",
            "Marte recibe ese nombre por el aspecto rojizo de su superficie.",
            "Júpiter es un gigante gaseoso y no es el planeta rojo.",
            "Mercurio es el planeta más cercano al Sol, no el planeta rojo.",
        ],
        "scope",
    ),
    (
        "A nivel del mar, ¿a qué temperatura hierve el agua pura?",
        ["50 °C", "90 °C", "100 °C", "120 °C"],
        2,
        "El agua pura hierve aproximadamente a 100 °C a presión atmosférica estándar.",
        [
            "50 °C está por debajo del punto de ebullición a presión estándar.",
            "90 °C también está por debajo del punto de ebullición.",
            "A presión atmosférica estándar, el agua pura hierve a unos 100 °C.",
            "120 °C está por encima del punto de ebullición a presión estándar.",
        ],
        "schedule",
    ),
    (
        "¿Cuántos minutos hay en una hora?",
        ["30", "45", "60", "90"],
        2,
        "Una hora se compone de 60 minutos.",
        [
            "30 minutos equivalen a media hora.",
            "45 minutos son tres cuartos de hora.",
            "Una hora completa contiene 60 minutos.",
            "90 minutos equivalen a una hora y media.",
        ],
        "finance",
    ),
    (
        "¿Cuál es la fórmula química del agua?",
        ["CO₂", "H₂O", "O₂", "NaCl"],
        1,
        "Cada molécula de agua contiene dos átomos de hidrógeno y uno de oxígeno.",
        [
            "CO₂ es la fórmula del dióxido de carbono.",
            "H₂O representa dos átomos de hidrógeno y uno de oxígeno.",
            "O₂ representa una molécula de oxígeno.",
            "NaCl es cloruro de sodio, no agua.",
        ],
        "stakeholders",
    ),
    (
        "¿Cuántos lados tiene un triángulo?",
        ["2", "3", "4", "5"],
        1,
        "Un triángulo es un polígono de tres lados.",
        [
            "Un polígono de dos lados no es un triángulo.",
            "Un triángulo tiene exactamente tres lados.",
            "Un polígono de cuatro lados es un cuadrilátero.",
            "Un polígono de cinco lados es un pentágono.",
        ],
        "resources",
    ),
    (
        "¿Cuál es el planeta más grande del sistema solar?",
        ["Tierra", "Saturno", "Júpiter", "Neptuno"],
        2,
        "Júpiter es el planeta de mayor tamaño del sistema solar.",
        [
            "La Tierra es mucho menor que Júpiter.",
            "Saturno es grande, pero es menor que Júpiter.",
            "Júpiter es el planeta de mayor tamaño del sistema solar.",
            "Neptuno es menor que Júpiter.",
        ],
        "risk",
    ),
    (
        "¿Qué día de la semana sigue al martes?",
        ["Lunes", "Miércoles", "Jueves", "Viernes"],
        1,
        "El miércoles sigue al martes en la secuencia de los días de la semana.",
        [
            "El lunes precede al martes.",
            "El miércoles sigue inmediatamente al martes.",
            "El jueves viene después del miércoles.",
            "El viernes no sigue inmediatamente al martes.",
        ],
        "governance",
    ),
    (
        "¿Cuál de estos números es primo?",
        ["9", "15", "17", "21"],
        2,
        "17 solo es divisible exactamente entre 1 y 17.",
        [
            "9 es divisible entre 3.",
            "15 es divisible entre 3 y 5.",
            "17 solo tiene como divisores positivos a 1 y 17.",
            "21 es divisible entre 3 y 7.",
        ],
        "scope",
    ),
)
EXAM_LENGTHS = (30, 60, 75, 180)
SECONDS_PER_QUESTION = 76
MOBILE_USER_AGENT = re.compile(
    r"android|iphone|ipod|ipad|windows phone|blackberry|opera mini|mobile",
    re.IGNORECASE,
)


def persistent_secret(instance_path):
    environment_secret = os.environ.get("SIMULADOR_SECRET_KEY")
    if environment_secret:
        return environment_secret
    secret_path = os.path.join(instance_path, ".secret_key")
    try:
        with open(secret_path, encoding="ascii") as secret_file:
            return secret_file.read().strip()
    except FileNotFoundError:
        secret = secrets.token_hex(32)
        with open(secret_path, "x", encoding="ascii") as secret_file:
            secret_file.write(secret)
        return secret


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=persistent_secret(app.instance_path),
        DATABASE=os.path.join(app.instance_path, "simulator.sqlite"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    csrf = CSRFProtect()

    @app.before_request
    def restrict_mobile_browsers():
        if MOBILE_USER_AGENT.search(request.headers.get("User-Agent", "")):
            return render_template("mobile_blocked.html"), 403
        return None

    csrf.init_app(app)

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.context_processor
    def inject_taxonomies():
        return {
            "performance_domains": PERFORMANCE_DOMAINS,
            "methodologies": METHODOLOGIES,
            "eco_domains": ECO_DOMAINS,
        }

    @app.template_filter("datetime")
    def format_datetime(timestamp):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))

    with app.app_context():
        init_db()

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        print("Base de datos inicializada.")

    @app.get("/")
    def index():
        if g_user_id():
            return redirect(url_for("dashboard"))
        return render_template("index.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirmation = request.form.get("confirmation", "")

            if not 3 <= len(username) <= 40:
                flash("El nombre de usuario debe tener entre 3 y 40 caracteres.", "error")
            elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                flash("Ingresa un correo electrónico válido.", "error")
            elif len(password) < 10:
                flash("La contraseña debe tener al menos 10 caracteres.", "error")
            elif password != confirmation:
                flash("Las contraseñas no coinciden.", "error")
            else:
                db = get_db()
                try:
                    cursor = db.execute(
                        "INSERT INTO users (username, email, password_hash, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (username, email, generate_password_hash(password), time.time()),
                    )
                    db.commit()
                except sqlite3.IntegrityError:
                    flash("Ese usuario o correo ya está registrado.", "error")
                else:
                    session.clear()
                    session["user_id"] = cursor.lastrowid
                    return redirect(url_for("dashboard"))
            return render_template("register.html"), 400
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_db().execute(
                "SELECT id, password_hash FROM users WHERE email = ? COLLATE NOCASE",
                (email,),
            ).fetchone()
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Correo o contraseña incorrectos.", "error")
                return render_template("login.html"), 401
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        db = get_db()
        user_id = g.user["id"]
        stats = db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN pending = 1 THEN 1 ELSE 0 END) AS pending, "
            "SUM(CASE WHEN favorite = 1 THEN 1 ELSE 0 END) AS favorites "
            "FROM user_question_states WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        recent_exams = db.execute(
            "SELECT id, requested_count, status, started_at, ended_at "
            "FROM exams WHERE user_id = ? ORDER BY started_at DESC LIMIT 5",
            (user_id,),
        ).fetchall()
        return render_template(
            "dashboard.html", stats=stats, recent_exams=recent_exams
        )

    @app.get("/practice")
    @login_required
    def practice_setup():
        db = get_db()
        valid_combinations = {
            (row["methodology_id"], row["eco_domain_id"])
            for row in db.execute(
                "SELECT methodology_id, eco_domain_id FROM valid_combinations"
            ).fetchall()
        }
        available_count = None
        selected = {
            "performance": request.args.getlist("performance"),
            "methodology": request.args.getlist("methodology"),
            "eco": request.args.getlist("eco"),
        }
        try:
            filters = validate_filters(db, selected)
            available_count = count_matching_questions(db, filters)
        except ValueError:
            pass
        return render_template(
            "practice_setup.html",
            selected=selected,
            valid_combinations=sorted(valid_combinations),
            available_count=available_count,
            exam_lengths=EXAM_LENGTHS,
        )

    @app.get("/practice/availability")
    @login_required
    def practice_availability():
        selected = {
            "performance": request.args.getlist("performance"),
            "methodology": request.args.getlist("methodology"),
            "eco": request.args.getlist("eco"),
        }
        try:
            filters = validate_filters(get_db(), selected)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return jsonify(available=count_matching_questions(get_db(), filters))

    @app.post("/practice/start")
    @login_required
    def start_exam():
        db = get_db()
        selected = {
            "performance": request.form.getlist("performance"),
            "methodology": request.form.getlist("methodology"),
            "eco": request.form.getlist("eco"),
        }
        try:
            filters = validate_filters(db, selected)
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("practice_setup"))

        try:
            requested_count = int(request.form.get("length", ""))
        except ValueError:
            abort(400, "Tamaño de examen no válido.")
        if requested_count not in EXAM_LENGTHS:
            abort(400, "Tamaño de examen no válido.")

        questions = matching_questions(db, filters, requested_count)
        if len(questions) < requested_count:
            flash(
                f"Hay {len(questions)} preguntas compatibles; se necesitan "
                f"{requested_count}. La prueba no se inició.",
                "error",
            )
            return redirect(url_for("practice_setup"))

        now = time.time()
        filters_json = json.dumps(filters, ensure_ascii=False)
        cursor = db.execute(
            "INSERT INTO exams "
            "(user_id, requested_count, filters_json, started_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                g.user["id"],
                requested_count,
                filters_json,
                now,
                now + requested_count * SECONDS_PER_QUESTION,
            ),
        )
        exam_id = cursor.lastrowid
        db.executemany(
            "INSERT INTO exam_questions (exam_id, question_id, position) "
            "VALUES (?, ?, ?)",
            ((exam_id, q["id"], position) for position, q in enumerate(questions, 1)),
        )
        db.execute(
            "INSERT INTO user_question_states (user_id, question_id, updated_at) "
            "SELECT ?, question_id, ? FROM exam_questions WHERE exam_id = ? "
            "ON CONFLICT(user_id, question_id) DO UPDATE SET updated_at = excluded.updated_at",
            (g.user["id"], now, exam_id),
        )
        db.commit()
        return redirect(url_for("take_exam", exam_id=exam_id, question=1))

    @app.get("/exams/<int:exam_id>")
    @login_required
    def take_exam(exam_id):
        db = get_db()
        exam = owned_exam(db, exam_id)
        if exam["status"] != "active":
            return redirect(url_for("exam_results", exam_id=exam_id))
        if time.time() >= exam["expires_at"]:
            finalize_exam(db, exam, expired=True)
            return redirect(url_for("exam_results", exam_id=exam_id))

        question_rows = [
            dict(row)
            for row in db.execute(
                "SELECT q.id, q.prompt, q.options_json, eq.position, eq.marked_saved, "
                "a.selected_option "
                "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
                "LEFT JOIN exam_answers a ON a.exam_id = eq.exam_id "
                "AND a.question_id = eq.question_id "
                "WHERE eq.exam_id = ? ORDER BY eq.position",
                (exam_id,),
            ).fetchall()
        ]
        try:
            current_position = int(request.args.get("question", "1"))
        except ValueError:
            current_position = 1
        current_position = min(max(current_position, 1), len(question_rows))
        current = dict(question_rows[current_position - 1])
        current["options"] = json.loads(current.pop("options_json"))
        for row in question_rows:
            row["options"] = json.loads(row["options_json"])
        return render_template(
            "exam.html",
            exam=exam,
            questions=question_rows,
            current=current,
            current_position=current_position,
            remaining_seconds=max(0, math.ceil(exam["expires_at"] - time.time())),
        )

    @app.post("/exams/<int:exam_id>/answer")
    @login_required
    def save_exam_answer(exam_id):
        db = get_db()
        exam = owned_exam(db, exam_id)
        if exam["status"] != "active":
            return redirect(url_for("exam_results", exam_id=exam_id))
        if time.time() >= exam["expires_at"]:
            finalize_exam(db, exam, expired=True)
            return redirect(url_for("exam_results", exam_id=exam_id))

        question_id = request.form.get("question_id", type=int)
        question = db.execute(
            "SELECT q.id, q.options_json, eq.position "
            "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
            "WHERE eq.exam_id = ? AND q.id = ?",
            (exam_id, question_id),
        ).fetchone()
        if question is None:
            abort(400, "La pregunta no pertenece a esta prueba.")

        action = request.form.get("action")
        if action == "answer":
            selected_option = request.form.get("selected_option", type=int)
            options = json.loads(question["options_json"])
            if selected_option is None or not 0 <= selected_option < len(options):
                flash("Selecciona una respuesta válida.", "error")
                return redirect(
                    url_for("take_exam", exam_id=exam_id, question=question["position"])
                )
            db.execute(
                "INSERT INTO exam_answers "
                "(exam_id, question_id, selected_option, answered_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(exam_id, question_id) DO UPDATE SET "
                "selected_option = excluded.selected_option, answered_at = excluded.answered_at",
                (exam_id, question_id, selected_option, time.time()),
            )
            db.execute(
                "UPDATE exam_questions SET marked_saved = 0 "
                "WHERE exam_id = ? AND question_id = ?",
                (exam_id, question_id),
            )
            db.execute(
                "UPDATE user_question_states SET pending = 0, updated_at = ? "
                "WHERE user_id = ? AND question_id = ?",
                (time.time(), g.user["id"], question_id),
            )
        elif action == "save":
            db.execute(
                "DELETE FROM exam_answers WHERE exam_id = ? AND question_id = ?",
                (exam_id, question_id),
            )
            db.execute(
                "UPDATE exam_questions SET marked_saved = 1 "
                "WHERE exam_id = ? AND question_id = ?",
                (exam_id, question_id),
            )
            db.execute(
                "UPDATE user_question_states SET pending = 1, updated_at = ? "
                "WHERE user_id = ? AND question_id = ?",
                (time.time(), g.user["id"], question_id),
            )
        elif action == "finish":
            db.commit()
            finalize_exam(db, exam)
            return redirect(url_for("exam_results", exam_id=exam_id))
        else:
            abort(400, "Acción no válida.")

        db.commit()
        next_position = request.form.get("next_position", type=int)
        next_position = next_position or question["position"]
        return redirect(url_for("take_exam", exam_id=exam_id, question=next_position))

    @app.get("/exams/<int:exam_id>/results")
    @login_required
    def exam_results(exam_id):
        db = get_db()
        exam = owned_exam(db, exam_id)
        if exam["status"] == "active":
            if time.time() >= exam["expires_at"]:
                finalize_exam(db, exam, expired=True)
                exam = owned_exam(db, exam_id)
            else:
                return redirect(url_for("take_exam", exam_id=exam_id))
        counts = db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN eq.marked_saved = 0 "
            "AND a.selected_option = q.correct_option THEN 1 ELSE 0 END) AS correct, "
            "SUM(CASE WHEN a.selected_option IS NOT NULL "
            "AND eq.marked_saved = 0 "
            "AND a.selected_option != q.correct_option THEN 1 ELSE 0 END) AS incorrect, "
            "SUM(CASE WHEN a.selected_option IS NULL OR eq.marked_saved = 1 "
            "THEN 1 ELSE 0 END) AS pending "
            "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
            "LEFT JOIN exam_answers a ON a.exam_id = eq.exam_id "
            "AND a.question_id = eq.question_id WHERE eq.exam_id = ?",
            (exam_id,),
        ).fetchone()
        return render_template("results.html", exam=exam, counts=counts)

    @app.get("/exams/<int:exam_id>/review")
    @login_required
    def review_exam(exam_id):
        db = get_db()
        exam = owned_exam(db, exam_id)
        if exam["status"] == "active":
            return redirect(url_for("exam_results", exam_id=exam_id))
        kind = request.args.get("kind", "both")
        if kind not in {"correct", "incorrect", "both", "pending"}:
            abort(400, "Filtro no válido.")
        rows = db.execute(
            "SELECT q.id, q.prompt, q.options_json, q.option_feedback_json, "
            "q.correct_option, q.explanation, "
            "a.selected_option, eq.marked_saved, eq.position "
            "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
            "LEFT JOIN exam_answers a ON a.exam_id = eq.exam_id "
            "AND a.question_id = eq.question_id WHERE eq.exam_id = ? "
            "ORDER BY eq.position",
            (exam_id,),
        ).fetchall()
        reviewed = []
        for row in rows:
            item = dict(row)
            item["options"] = json.loads(item.pop("options_json"))
            item["option_feedback"] = json.loads(item.pop("option_feedback_json"))
            if item["selected_option"] is None or item["marked_saved"]:
                item["result"] = "pending"
            elif item["selected_option"] == item["correct_option"]:
                item["result"] = "correct"
            else:
                item["result"] = "incorrect"
            if kind == "both" and item["result"] in {"correct", "incorrect"}:
                reviewed.append(item)
            elif kind == item["result"]:
                reviewed.append(item)
        return render_template(
            "review.html", exam=exam, questions=reviewed, selected_kind=kind
        )

    @app.get("/bank")
    @login_required
    def personal_bank():
        kind = request.args.get("kind", "all")
        if kind not in {"all", "used", "pending", "favorites"}:
            abort(400, "Filtro de banco no válido.")
        condition = {
            "all": "1 = 1",
            "used": "s.seen_count > 0",
            "pending": "s.pending = 1",
            "favorites": "s.favorite = 1",
        }[kind]
        rows = get_db().execute(
            "SELECT q.id, q.prompt, q.options_json, q.explanation, q.source_type, "
            "s.favorite, s.pending, s.seen_count FROM user_question_states s "
            "JOIN questions q ON q.id = s.question_id "
            f"WHERE s.user_id = ? AND {condition} ORDER BY s.updated_at DESC",
            (g.user["id"],),
        ).fetchall()
        questions = []
        for row in rows:
            item = dict(row)
            item["options"] = json.loads(item.pop("options_json"))
            questions.append(item)
        return render_template("bank.html", questions=questions, selected_kind=kind)

    @app.post("/bank/<int:question_id>/favorite")
    @login_required
    def toggle_favorite(question_id):
        db = get_db()
        exists = db.execute(
            "SELECT 1 FROM user_question_states WHERE user_id = ? AND question_id = ?",
            (g.user["id"], question_id),
        ).fetchone()
        if exists is None:
            abort(404)
        db.execute(
            "UPDATE user_question_states SET favorite = 1 - favorite, updated_at = ? "
            "WHERE user_id = ? AND question_id = ?",
            (time.time(), g.user["id"], question_id),
        )
        db.commit()
        return redirect(url_for("personal_bank"))

    return app


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        from flask import g as flask_g

        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("login", next=request.path))
        flask_g.user = get_db().execute(
            "SELECT id, username, email FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if flask_g.user is None:
            session.clear()
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def init_db():
    db = get_db()
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, encoding="utf-8") as schema_file:
        db.executescript(schema_file.read())
    question_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(questions)").fetchall()
    }
    if "option_feedback_json" not in question_columns:
        db.execute(
            "ALTER TABLE questions ADD COLUMN option_feedback_json "
            "TEXT NOT NULL DEFAULT '[]'"
        )
    db.executemany(
        "INSERT OR IGNORE INTO performance_domains (id, label) VALUES (?, ?)",
        PERFORMANCE_DOMAINS.items(),
    )
    db.executemany(
        "INSERT OR IGNORE INTO methodologies (id, label) VALUES (?, ?)",
        METHODOLOGIES.items(),
    )
    db.executemany(
        "INSERT OR IGNORE INTO eco_domains (id, label) VALUES (?, ?)",
        ECO_DOMAINS.items(),
    )
    db.executemany(
        "INSERT OR IGNORE INTO valid_combinations (methodology_id, eco_domain_id) "
        "VALUES (?, ?)",
        VALID_COMBINATIONS,
    )
    seed_demo_questions(db)
    db.commit()


def seed_demo_questions(db):
    seeded = db.execute(
        "SELECT value FROM app_metadata WHERE key = 'demo_questions_v1'"
    ).fetchone()
    for question in DEMO_QUESTIONS:
        db.execute(
            "UPDATE questions SET option_feedback_json = ? "
            "WHERE prompt = ? AND option_feedback_json = '[]'",
            (json.dumps(question[5], ensure_ascii=False), question[0]),
        )
    if seeded:
        return
    for index, combination in enumerate(VALID_COMBINATIONS):
        (
            prompt,
            options,
            correct_option,
            explanation,
            option_feedback,
            performance_domain,
        ) = DEMO_QUESTIONS[index]
        cursor = db.execute(
            "INSERT INTO questions "
            "(prompt, options_json, option_feedback_json, correct_option, explanation, "
            "source_type, created_at) VALUES (?, ?, ?, ?, ?, 'general', ?)",
            (
                prompt,
                json.dumps(options, ensure_ascii=False),
                json.dumps(option_feedback, ensure_ascii=False),
                correct_option,
                explanation,
                time.time(),
            ),
        )
        question_id = cursor.lastrowid
        db.execute(
            "INSERT INTO question_performance_domains (question_id, domain_id) "
            "VALUES (?, ?)",
            (question_id, performance_domain),
        )
        db.execute(
            "INSERT INTO question_methodologies (question_id, methodology_id) "
            "VALUES (?, ?)",
            (question_id, combination[0]),
        )
        db.execute(
            "INSERT INTO question_eco_domains (question_id, eco_domain_id) "
            "VALUES (?, ?)",
            (question_id, combination[1]),
        )
    db.execute(
        "INSERT INTO app_metadata (key, value) VALUES ('demo_questions_v1', 'seeded')"
    )


def validate_filters(db, selected):
    performance = list(dict.fromkeys(selected.get("performance", [])))
    methodologies = list(dict.fromkeys(selected.get("methodology", [])))
    eco_domains = list(dict.fromkeys(selected.get("eco", [])))
    if not performance or not methodologies or not eco_domains:
        raise ValueError("Selecciona al menos una categoría en cada grupo.")
    if not set(performance).issubset(PERFORMANCE_DOMAINS):
        raise ValueError("Hay un dominio de desempeño no válido.")
    if not set(methodologies).issubset(METHODOLOGIES):
        raise ValueError("Hay un enfoque metodológico no válido.")
    if not set(eco_domains).issubset(ECO_DOMAINS):
        raise ValueError("Hay un dominio oficial no válido.")
    allowed = {
        (row["methodology_id"], row["eco_domain_id"])
        for row in db.execute(
            "SELECT methodology_id, eco_domain_id FROM valid_combinations"
        ).fetchall()
    }
    if any((method, eco) not in allowed for method in methodologies for eco in eco_domains):
        raise ValueError(
            "La combinación entre enfoque metodológico y dominio oficial no está permitida."
        )
    return {
        "performance": performance,
        "methodology": methodologies,
        "eco": eco_domains,
    }


def matching_questions(db, filters, limit):
    performance_placeholders = ",".join("?" for _ in filters["performance"])
    methodology_placeholders = ",".join("?" for _ in filters["methodology"])
    eco_placeholders = ",".join("?" for _ in filters["eco"])
    query = (
        "SELECT q.id FROM questions q "
        "WHERE q.active = 1 "
        "AND EXISTS (SELECT 1 FROM question_performance_domains d "
        f"WHERE d.question_id = q.id AND d.domain_id IN ({performance_placeholders})) "
        "AND EXISTS (SELECT 1 FROM question_methodologies m "
        f"WHERE m.question_id = q.id AND m.methodology_id IN ({methodology_placeholders})) "
        "AND EXISTS (SELECT 1 FROM question_eco_domains e "
        f"WHERE e.question_id = q.id AND e.eco_domain_id IN ({eco_placeholders})) "
        "ORDER BY RANDOM() LIMIT ?"
    )
    params = (
        *filters["performance"],
        *filters["methodology"],
        *filters["eco"],
        limit,
    )
    return db.execute(query, params).fetchall()


def count_matching_questions(db, filters):
    performance_placeholders = ",".join("?" for _ in filters["performance"])
    methodology_placeholders = ",".join("?" for _ in filters["methodology"])
    eco_placeholders = ",".join("?" for _ in filters["eco"])
    query = (
        "SELECT COUNT(*) FROM questions q "
        "WHERE q.active = 1 "
        "AND EXISTS (SELECT 1 FROM question_performance_domains d "
        f"WHERE d.question_id = q.id AND d.domain_id IN ({performance_placeholders})) "
        "AND EXISTS (SELECT 1 FROM question_methodologies m "
        f"WHERE m.question_id = q.id AND m.methodology_id IN ({methodology_placeholders})) "
        "AND EXISTS (SELECT 1 FROM question_eco_domains e "
        f"WHERE e.question_id = q.id AND e.eco_domain_id IN ({eco_placeholders}))"
    )
    params = (
        *filters["performance"],
        *filters["methodology"],
        *filters["eco"],
    )
    return db.execute(query, params).fetchone()[0]


def owned_exam(db, exam_id):
    exam = db.execute(
        "SELECT * FROM exams WHERE id = ? AND user_id = ?",
        (exam_id, g.user["id"]),
    ).fetchone()
    if exam is None:
        abort(404)
    return exam


def finalize_exam(db, exam, expired=False):
    if exam["status"] != "active":
        return
    now = time.time()
    updated = db.execute(
        "UPDATE exams SET status = ?, ended_at = ? WHERE id = ? AND status = 'active'",
        ("expired" if expired else "completed", now, exam["id"]),
    )
    if updated.rowcount != 1:
        db.commit()
        return
    db.execute(
        "INSERT INTO user_question_states "
        "(user_id, question_id, pending, seen_count, updated_at) "
        "SELECT ?, eq.question_id, "
        "CASE WHEN eq.marked_saved = 1 OR a.selected_option IS NULL THEN 1 ELSE 0 END, "
        "1, ? FROM exam_questions eq LEFT JOIN exam_answers a "
        "ON a.exam_id = eq.exam_id AND a.question_id = eq.question_id "
        "WHERE eq.exam_id = ? "
        "ON CONFLICT(user_id, question_id) DO UPDATE SET "
        "pending = CASE WHEN excluded.pending = 1 THEN 1 ELSE 0 END, "
        "seen_count = user_question_states.seen_count + 1, updated_at = excluded.updated_at",
        (exam["user_id"], now, exam["id"]),
    )
    db.commit()


def g_user_id():
    return session.get("user_id")


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
