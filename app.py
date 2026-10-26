import json
import html
import math
import os
import random
import re
import secrets
import tempfile
import threading
import time
from functools import wraps

import click
import psycopg2
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
from markupsafe import Markup
from conexion import connect_database, database_config_from_env
from werkzeug.security import check_password_hash, generate_password_hash


PERFORMANCE_DOMAINS = {
    "governance": "Gobernanza (Governance)",
    "scope": "Alcance (Scope)",
    "schedule": "Cronograma (Schedule)",
    "finance": "Finanzas (Finance)",
    "stakeholders": "Interesados (Stakeholders)",
    "resources": "Recursos (Resources)",
    "risk": "Riesgos (Risk)",
}
METHODOLOGIES = {
    "predictive": "Predictivo",
    "agile_hybrid": "Ágil/Híbrido",
}
ECO_DOMAINS = {
    "processes": "Procesos",
    "people": "Personas",
    "business_environment": "Entorno de Negocio",
}
VALID_COMBINATIONS = tuple(
    (methodology, eco_domain)
    for methodology in METHODOLOGIES
    for eco_domain in ECO_DOMAINS
)
PRACTICE_LENGTHS = (15, 30)
OFFICIAL_EXAM_LENGTHS = (60, 180)
SECONDS_PER_QUESTION = 80
OFFICIAL_EXAM_DISTRIBUTIONS = {
    60: {
        ("people", "agile_hybrid"): 12,
        ("people", "predictive"): 8,
        ("processes", "agile_hybrid"): 15,
        ("processes", "predictive"): 10,
        ("business_environment", "agile_hybrid"): 9,
        ("business_environment", "predictive"): 6,
    },
    180: {
        ("people", "agile_hybrid"): 36,
        ("people", "predictive"): 23,
        ("processes", "agile_hybrid"): 44,
        ("processes", "predictive"): 30,
        ("business_environment", "agile_hybrid"): 28,
        ("business_environment", "predictive"): 19,
    },
}
QUESTION_TYPES = {
    "case_set": (
        "Case/Scenario Set",
        "Estudio de caso extenso seguido de 3 a 5 preguntas encadenadas.",
    ),
    "visual_based": (
        "Visual-Based (Nuevo 2026)",
        "Reactivo basado en un artefacto gráfico, como un tablero o dashboard.",
    ),
    "multiple_choice": (
        "Multiple Choice (Tradicional)",
        "Selección única de la mejor alternativa entre cuatro opciones.",
    ),
    "multiple_responses": (
        "Multiple Responses",
        "Reactivo con más de una opción correcta.",
    ),
    "drag_drop": (
        "Drag and Drop",
        "Emparejamiento de conceptos o acciones en dos columnas.",
    ),
    "hotspot": (
        "Hotspot",
        "Identificación directa de una zona en un diagrama o gráfico.",
    ),
    "pull_down": (
        "Pull-Down List (Fill in the blank)",
        "Párrafo con uno o varios espacios en blanco interactivos.",
    ),
}
MOBILE_USER_AGENT_PATTERN = re.compile(
    r"android|iphone|ipad|ipod|mobile|tablet|windows phone|iemobile|opera mini|blackberry|bb10",
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


def render_rich_option(value):
    escaped = html.escape(str(value), quote=True)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return Markup(escaped)


def create_app(test_config=None, initialize_database=True):
    instance_path = None
    if os.environ.get("VERCEL"):
        instance_path = os.path.join(tempfile.gettempdir(), "simulador-pmp")
    app = Flask(
        __name__,
        instance_relative_config=True,
        instance_path=instance_path,
    )
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=persistent_secret(app.instance_path),
        DATABASE_CONFIG=database_config_from_env(),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    csrf = CSRFProtect()

    csrf.init_app(app)

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.before_request
    def block_mobile_devices():
        if MOBILE_USER_AGENT_PATTERN.search(request.user_agent.string):
            abort(403, description="El acceso desde dispositivos móviles no está permitido.")

    @app.context_processor
    def inject_taxonomies():
        return {
            "performance_domains": PERFORMANCE_DOMAINS,
            "methodologies": METHODOLOGIES,
            "eco_domains": ECO_DOMAINS,
            "question_types": QUESTION_TYPES,
        }

    @app.template_filter("datetime")
    def format_datetime(timestamp):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))

    @app.template_filter("rich_option")
    def rich_option_filter(value):
        return render_rich_option(value)

    if initialize_database:
        with app.app_context():
            init_db()
    else:
        database_initialization_lock = threading.Lock()

        @app.before_request
        def initialize_database_on_first_request():
            if app.extensions.get("simulator_database_initialized"):
                return
            with database_initialization_lock:
                if not app.extensions.get("simulator_database_initialized"):
                    init_db()
                    app.extensions["simulator_database_initialized"] = True

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        print("Base de datos inicializada.")

    @app.cli.command("create-owner")
    def create_owner_command():
        """Crea la cuenta inicial Owner mediante preguntas interactivas."""
        init_db()
        db = get_db()
        existing_owner = db.execute(
            "SELECT 1 FROM super_users WHERE role = 'Owner'"
        ).fetchone()
        if existing_owner:
            raise click.ClickException("Ya existe una cuenta Owner.")

        username = click.prompt("Nombre de usuario")
        first_name = click.prompt("Primer nombre")
        second_name = click.prompt("Segundo nombre", default="")
        first_surname = click.prompt("Primer apellido")
        second_surname = click.prompt("Segundo apellido", default="")
        document_id = click.prompt("Documento de ID", default="")
        primary_phone = click.prompt("Teléfono principal (+código de país)")
        secondary_phone = click.prompt("Teléfono secundario (+código de país)", default="")
        email = click.prompt("Correo electrónico").strip().lower()
        password = click.prompt(
            "Contraseña (mínimo 10 caracteres)",
            hide_input=True,
            confirmation_prompt=True,
        )
        if len(password) < 10:
            raise click.ClickException("La contraseña debe tener al menos 10 caracteres.")
        if not 3 <= len(username) <= 40 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email
        ):
            raise click.ClickException("El usuario o correo electrónico no es válido.")
        try:
            primary_phone = normalize_phone_number(primary_phone, required=True)
            secondary_phone = normalize_phone_number(secondary_phone)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        try:
            create_superuser_account(
                db,
                username=username,
                first_name=first_name,
                second_name=second_name,
                first_surname=first_surname,
                second_surname=second_surname,
                document_id=document_id,
                primary_phone=primary_phone,
                secondary_phone=secondary_phone,
                email=email,
                password=password,
                role="Owner",
            )
            db.commit()
        except psycopg2.IntegrityError as error:
            db.rollback()
            raise click.ClickException(
                "No se pudo crear el Owner: revisa que el usuario y correo no existan."
            ) from error
        click.echo("Cuenta Owner creada.")

    @app.get("/")
    def index():
        if g_user_id():
            return redirect(url_for("dashboard"))
        return render_template("index.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            first_name = request.form.get("first_name", "").strip()
            second_name = request.form.get("second_name", "").strip()
            first_surname = request.form.get("first_surname", "").strip()
            second_surname = request.form.get("second_surname", "").strip()
            document_id = request.form.get("document_id", "").strip()
            primary_phone = request.form.get("primary_phone", "")
            secondary_phone = request.form.get("secondary_phone", "")
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirmation = request.form.get("confirmation", "")

            if not 3 <= len(username) <= 40:
                flash("El nombre de usuario debe tener entre 3 y 40 caracteres.", "error")
            elif not first_name or not first_surname:
                flash("El primer nombre y el primer apellido son obligatorios.", "error")
            elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                flash("Ingresa un correo electrónico válido.", "error")
            elif len(password) < 10:
                flash("La contraseña debe tener al menos 10 caracteres.", "error")
            elif password != confirmation:
                flash("Las contraseñas no coinciden.", "error")
            else:
                try:
                    primary_phone = normalize_phone_number(
                        primary_phone, required=True
                    )
                    secondary_phone = normalize_phone_number(secondary_phone)
                except ValueError as error:
                    flash(str(error), "error")
                    return render_template("register.html"), 400
                db = get_db()
                try:
                    created_at = time.time()
                    cursor = db.execute(
                        "INSERT INTO users "
                        "(username, email, password_hash, created_at, first_name, "
                        "second_name, first_surname, second_surname, document_id, "
                        "primary_phone, secondary_phone) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "RETURNING id",
                        (
                            username,
                            email,
                            generate_password_hash(password),
                            created_at,
                            first_name,
                            second_name or None,
                            first_surname,
                            second_surname or None,
                            document_id or None,
                            primary_phone,
                            secondary_phone,
                        ),
                    )
                    user_id = cursor.fetchone()["id"]
                    db.execute(
                        "INSERT INTO user_profiles "
                        "(user_id, email, registration_date) VALUES (%s, %s, %s)",
                        (user_id, email, created_at),
                    )
                    db.commit()
                except psycopg2.IntegrityError:
                    db.rollback()
                    flash("Ese usuario o correo ya está registrado.", "error")
                else:
                    session.clear()
                    session["user_id"] = str(user_id)
                    return redirect(url_for("dashboard"))
            return render_template("register.html"), 400
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_db().execute(
                "SELECT u.id, u.password_hash, u.account_role, "
                "CASE WHEN u.account_role IN ('owner', 'admin') "
                "THEN COALESCE(s.status, 'inactive') "
                "ELSE COALESCE(p.status, 'inactive') END AS status "
                "FROM users u "
                "LEFT JOIN user_profiles p ON p.user_id = u.id "
                "LEFT JOIN super_users s ON s.user_id = u.id "
                "WHERE lower(u.email) = lower(%s)",
                (email,),
            ).fetchone()
            if (
                user is None
                or user["status"] != "active"
                or not check_password_hash(user["password_hash"], password)
            ):
                flash("Correo o contraseña incorrectos.", "error")
                return render_template("login.html"), 401
            get_db().execute(
                "UPDATE user_profiles SET last_login_at = %s WHERE user_id = %s",
                (time.time(), user["id"]),
            )
            get_db().commit()
            session.clear()
            session["user_id"] = str(user["id"])
            if user["account_role"] in {"owner", "admin"}:
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        if g.user["account_role"] in {"owner", "admin"}:
            return redirect(url_for("admin_dashboard"))
        db = get_db()
        user_id = g.user["id"]
        stats = db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN pending = 1 THEN 1 ELSE 0 END) AS pending, "
            "SUM(CASE WHEN favorite = 1 THEN 1 ELSE 0 END) AS favorites "
            "FROM user_question_states WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        recent_exams = db.execute(
            "SELECT id, requested_count, status, started_at, ended_at "
            "FROM exams WHERE user_id = %s ORDER BY started_at DESC LIMIT 5",
            (user_id,),
        ).fetchall()
        consistency = practice_consistency(db, user_id)
        return render_template(
            "dashboard.html",
            stats=stats,
            recent_exams=recent_exams,
            consistency=consistency,
        )

    @app.get("/practice")
    @login_required
    def practice_setup():
        selected = {
            "axis": request.args.get("axis", ""),
            "values": request.args.getlist("category"),
            "length": request.args.get("length", ""),
        }
        return render_template(
            "practice_setup.html",
            selected=selected,
            available_count=None,
            practice_lengths=PRACTICE_LENGTHS,
            official_exam_lengths=OFFICIAL_EXAM_LENGTHS,
            seconds_per_question=SECONDS_PER_QUESTION,
        )

    @app.get("/practice/availability")
    @login_required
    def practice_availability():
        mode = request.args.get("mode", "practice")
        try:
            requested_count = int(request.args.get("length", ""))
        except ValueError:
            return jsonify(error="Tamaño de prueba no válido."), 400
        if mode == "official":
            if requested_count not in OFFICIAL_EXAM_LENGTHS:
                return jsonify(error="El simulacro debe tener 60 o 180 preguntas."), 400
            db = get_db()
            available, shortages = count_official_question_availability(
                db, requested_count
            )
            ready = not shortages
            message = (
                f"Preguntas compatibles disponibles: {available}."
                if ready
                else "No hay suficientes preguntas clasificadas para completar la distribución oficial."
            )
            return jsonify(available=available, ready=ready, message=message)
        if mode != "practice" or requested_count not in PRACTICE_LENGTHS:
            return jsonify(error="Selecciona un modo y tamaño de práctica válidos."), 400
        selected = {
            "axis": request.args.get("axis", ""),
            "values": request.args.getlist("category"),
        }
        try:
            filters = validate_filters(get_db(), selected)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        available = count_matching_questions(get_db(), filters)
        return jsonify(
            available=available,
            ready=available >= requested_count,
            message=f"Preguntas compatibles disponibles: {available}.",
        )

    @app.post("/practice/start")
    @login_required
    def start_exam():
        db = get_db()
        try:
            requested_count = int(request.form.get("length", ""))
        except ValueError as error:
            abort(400, "Tamaño de examen no válido.")
        mode = request.form.get("mode") or (
            "official" if requested_count in OFFICIAL_EXAM_LENGTHS else "practice"
        )
        if mode == "practice":
            if requested_count not in PRACTICE_LENGTHS:
                abort(400, "Las prácticas cortas deben tener 15 o 30 preguntas.")
            selected = {
                "axis": request.form.get("axis", ""),
                "values": request.form.getlist("category"),
            }
            try:
                filters = validate_filters(db, selected)
            except ValueError as error:
                flash(str(error), "error")
                return redirect(url_for("practice_setup"))
            filters["mode"] = "practice"
            questions = matching_questions(db, filters, requested_count)
        elif mode == "official":
            if requested_count not in OFFICIAL_EXAM_LENGTHS:
                abort(400, "Los simulacros oficiales deben tener 60 o 180 preguntas.")
            filters = {"mode": "official"}
            questions, shortages = official_exam_questions(db, requested_count)
            if shortages:
                flash(
                    "No hay suficientes preguntas etiquetadas para completar la "
                    "distribución ECO y de enfoque requerida.",
                    "error",
                )
                return redirect(url_for("practice_setup"))
        else:
            abort(400, "Modo de prueba no válido.")

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
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (
                g.user["id"],
                requested_count,
                filters_json,
                now,
                now + requested_count * SECONDS_PER_QUESTION,
            ),
        )
        exam_id = cursor.fetchone()["id"]
        db.executemany(
            "INSERT INTO exam_questions (exam_id, question_id, position) "
            "VALUES (%s, %s, %s)",
            ((exam_id, q["id"], position) for position, q in enumerate(questions, 1)),
        )
        db.execute(
            "INSERT INTO user_question_states (user_id, question_id, updated_at) "
            "SELECT %s, question_id, %s FROM exam_questions WHERE exam_id = %s "
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
                "SELECT q.id, q.prompt, q.options_json, q.question_type, q.content_json, "
                "q.correct_answer_json, eq.position, eq.marked_saved, "
                "a.selected_option, a.response_json "
                "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
                "LEFT JOIN exam_answers a ON a.exam_id = eq.exam_id "
                "AND a.question_id = eq.question_id "
                "WHERE eq.exam_id = %s ORDER BY eq.position",
                (exam_id,),
            ).fetchall()
        ]
        try:
            current_position = int(request.args.get("question", "1"))
        except ValueError:
            current_position = 1
        current_position = min(max(current_position, 1), len(question_rows))
        for row in question_rows:
            prepare_exam_question(row)
        current = question_rows[current_position - 1]
        current["active_subquestion"] = 0
        if current["question_type"] == "case_set":
            active_subquestion = request.args.get("subquestion", 1, type=int) - 1
            current["active_subquestion"] = min(
                max(active_subquestion, 0), len(current["content"]["questions"]) - 1
            )
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
            "SELECT q.id, q.options_json, q.question_type, q.content_json, "
            "q.correct_answer_json, eq.position "
            "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
            "WHERE eq.exam_id = %s AND q.id = %s",
            (exam_id, question_id),
        ).fetchone()
        if question is None:
            abort(400, "La pregunta no pertenece a esta prueba.")

        action = request.form.get("action")
        if action == "answer":
            try:
                response = parse_exam_response(question)
            except ValueError as error:
                flash(str(error), "error")
                return redirect(
                    url_for("take_exam", exam_id=exam_id, question=question["position"])
                )
            selected_option = response if isinstance(response, int) else 0
            db.execute(
                "INSERT INTO exam_answers "
                "(exam_id, question_id, selected_option, response_json, answered_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT(exam_id, question_id) DO UPDATE SET "
                "selected_option = excluded.selected_option, "
                "response_json = excluded.response_json, answered_at = excluded.answered_at",
                (
                    exam_id,
                    question_id,
                    selected_option,
                    json.dumps(response, ensure_ascii=False),
                    time.time(),
                ),
            )
            db.execute(
                "UPDATE exam_questions SET marked_saved = 0 "
                "WHERE exam_id = %s AND question_id = %s",
                (exam_id, question_id),
            )
            db.execute(
                "UPDATE user_question_states SET pending = 0, updated_at = %s "
                "WHERE user_id = %s AND question_id = %s",
                (time.time(), g.user["id"], question_id),
            )
        elif action == "save":
            db.execute(
                "DELETE FROM exam_answers WHERE exam_id = %s AND question_id = %s",
                (exam_id, question_id),
            )
            db.execute(
                "UPDATE exam_questions SET marked_saved = 1 "
                "WHERE exam_id = %s AND question_id = %s",
                (exam_id, question_id),
            )
            db.execute(
                "UPDATE user_question_states SET pending = 1, updated_at = %s "
                "WHERE user_id = %s AND question_id = %s",
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
        rows = db.execute(
            "SELECT q.question_type, q.content_json, q.correct_answer_json, "
            "q.correct_option, eq.marked_saved, a.selected_option, a.response_json, "
            "(SELECT eco_domain_id FROM question_eco_domains "
            "WHERE question_id = q.id LIMIT 1) AS eco_domain_id, "
            "(SELECT methodology_id FROM question_methodologies "
            "WHERE question_id = q.id LIMIT 1) AS methodology_id "
            "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
            "LEFT JOIN exam_answers a ON a.exam_id = eq.exam_id "
            "AND a.question_id = eq.question_id WHERE eq.exam_id = %s",
            (exam_id,),
        ).fetchall()
        counts = {"total": len(rows), "correct": 0, "incorrect": 0, "pending": 0}
        for row in rows:
            if row["marked_saved"] or (
                row["response_json"] is None and row["selected_option"] is None
            ):
                counts["pending"] += 1
            elif exam_response_is_correct(row):
                counts["correct"] += 1
            else:
                counts["incorrect"] += 1
        counts["score"] = (
            round(counts["correct"] * 100 / counts["total"])
            if counts["total"]
            else 0
        )
        exam_filters = json.loads(exam["filters_json"])
        official_exam = exam_filters.get("mode") == "official"
        breakdown = {}
        if official_exam:
            for axis, labels in (
                ("eco", ECO_DOMAINS),
                ("methodology", METHODOLOGIES),
            ):
                row_key = "eco_domain_id" if axis == "eco" else "methodology_id"
                axis_breakdown = []
                for category_id, label in labels.items():
                    relevant = [
                        row for row in rows
                        if row[row_key] == category_id
                        or (
                            axis == "methodology"
                            and category_id == "agile_hybrid"
                            and row[row_key] in {"agile", "hybrid"}
                        )
                    ]
                    correct = sum(exam_response_is_correct(row) for row in relevant)
                    axis_breakdown.append(
                        {
                            "label": label,
                            "correct": correct,
                            "total": len(relevant),
                            "score": (
                                round(correct * 100 / len(relevant))
                                if relevant
                                else None
                            ),
                        }
                    )
                breakdown[axis] = axis_breakdown
        return render_template(
            "results.html",
            exam=exam,
            counts=counts,
            official_exam=official_exam,
            breakdown=breakdown,
        )

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
            "q.correct_option, q.question_type, q.content_json, "
            "q.correct_answer_json, q.explanation, a.selected_option, "
            "a.response_json, eq.marked_saved, eq.position "
            "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
            "LEFT JOIN exam_answers a ON a.exam_id = eq.exam_id "
            "AND a.question_id = eq.question_id WHERE eq.exam_id = %s "
            "ORDER BY eq.position",
            (exam_id,),
        ).fetchall()
        reviewed = []
        for row in rows:
            item = dict(row)
            item["options"] = json.loads(item.pop("options_json"))
            item["option_feedback"] = json.loads(item.pop("option_feedback_json"))
            item["content"] = json.loads(item.pop("content_json") or "{}")
            item["correct_answer"] = json.loads(
                item.pop("correct_answer_json") or str(item["correct_option"])
            )
            response = (
                json.loads(item["response_json"])
                if item["response_json"] is not None
                else item["selected_option"]
            )
            item["review_prompt"] = item["prompt"]
            if item["question_type"] == "pull_down":
                blanks = response if response is not None else ["________"] * (
                    len(item["content"]["prompt_parts"]) - 1
                )
                item["review_prompt"] = "".join(
                    part + (item["options"][blanks[index]] if index < len(blanks) else "")
                    for index, part in enumerate(item["content"]["prompt_parts"])
                )
            if response is None or item["marked_saved"]:
                item["result"] = "pending"
            elif exam_response_is_correct(
                {**item, "response_json": json.dumps(response)}
            ):
                item["result"] = "correct"
            else:
                item["result"] = "incorrect"
            item["user_answer_display"] = format_exam_answer(
                item["question_type"], item["options"], item["content"], response
            )
            item["correct_answer_display"] = format_exam_answer(
                item["question_type"],
                item["options"],
                item["content"],
                item["correct_answer"],
                correct=True,
            )
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
            f"WHERE s.user_id = %s AND {condition} ORDER BY s.updated_at DESC",
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
            "SELECT 1 FROM user_question_states WHERE user_id = %s AND question_id = %s",
            (g.user["id"], question_id),
        ).fetchone()
        if exists is None:
            abort(404)
        db.execute(
            "UPDATE user_question_states SET favorite = 1 - favorite, updated_at = %s "
            "WHERE user_id = %s AND question_id = %s",
            (time.time(), g.user["id"], question_id),
        )
        db.commit()
        return redirect(url_for("personal_bank"))

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        db = get_db()
        counts = {
            "users": db.execute(
                "SELECT COUNT(*) AS count FROM users WHERE account_role = 'user'"
            ).fetchone()["count"],
            "manual_questions": db.execute(
                "SELECT COUNT(*) AS count FROM manual_question_bank"
            ).fetchone()["count"],
            "automated_questions": db.execute(
                "SELECT COUNT(*) AS count FROM automated_question_bank"
            ).fetchone()["count"],
            "pending_questions": db.execute(
                "SELECT COUNT(*) AS count FROM automated_question_bank "
                "WHERE validation_status = 'pending'"
            ).fetchone()["count"],
        }
        recent_users = db.execute(
            "SELECT u.id, u.username, u.email, u.first_name, u.second_name, "
            "u.first_surname, u.second_surname, p.registration_date, p.status "
            "FROM users u JOIN user_profiles p ON p.user_id = u.id "
            "WHERE u.account_role = 'user' ORDER BY p.registration_date DESC LIMIT 10"
        ).fetchall()
        return render_template(
            "admin/dashboard.html", counts=counts, recent_users=recent_users
        )

    @app.get("/admin/users")
    @admin_required
    def admin_users():
        users = get_db().execute(
            "SELECT u.id, u.username, u.email, u.first_name, u.second_name, "
            "u.first_surname, u.second_surname, u.document_id, u.primary_phone, "
            "u.secondary_phone, "
            "p.registration_date, p.last_login_at, p.status, "
            "(SELECT COUNT(*) FROM exams e WHERE e.user_id = u.id) AS exam_count "
            "FROM users u JOIN user_profiles p ON p.user_id = u.id "
            "WHERE u.account_role = 'user' ORDER BY p.registration_date DESC"
        ).fetchall()
        return render_template("admin/users.html", users=users)

    @app.post("/admin/users/<uuid:user_id>/status")
    @admin_required
    def set_user_status(user_id):
        status = request.form.get("status")
        if status not in {"active", "inactive"}:
            abort(400, "Estado de usuario no válido.")
        result = get_db().execute(
            "UPDATE user_profiles SET status = %s "
            "WHERE user_id = %s AND user_id IN "
            "(SELECT id FROM users WHERE account_role = 'user')",
            (status, str(user_id)),
        )
        if result.rowcount != 1:
            abort(404)
        get_db().commit()
        flash("Estado del usuario actualizado.", "success")
        return redirect(url_for("admin_users"))

    @app.get("/admin/administrators")
    @owner_required
    def admin_administrators():
        administrators = get_db().execute(
            "SELECT u.id, u.first_name, u.second_name, u.first_surname, "
            "u.second_surname, u.document_id, u.primary_phone, u.secondary_phone, "
            "u.email, s.business_id, s.role, s.status, u.username "
            "FROM super_users s JOIN users u ON u.id = s.user_id "
            "WHERE s.role = 'Administrator' "
            "ORDER BY u.first_name, u.first_surname"
        ).fetchall()
        return render_template("admin/administrators.html", administrators=administrators)

    @app.post("/admin/administrators")
    @owner_required
    def create_administrator():
        username = request.form.get("username", "").strip()
        first_name = request.form.get("first_name", "").strip()
        second_name = request.form.get("second_name", "").strip()
        first_surname = request.form.get("first_surname", "").strip()
        second_surname = request.form.get("second_surname", "").strip()
        document_id = request.form.get("document_id", "").strip()
        primary_phone = request.form.get("primary_phone", "")
        secondary_phone = request.form.get("secondary_phone", "")
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")
        business_id = request.form.get("business_id", "").strip()
        age = request.form.get("age", type=int)
        if (
            not 3 <= len(username) <= 40
            or not first_name
            or not first_surname
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            or len(password) < 10
            or password != confirmation
            or (age is not None and not 1 <= age <= 120)
        ):
            flash("Revisa los datos; la contraseña debe tener al menos 10 caracteres.", "error")
            return redirect(url_for("admin_administrators"))
        try:
            primary_phone = normalize_phone_number(primary_phone, required=True)
            secondary_phone = normalize_phone_number(secondary_phone)
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("admin_administrators"))
        db = get_db()
        try:
            create_superuser_account(
                db,
                username=username,
                first_name=first_name,
                second_name=second_name,
                first_surname=first_surname,
                second_surname=second_surname,
                document_id=document_id,
                primary_phone=primary_phone,
                secondary_phone=secondary_phone,
                email=email,
                password=password,
                role="Administrator",
                age=age,
                business_id=business_id,
            )
            db.commit()
        except psycopg2.IntegrityError:
            db.rollback()
            flash("Ese nombre de usuario o correo ya está registrado.", "error")
            return redirect(url_for("admin_administrators"))
        flash("Administrador creado.", "success")
        return redirect(url_for("admin_administrators"))

    @app.post("/admin/administrators/<uuid:admin_id>/status")
    @owner_required
    def set_administrator_status(admin_id):
        status = request.form.get("status")
        if status not in {"active", "inactive"}:
            abort(400, "Estado de administrador no válido.")
        result = get_db().execute(
            "UPDATE super_users SET status = %s "
            "WHERE user_id = %s AND role = 'Administrator'",
            (status, str(admin_id)),
        )
        if result.rowcount != 1:
            abort(404)
        get_db().commit()
        flash("Estado del administrador actualizado.", "success")
        return redirect(url_for("admin_administrators"))

    @app.post("/admin/administrators/<uuid:admin_id>/delete")
    @owner_required
    def delete_administrator(admin_id):
        result = get_db().execute(
            "DELETE FROM users WHERE id = "
            "(SELECT user_id FROM super_users WHERE user_id = %s "
            "AND role = 'Administrator') RETURNING id",
            (str(admin_id),),
        )
        if result.fetchone() is None:
            abort(404)
        get_db().commit()
        flash("Administrador eliminado.", "success")
        return redirect(url_for("admin_administrators"))

    @app.get("/admin/questions")
    @admin_required
    def admin_questions():
        db = get_db()
        manual = db.execute(
            "SELECT b.id, b.question_id, b.stem AS question, b.added_at, "
            "CASE WHEN b.status THEN 'active' ELSE 'inactive' END AS status, "
            "q.question_type, NULL AS validation_status, q.active "
            "FROM manual_question_bank b LEFT JOIN questions q ON q.id = b.question_id"
        ).fetchall()
        automated = db.execute(
            "SELECT b.id, b.question_id, b.question, b.added_at, b.status, "
            "b.validation_status, q.question_type, q.active "
            "FROM automated_question_bank b "
            "LEFT JOIN questions q ON q.id = b.question_id"
        ).fetchall()
        return render_template(
            "admin/questions.html", manual=manual, automated=automated
        )

    @app.route("/admin/questions/new", methods=["GET", "POST"])
    @admin_required
    def new_admin_question():
        kind = request.values.get("kind", "manual")
        if kind not in {"manual", "automated"}:
            abort(400, "Tipo de banco no válido.")
        if request.method == "POST":
            errors = save_admin_question(get_db(), kind)
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template(
                    "admin/question_form.html",
                    kind=kind,
                    question=None,
                    selected=admin_question_selections(),
                ), 400
            get_db().commit()
            flash("Pregunta agregada al banco.", "success")
            return redirect(url_for("admin_questions"))
        return render_template(
            "admin/question_form.html",
            kind=kind,
            question=None,
            selected={
                "axis": "",
                "value": "",
                "performance": [],
                "official": [],
                "methodology": [],
            },
        )

    @app.route("/admin/questions/<kind>/<int:bank_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_admin_question(kind, bank_id):
        if kind not in {"manual", "automated"}:
            abort(404)
        table = f"{kind}_question_bank"
        db = get_db()
        bank_entry = db.execute(
            f"SELECT * FROM {table} WHERE id = %s", (bank_id,)
        ).fetchone()
        if bank_entry is None or bank_entry["question_id"] is None:
            abort(404)
        question = db.execute(
            "SELECT * FROM questions WHERE id = %s", (bank_entry["question_id"],)
        ).fetchone()
        if question is None:
            abort(404)
        if request.method == "POST":
            errors = save_admin_question(db, kind, bank_entry, question)
            if errors:
                for error in errors:
                    flash(error, "error")
                selected = admin_question_selections()
                question = question_form_values(question, selected)
                return render_template(
                    "admin/question_form.html",
                    kind=kind,
                    question=question,
                    selected=selected,
                ), 400
            db.commit()
            flash("Pregunta actualizada.", "success")
            return redirect(url_for("admin_questions"))
        tags = db.execute(
            "SELECT "
            "(SELECT domain_id FROM question_performance_domains "
            "WHERE question_id = q.id LIMIT 1) AS performance_domain, "
            "(SELECT eco_domain_id FROM question_eco_domains "
            "WHERE question_id = q.id LIMIT 1) AS eco_domain, "
            "(SELECT methodology_id FROM question_methodologies "
            "WHERE question_id = q.id LIMIT 1) AS methodology "
            "FROM questions q WHERE q.id = %s",
            (question["id"],),
        ).fetchone()
        selected = {
            "axis": "",
            "value": tags["performance_domain"] or "",
            "performance": [tags["performance_domain"]]
            if tags["performance_domain"]
            else [],
            "official": [tags["eco_domain"]] if tags["eco_domain"] else [],
            "methodology": [tags["methodology"]] if tags["methodology"] else [],
        }
        question = question_form_values(question, selected)
        return render_template(
            "admin/question_form.html",
            kind=kind,
            question=question,
            selected=selected,
        )

    @app.post("/admin/questions/<kind>/<int:bank_id>/status")
    @admin_required
    def set_admin_question_status(kind, bank_id):
        db = get_db()
        if kind == "manual":
            status = request.form.get("status")
            if status not in {"active", "inactive"}:
                abort(400, "Estado de pregunta no válido.")
            table = "manual_question_bank"
            validation_sql = ""
            active = status == "active"
        elif kind == "automated":
            validation = request.form.get("validation_status")
            if validation not in {"approved", "rejected"}:
                abort(400, "La validación debe ser aprobada o rechazada.")
            status = "active" if validation == "approved" else "inactive"
            table = "automated_question_bank"
            validation_sql = ", validation_status = %s"
            active = validation == "approved"
        else:
            abort(404)

        if kind == "manual":
            result = db.execute(
                f"UPDATE {table} SET status = %s WHERE id = %s RETURNING question_id",
                (active, bank_id),
            )
        else:
            result = db.execute(
                f"UPDATE {table} SET status = %s{validation_sql} "
                "WHERE id = %s RETURNING question_id",
                (status, request.form["validation_status"], bank_id),
            )
        entry = result.fetchone()
        if entry is None:
            abort(404)
        if entry["question_id"] is not None:
            db.execute(
                "UPDATE questions SET active = %s WHERE id = %s",
                (int(active), entry["question_id"]),
            )
        db.commit()
        flash("Estado y disponibilidad de la pregunta actualizados.", "success")
        return redirect(url_for("admin_questions"))

    return app


def get_db():
    if "db" not in g:
        g.db = connect_database(current_app.config["DATABASE_CONFIG"])
    return g.db


def authenticated_account(db, user_id):
    return db.execute(
        "SELECT u.id, u.username, u.email, u.account_role, "
        "u.first_name, u.second_name, u.first_surname, u.second_surname, "
        "CASE WHEN u.account_role IN ('owner', 'admin') "
        "THEN COALESCE(s.status, 'inactive') "
        "ELSE COALESCE(p.status, 'inactive') END AS status "
        "FROM users u "
        "LEFT JOIN user_profiles p ON p.user_id = u.id "
        "LEFT JOIN super_users s ON s.user_id = u.id "
        "WHERE u.id = %s",
        (user_id,),
    ).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        from flask import g as flask_g

        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("login", next=request.path))
        flask_g.user = authenticated_account(get_db(), user_id)
        if flask_g.user is None or flask_g.user["status"] != "active":
            session.clear()
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["account_role"] not in {"owner", "admin"}:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def owner_required(view):
    @wraps(view)
    @admin_required
    def wrapped(*args, **kwargs):
        if g.user["account_role"] != "owner":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def normalize_phone_number(value, *, required=False):
    phone = re.sub(r"[\s().-]", "", value or "")
    if not phone and not required:
        return None
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
        raise ValueError(
            "Ingresa un teléfono internacional con + código de país "
            "y entre 8 y 15 dígitos."
        )
    return phone


def create_superuser_account(
    db,
    *,
    username,
    first_name,
    second_name="",
    first_surname,
    second_surname="",
    document_id="",
    primary_phone,
    secondary_phone=None,
    email,
    password,
    role,
    age=None,
    business_id="",
):
    account_role = "owner" if role == "Owner" else "admin"
    created_at = time.time()
    account = db.execute(
        "INSERT INTO users "
        "(username, email, password_hash, created_at, account_role, first_name, "
        "second_name, first_surname, second_surname, document_id, primary_phone, "
        "secondary_phone) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            username,
            email,
            generate_password_hash(password),
            created_at,
            account_role,
            first_name,
            second_name or None,
            first_surname,
            second_surname or None,
            document_id or None,
            primary_phone,
            secondary_phone,
        ),
    ).fetchone()
    user_id = account["id"]
    db.execute(
        "INSERT INTO user_profiles "
        "(user_id, age, registration_date, email) VALUES (%s, %s, %s, %s)",
        (user_id, age, created_at, email),
    )
    db.execute(
        "INSERT INTO super_users "
        "(user_id, business_id, role, status) "
        "VALUES (%s, %s, %s, 'active')",
        (user_id, business_id, role),
    )
    return user_id


def admin_question_selections():
    performance = request.form.get("performance_domain", "")
    official = request.form.get("eco_domain", "")
    methodology = request.form.get("methodology", "")
    return {
        "axis": "",
        "value": performance,
        "category_type": "performance_domain",
        "official": [official] if official else [],
        "methodology": [methodology] if methodology else [],
        "performance": [performance] if performance else [],
    }


def question_form_values(question, selected):
    item = dict(question)
    item["options_text"] = "\n".join(json.loads(item["options_json"]))
    item["question_type"] = item.get("question_type", "multiple_choice")
    item["content"] = json.loads(item.get("content_json", "{}"))
    item["alt_text"] = item["content"].get("alt_text", "")
    item["correct_answer"] = json.loads(item.get("correct_answer_json", "0"))
    item["correct_option"] = (
        item["correct_answer"] + 1 if type(item["correct_answer"]) is int else ""
    )
    item["case_questions"] = item["content"].get("questions", [])
    item["case_question_count"] = max(3, min(5, len(item["case_questions"]) or 3))
    for index in range(5):
        child = item["case_questions"][index] if index < len(item["case_questions"]) else {}
        item[f"case_prompt_{index + 1}"] = child.get("prompt", "")
        item[f"case_options_{index + 1}"] = "\n".join(child.get("options", []))
        item[f"case_correct_{index + 1}"] = (
            child.get("correct_option", 0) + 1 if child else ""
        )
    item["drag_items"] = item["options_text"]
    item["drag_targets"] = "\n".join(item["content"].get("targets", []))
    item["drag_matches"] = "\n".join(
        str(value + 1) for value in item["correct_answer"]
    ) if isinstance(item["correct_answer"], list) else ""
    item["correct_options"] = "\n".join(
        str(value + 1) for value in item["correct_answer"]
    ) if isinstance(item["correct_answer"], list) else ""
    hotspot = item["content"].get("hotspot", {})
    item["hotspot_x"] = hotspot.get("x", "")
    item["hotspot_y"] = hotspot.get("y", "")
    item["hotspot_width"] = hotspot.get("width", "")
    item["hotspot_height"] = hotspot.get("height", "")
    if request.method == "POST":
        item["prompt"] = request.form.get("prompt", item["prompt"])
        item["options_text"] = request.form.get("options", item["options_text"])
        item["question_type"] = request.form.get("question_type", item["question_type"])
        item["case_question_count"] = request.form.get(
            "case_question_count", item["case_question_count"]
        )
        item["correct_option"] = request.form.get(
            "correct_option", item["correct_option"]
        )
        item["correct_options"] = request.form.get("correct_options", item["correct_options"])
        item["drag_items"] = request.form.get("drag_items", item["drag_items"])
        item["drag_targets"] = request.form.get("drag_targets", item["drag_targets"])
        item["drag_matches"] = request.form.get("drag_matches", item["drag_matches"])
        for index in range(5):
            for key in ("prompt", "options", "correct"):
                form_key = f"case_{key}_{index + 1}"
                item[form_key] = request.form.get(form_key, item.get(form_key, ""))
        for key in ("x", "y", "width", "height"):
            item[f"hotspot_{key}"] = request.form.get(
                f"hotspot_{key}", item[f"hotspot_{key}"]
            )
        item["explanation"] = request.form.get("explanation", item["explanation"])
        item["alt_text"] = request.form.get("alt_text", item["alt_text"])
    item["selected"] = selected
    return item


def save_admin_question(db, kind, bank_entry=None, existing_question=None):
    values = request.form
    prompt = values.get("prompt", "").strip()
    explanation = values.get("explanation", "").strip()
    question_type = values.get("question_type", "")
    selected = admin_question_selections()
    errors = []
    if not prompt or not explanation:
        errors.append("La pregunta y su retroalimentación son obligatorias.")
    if question_type not in QUESTION_TYPES:
        errors.append("Selecciona un tipo de pregunta válido.")
    if (
        len(selected["performance"]) != 1
        or selected["performance"][0] not in PERFORMANCE_DOMAINS
        or len(selected["official"]) != 1
        or selected["official"][0] not in ECO_DOMAINS
        or len(selected["methodology"]) != 1
        or selected["methodology"][0] not in METHODOLOGIES
    ):
        errors.append("Selecciona un tag válido en cada uno de los tres ejes.")

    options = []
    content = {}
    correct_answer = 0
    correct_option = 0
    if question_type in {"multiple_choice", "visual_based", "multiple_responses", "pull_down"}:
        options = [line.strip() for line in values.get("options", "").splitlines() if line.strip()]
        if len(options) < 2:
            errors.append("Agrega al menos dos opciones de respuesta, una por línea.")
        if question_type in {"multiple_choice", "visual_based", "multiple_responses"} and len(options) != 4:
            errors.append("Este tipo de pregunta requiere exactamente cuatro opciones.")
        if question_type == "multiple_responses":
            correct_answer = parse_option_indexes(values.get("correct_options", ""), len(options), errors)
            if not 2 <= len(correct_answer) <= 3:
                errors.append("Multiple Responses requiere dos o tres respuestas correctas.")
        elif question_type == "pull_down":
            blank_count = prompt.count("[[blank]]")
            correct_answer = parse_option_indexes(
                values.get("correct_options", ""),
                len(options),
                errors,
                allow_duplicates=True,
            )
            if blank_count < 1 or blank_count != len(correct_answer):
                errors.append("Usa un marcador [[blank]] por cada respuesta correcta del párrafo.")
            content["prompt_parts"] = prompt.split("[[blank]]")
        else:
            try:
                correct_option = int(values.get("correct_option", "")) - 1
            except ValueError:
                correct_option = -1
            if not 0 <= correct_option < len(options):
                errors.append("El número de respuesta correcta no coincide con las opciones.")
            correct_answer = correct_option
    elif question_type == "case_set":
        children = []
        for index in range(1, 6):
            child_prompt = values.get(f"case_prompt_{index}", "").strip()
            child_options = [
                line.strip()
                for line in values.get(f"case_options_{index}", "").splitlines()
                if line.strip()
            ]
            child_correct_raw = values.get(f"case_correct_{index}", "").strip()
            if not child_prompt and not child_options and not child_correct_raw:
                continue
            if not child_prompt or len(child_options) != 4:
                errors.append(f"Completa la pregunta {index} del caso con cuatro opciones.")
                continue
            try:
                child_correct = int(child_correct_raw) - 1
            except ValueError:
                child_correct = -1
            if not 0 <= child_correct < len(child_options):
                errors.append(f"Indica una respuesta correcta válida para la pregunta {index}.")
                continue
            children.append(
                {
                    "prompt": child_prompt,
                    "options": child_options,
                    "correct_option": child_correct,
                }
            )
        if not 3 <= len(children) <= 5:
            errors.append("Un Case/Scenario Set debe incluir entre 3 y 5 preguntas completas.")
        content["questions"] = children
        correct_answer = [child["correct_option"] for child in children]
    elif question_type == "drag_drop":
        options = [line.strip() for line in values.get("drag_items", "").splitlines() if line.strip()]
        targets = [line.strip() for line in values.get("drag_targets", "").splitlines() if line.strip()]
        correct_answer = parse_option_indexes(values.get("drag_matches", ""), len(targets), errors)
        if (
            len(options) < 2
            or len(targets) < 2
            or len(options) != len(targets)
            or len(options) != len(correct_answer)
            or set(correct_answer) != set(range(len(targets)))
        ):
            errors.append(
                "Agrega igual número de conceptos y destinos, con una correspondencia "
                "única y válida para cada elemento."
            )
        content["targets"] = targets
    elif question_type == "hotspot":
        uploaded = request.files.get("image")
        existing_content = {}
        if existing_question is not None:
            existing_content = json.loads(existing_question.get("content_json") or "{}")
        image_path = existing_content.get("image_path")
        if uploaded and uploaded.filename:
            extension = os.path.splitext(uploaded.filename)[1].lower()
            if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
                errors.append("La imagen del hotspot debe ser PNG, JPG o WebP.")
            elif not supported_image_upload(uploaded, extension):
                errors.append("El archivo no coincide con el formato de imagen indicado.")
            else:
                image_path = f"uploads/questions/{secrets.token_hex(16)}{extension}"
                content["uploaded_image"] = uploaded
        if not image_path and "uploaded_image" not in content:
            errors.append("Sube una imagen para la pregunta Hotspot.")
        try:
            hotspot = {
                key: float(values.get(f"hotspot_{key}", ""))
                for key in ("x", "y", "width", "height")
            }
            if (
                hotspot["x"] < 0 or hotspot["y"] < 0
                or hotspot["width"] <= 0 or hotspot["height"] <= 0
                or hotspot["x"] + hotspot["width"] > 100
                or hotspot["y"] + hotspot["height"] > 100
            ):
                raise ValueError
        except ValueError:
            errors.append("Define una zona Hotspot válida en porcentajes (0 a 100).")
            hotspot = {}
        try:
            image_width = int(values.get("hotspot_image_width", ""))
            image_height = int(values.get("hotspot_image_height", ""))
            if image_width <= 0 or image_height <= 0:
                raise ValueError
        except ValueError:
            errors.append("No se pudieron determinar las dimensiones de la imagen Hotspot.")
            image_width = image_height = 0
        content["image_path"] = image_path
        content["hotspot"] = hotspot
        content["image_dimensions"] = {
            "width": image_width,
            "height": image_height,
        }
        content["hotspot_pixels"] = {
            "x_min": hotspot.get("x", 0) * image_width / 100,
            "y_min": hotspot.get("y", 0) * image_height / 100,
            "x_max": (hotspot.get("x", 0) + hotspot.get("width", 0)) * image_width / 100,
            "y_max": (hotspot.get("y", 0) + hotspot.get("height", 0)) * image_height / 100,
        }
        correct_answer = hotspot

    if question_type == "visual_based":
        existing_content = (
            json.loads(existing_question.get("content_json") or "{}")
            if existing_question is not None
            else {}
        )
        image_path = existing_content.get("image_path")
        uploaded = request.files.get("image")
        if uploaded and uploaded.filename:
            extension = os.path.splitext(uploaded.filename)[1].lower()
            if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
                errors.append("La imagen del reactivo debe ser PNG, JPG o WebP.")
            elif not supported_image_upload(uploaded, extension):
                errors.append("El archivo no coincide con el formato de imagen indicado.")
            else:
                image_path = f"uploads/questions/{secrets.token_hex(16)}{extension}"
                content["uploaded_image"] = uploaded
        if not image_path and "uploaded_image" not in content:
            errors.append("Sube una imagen para la pregunta Visual-Based.")
        content["image_path"] = image_path

    if errors:
        return errors

    if question_type in {"visual_based", "hotspot"}:
        content["alt_text"] = values.get("alt_text", "").strip() or prompt
    uploaded = content.pop("uploaded_image", None)
    if uploaded:
        image_directory = os.path.join(current_app.static_folder, "uploads", "questions")
        os.makedirs(image_directory, exist_ok=True)
        uploaded.save(os.path.join(current_app.static_folder, content["image_path"]))
    interaction_config = question_interaction_config(
        question_type, prompt, options, correct_answer, content
    )
    option_feedback = [explanation for _ in options]
    answer = json.dumps(correct_answer, ensure_ascii=False)
    active = int(
        kind == "manual"
        and (
            bank_entry is None
            or bank_entry["status"] is True
            or bank_entry["status"] == "active"
        )
    )
    source_type = "general" if kind == "manual" else "automated"
    if existing_question is None:
        question = db.execute(
            "INSERT INTO questions "
            "(prompt, options_json, option_feedback_json, correct_option, "
            "question_type, content_json, correct_answer_json, explanation, "
            "source_type, active, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                prompt,
                json.dumps(options, ensure_ascii=False),
                json.dumps(option_feedback, ensure_ascii=False),
                correct_option,
                question_type,
                json.dumps(content, ensure_ascii=False),
                json.dumps(correct_answer, ensure_ascii=False),
                explanation,
                source_type,
                active,
                time.time(),
            ),
        ).fetchone()
        question_id = question["id"]
        db.execute(
            "INSERT INTO question_performance_domains VALUES (%s, %s)",
            (question_id, selected["performance"][0]),
        )
        db.execute(
            "INSERT INTO question_methodologies VALUES (%s, %s)",
            (question_id, selected["methodology"][0]),
        )
        db.execute(
            "INSERT INTO question_eco_domains VALUES (%s, %s)",
            (question_id, selected["official"][0]),
        )
        table = f"{kind}_question_bank"
        if kind == "automated":
            db.execute(
                f"INSERT INTO {table} "
                "(question_id, question, official_domains_json, "
                "methodological_approaches_json, performance_domains_json, answer, "
                "feedback, added_at, status, interaction_config) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    question_id,
                    prompt,
                    json.dumps(selected["official"]),
                    json.dumps(selected["methodology"]),
                    json.dumps(selected["performance"]),
                    answer,
                    explanation,
                    time.time(),
                    "inactive",
                    json.dumps(interaction_config, ensure_ascii=False),
                ),
            )
            db.execute(
                "UPDATE automated_question_bank SET validation_status = 'pending' "
                "WHERE question_id = %s",
                (question_id,),
            )
        else:
            db.execute(
                "INSERT INTO manual_question_bank "
                "(question_id, stem, options, correct_answer, category_type, "
                "category_value, interaction_config, feedback, added_at, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, TRUE)",
                (
                    question_id,
                    prompt,
                    json.dumps(
                        {chr(65 + index): option for index, option in enumerate(options)},
                        ensure_ascii=False,
                    ),
                    format_bank_answer(question_type, correct_answer),
                    selected["category_type"],
                    selected["value"],
                    json.dumps(interaction_config, ensure_ascii=False),
                    explanation,
                    time.time(),
                ),
            )
        return []

    question_id = existing_question["id"]
    db.execute(
        "UPDATE questions SET prompt = %s, options_json = %s, "
        "option_feedback_json = %s, correct_option = %s, question_type = %s, "
        "content_json = %s, correct_answer_json = %s, explanation = %s, "
        "active = %s WHERE id = %s",
        (
            prompt,
            json.dumps(options, ensure_ascii=False),
            json.dumps(option_feedback, ensure_ascii=False),
            correct_option,
            question_type,
            json.dumps(content, ensure_ascii=False),
            json.dumps(correct_answer, ensure_ascii=False),
            explanation,
            active,
            question_id,
        ),
    )
    db.execute("DELETE FROM question_performance_domains WHERE question_id = %s", (question_id,))
    db.execute("DELETE FROM question_methodologies WHERE question_id = %s", (question_id,))
    db.execute("DELETE FROM question_eco_domains WHERE question_id = %s", (question_id,))
    db.execute(
        "INSERT INTO question_performance_domains VALUES (%s, %s)",
        (question_id, selected["performance"][0]),
    )
    db.execute(
        "INSERT INTO question_methodologies VALUES (%s, %s)",
        (question_id, selected["methodology"][0]),
    )
    db.execute(
        "INSERT INTO question_eco_domains VALUES (%s, %s)",
        (question_id, selected["official"][0]),
    )
    table = f"{kind}_question_bank"
    if kind == "manual":
        db.execute(
            f"UPDATE {table} SET stem = %s, options = %s::jsonb, "
            "correct_answer = %s, category_type = %s, category_value = %s, "
            "interaction_config = %s::jsonb, feedback = %s WHERE id = %s",
            (
                prompt,
                json.dumps(
                    {chr(65 + index): option for index, option in enumerate(options)},
                    ensure_ascii=False,
                ),
                format_bank_answer(question_type, correct_answer),
                selected["category_type"],
                selected["value"],
                json.dumps(interaction_config, ensure_ascii=False),
                explanation,
                bank_entry["id"],
            ),
        )
    else:
        db.execute(
            f"UPDATE {table} SET question = %s, official_domains_json = %s, "
            "methodological_approaches_json = %s, performance_domains_json = %s, "
            "answer = %s, feedback = %s, status = 'inactive', interaction_config = %s::jsonb, "
            "validation_status = 'pending' WHERE id = %s",
            (
                prompt,
                json.dumps(selected["official"]),
                json.dumps(selected["methodology"]),
                json.dumps(selected["performance"]),
                answer,
                explanation,
                json.dumps(interaction_config, ensure_ascii=False),
                bank_entry["id"],
            ),
        )
    return []


def format_bank_answer(question_type, correct_answer):
    if question_type in {"multiple_choice", "visual_based"}:
        return chr(65 + correct_answer) if isinstance(correct_answer, int) else ""
    if question_type == "multiple_responses":
        return ",".join(chr(65 + index) for index in correct_answer)
    return {
        "case_set": "CASE_SET",
        "drag_drop": "DRAG_DROP",
        "hotspot": "HOTSPOT",
        "pull_down": "PULL_DOWN",
    }.get(question_type, "")


def question_interaction_config(question_type, prompt, options, correct_answer, content):
    def option_map(values):
        return {
            chr(65 + index): value
            for index, value in enumerate(values)
        }

    if question_type == "case_set":
        return {
            "case_text": prompt,
            "sub_questions": [
                {
                    "sub_id": index,
                    "stem": child["prompt"],
                    "options": option_map(child["options"]),
                    "correct_answer": chr(65 + child["correct_option"]),
                }
                for index, child in enumerate(content["questions"], start=1)
            ],
        }
    if question_type in {"visual_based", "hotspot"}:
        hotspot = content.get("hotspot_pixels", content.get("hotspot", {}))
        target_area = (
            {
                "x_min": hotspot.get("x_min", hotspot.get("x", 0)),
                "y_min": hotspot.get("y_min", hotspot.get("y", 0)),
                "x_max": hotspot.get(
                    "x_max", hotspot.get("x", 0) + hotspot.get("width", 0)
                ),
                "y_max": hotspot.get(
                    "y_max", hotspot.get("y", 0) + hotspot.get("height", 0)
                ),
            }
            if question_type == "hotspot"
            else {}
        )
        return {
            "media_url": f"/static/{content.get('image_path', '')}",
            "alt_text": content.get("alt_text", prompt),
            "interaction_mode": "hotspot" if question_type == "hotspot" else "multiple_choice",
            "target_area": target_area,
            "options": option_map(options) if question_type == "visual_based" else {},
        }
    if question_type in {"multiple_choice", "multiple_responses"}:
        answers = (
            [correct_answer]
            if isinstance(correct_answer, int)
            else correct_answer
        )
        return {
            "max_Selections": 1 if question_type == "multiple_choice" else len(answers),
            "options": option_map(options),
            "correct_answers": [chr(65 + index) for index in answers],
        }
    if question_type == "drag_drop":
        source_items = [
            {"id": f"s{index}", "text": item}
            for index, item in enumerate(options, start=1)
        ]
        return {
            "source_items": source_items,
            "target_boxes": [
                {
                    "id": f"t{index}",
                    "label": target,
                    "accepts": next(
                        (
                            source_items[source_index]["id"]
                            for source_index, matched_target in enumerate(correct_answer)
                            if matched_target == index - 1
                        ),
                        "",
                    ),
                }
                for index, target in enumerate(content["targets"], start=1)
            ],
        }
    if question_type == "pull_down":
        dropdowns = {}
        blanks = re.split(r"\[\[blank\]\]", prompt)
        template_parts = [blanks[0]]
        for index, correct_index in enumerate(correct_answer, start=1):
            blank_key = f"BLANK_{index}"
            template_parts.extend([f"[{blank_key}]", blanks[index]])
            dropdowns[blank_key] = {
                "options": options,
                "correct": options[correct_index],
            }
        return {
            "template_text": "".join(template_parts),
            "dropdowns": dropdowns,
        }
    return {}


def parse_option_indexes(raw_value, option_count, errors, *, allow_duplicates=False):
    indexes = []
    try:
        indexes = [int(value.strip()) - 1 for value in raw_value.splitlines() if value.strip()]
    except ValueError:
        errors.append("Las respuestas correctas deben ser números, uno por línea.")
        return []
    if not indexes or any(index < 0 or index >= option_count for index in indexes):
        errors.append("Los números de respuesta deben corresponder con las opciones.")
        return []
    if not allow_duplicates and len(indexes) != len(set(indexes)):
        errors.append("No repitas números de respuesta correcta.")
        return []
    return indexes


def supported_image_upload(uploaded, extension):
    uploaded.stream.seek(0)
    signature = uploaded.stream.read(12)
    uploaded.stream.seek(0)
    if extension == ".png":
        return signature.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return signature.startswith(b"\xff\xd8\xff")
    return (
        extension == ".webp"
        and signature.startswith(b"RIFF")
        and signature[8:12] == b"WEBP"
    )


def parse_exam_response(question):
    question_type = question["question_type"]
    options = json.loads(question["options_json"])
    content = json.loads(question.get("content_json") or "{}")

    def selected_indexes(field, count, option_count):
        raw = request.form.getlist(field)
        try:
            indexes = [int(value) for value in raw]
        except ValueError as error:
            raise ValueError("Selecciona respuestas válidas.") from error
        if len(indexes) != count or any(index < 0 or index >= option_count for index in indexes):
            raise ValueError("Completa todas las partes de la respuesta.")
        return indexes

    if question_type in {"multiple_choice", "visual_based"}:
        selected = request.form.get("selected_option", type=int)
        if selected is None or not 0 <= selected < len(options):
            raise ValueError("Selecciona una respuesta válida.")
        return selected
    if question_type == "multiple_responses":
        selected = selected_indexes("selected_options", len(request.form.getlist("selected_options")), len(options))
        correct_count = len(json.loads(question["correct_answer_json"]))
        if len(selected) != correct_count or len(selected) != len(set(selected)):
            raise ValueError(f"Selecciona exactamente {correct_count} opciones distintas.")
        return selected
    if question_type == "case_set":
        children = content["questions"]
        raw = request.form.getlist("case_answers")
        if len(raw) != len(children):
            raise ValueError("Selecciona una respuesta para cada pregunta del caso.")
        try:
            indexes = [int(value) for value in raw]
        except ValueError as error:
            raise ValueError("Selecciona respuestas válidas para el caso.") from error
        if any(
            index < 0 or index >= len(child["options"])
            for index, child in zip(indexes, children)
        ):
            raise ValueError("Selecciona respuestas válidas para el caso.")
        return indexes
    if question_type == "drag_drop":
        targets = content["targets"]
        return selected_indexes("drag_match", len(options), len(targets))
    if question_type == "pull_down":
        blank_count = len(content["prompt_parts"]) - 1
        return selected_indexes("blank_answers", blank_count, len(options))
    if question_type == "hotspot":
        try:
            x, y = (float(value) for value in request.form.get("hotspot_point", "").split(",", 1))
        except ValueError as error:
            raise ValueError("Selecciona una zona de la imagen.") from error
        if not 0 <= x <= 100 or not 0 <= y <= 100:
            raise ValueError("La zona seleccionada no es válida.")
        return {"x": x, "y": y}
    raise ValueError("El tipo de pregunta no es válido.")


def prepare_exam_question(question):
    question["options"] = json.loads(question.pop("options_json"))
    question["content"] = json.loads(question.pop("content_json") or "{}")
    correct_answer = json.loads(question.pop("correct_answer_json") or "0")
    question["selection_limit"] = (
        len(correct_answer)
        if question["question_type"] == "multiple_responses"
        else None
    )
    question["response"] = (
        json.loads(question["response_json"])
        if question["response_json"] is not None
        else question["selected_option"]
    )
    question["answered"] = question["response"] is not None


def exam_response_is_correct(row):
    response_raw = row.get("response_json")
    response = (
        json.loads(response_raw)
        if response_raw is not None
        else row.get("selected_option")
    )
    if response is None:
        return False
    correct_answer_raw = row.get("correct_answer_json")
    correct = (
        json.loads(correct_answer_raw)
        if correct_answer_raw is not None
        else row.get("correct_answer", row.get("correct_option"))
    )
    question_type = row["question_type"]
    if question_type == "hotspot":
        hotspot = correct
        return (
            hotspot["x"] <= response["x"] <= hotspot["x"] + hotspot["width"]
            and hotspot["y"] <= response["y"] <= hotspot["y"] + hotspot["height"]
        )
    if question_type == "multiple_responses":
        return sorted(response) == sorted(correct)
    return response == correct


def format_exam_answer(question_type, options, content, response, correct=False):
    if response is None:
        return ""
    if question_type in {"multiple_choice", "visual_based"}:
        return options[response]
    if question_type == "multiple_responses":
        return ", ".join(options[index] for index in response)
    if question_type == "case_set":
        return " | ".join(
            f"{child['prompt']}: {child['options'][index]}"
            for child, index in zip(content["questions"], response)
        )
    if question_type == "drag_drop":
        return " | ".join(
            f"{item} -> {content['targets'][target]}"
            for item, target in zip(options, response)
        )
    if question_type == "pull_down":
        return " / ".join(options[index] for index in response)
    if question_type == "hotspot":
        if correct:
            hotspot = response
            return (
                f"Zona correcta: X {hotspot['x']}%, Y {hotspot['y']}%, "
                f"ancho {hotspot['width']}%, alto {hotspot['height']}%."
            )
        return f"Punto seleccionado: X {response['x']}%, Y {response['y']}%."
    return str(response)


def init_db():
    db = get_db()
    schema_path = os.path.join(
        os.path.dirname(__file__),
        "schema_postgres.sql",
    )
    with open(schema_path, encoding="utf-8") as schema_file:
        schema_sql = schema_file.read()

    db.execute(schema_sql)

    db.executemany(
        "INSERT INTO performance_domains (id, label) VALUES (%s, %s) "
        "ON CONFLICT (id) DO NOTHING",
        list(PERFORMANCE_DOMAINS.items()),
    )
    db.executemany(
        "INSERT INTO methodologies (id, label) VALUES (%s, %s) "
        "ON CONFLICT (id) DO NOTHING",
        list(METHODOLOGIES.items()),
    )
    db.executemany(
        "INSERT INTO eco_domains (id, label) VALUES (%s, %s) "
        "ON CONFLICT (id) DO NOTHING",
        list(ECO_DOMAINS.items()),
    )
    db.executemany(
        "INSERT INTO valid_combinations (methodology_id, eco_domain_id) "
        "VALUES (%s, %s) ON CONFLICT (methodology_id, eco_domain_id) DO NOTHING",
        VALID_COMBINATIONS,
    )
    db.execute(
        "WITH legacy_methods AS ("
        "DELETE FROM question_methodologies "
        "WHERE methodology_id IN ('agile', 'hybrid') RETURNING question_id"
        ") INSERT INTO question_methodologies (question_id, methodology_id) "
        "SELECT DISTINCT question_id, 'agile_hybrid' FROM legacy_methods "
        "ON CONFLICT DO NOTHING"
    )
    db.execute(
        "UPDATE manual_question_bank SET category_value = 'agile_hybrid' "
        "WHERE category_value IN ('agile', 'hybrid')"
    )
    db.execute(
        "UPDATE automated_question_bank "
        "SET methodological_approaches_json = '[\"agile_hybrid\"]' "
        "WHERE methodological_approaches_json IN ('[\"agile\"]', '[\"hybrid\"]')"
    )
    db.execute(
        "UPDATE exams SET filters_json = jsonb_set("
        "filters_json::jsonb, '{values}', '[\"agile_hybrid\"]'::jsonb)::text "
        "WHERE filters_json::jsonb ->> 'axis' = 'methodology' "
        "AND (filters_json::jsonb -> 'values') ?| ARRAY['agile', 'hybrid']"
    )
    db.execute(
        "INSERT INTO user_profiles (user_id, email, registration_date) "
        "SELECT id, email, created_at FROM users "
        "ON CONFLICT (user_id) DO NOTHING"
    )
    db.commit()


def validate_filters(db, selected):
    axis = selected.get("axis")
    categories = list(dict.fromkeys(selected.get("values", [])))
    taxonomies = {
        "performance": PERFORMANCE_DOMAINS,
        "methodology": METHODOLOGIES,
        "eco": ECO_DOMAINS,
    }
    if axis not in taxonomies:
        raise ValueError("Selecciona un eje de filtrado válido.")
    if not categories:
        raise ValueError("Selecciona al menos una categoría del eje elegido.")
    if not set(categories).issubset(taxonomies[axis]):
        raise ValueError("Hay una categoría no válida para el eje seleccionado.")
    if axis != "performance" and len(categories) != 1:
        raise ValueError("Esta ruta permite seleccionar una sola categoría.")
    return {"axis": axis, "values": categories}


def matching_questions(db, filters, limit):
    axis_tables = {
        "performance": ("question_performance_domains", "domain_id"),
        "methodology": ("question_methodologies", "methodology_id"),
        "eco": ("question_eco_domains", "eco_domain_id"),
    }
    table, column = axis_tables[filters["axis"]]
    placeholders = ",".join("%s" for _ in filters["values"])
    query = (
        "SELECT q.id FROM questions q "
        "WHERE q.active = 1 "
        "AND EXISTS (SELECT 1 FROM question_performance_domains performance "
        "WHERE performance.question_id = q.id) "
        "AND EXISTS (SELECT 1 FROM question_eco_domains eco "
        "WHERE eco.question_id = q.id) "
        "AND EXISTS (SELECT 1 FROM question_methodologies methodology "
        "WHERE methodology.question_id = q.id) "
        f"AND EXISTS (SELECT 1 FROM {table} category "
        f"WHERE category.question_id = q.id AND category.{column} IN ({placeholders})) "
        "ORDER BY RANDOM() LIMIT %s"
    )
    params = (*filters["values"], limit)
    return db.execute(query, params).fetchall()


def count_matching_questions(db, filters):
    axis_tables = {
        "performance": ("question_performance_domains", "domain_id"),
        "methodology": ("question_methodologies", "methodology_id"),
        "eco": ("question_eco_domains", "eco_domain_id"),
    }
    table, column = axis_tables[filters["axis"]]
    placeholders = ",".join("%s" for _ in filters["values"])
    query = (
        "SELECT COUNT(*) AS count FROM questions q "
        "WHERE q.active = 1 "
        "AND EXISTS (SELECT 1 FROM question_performance_domains performance "
        "WHERE performance.question_id = q.id) "
        "AND EXISTS (SELECT 1 FROM question_eco_domains eco "
        "WHERE eco.question_id = q.id) "
        "AND EXISTS (SELECT 1 FROM question_methodologies methodology "
        "WHERE methodology.question_id = q.id) "
        f"AND EXISTS (SELECT 1 FROM {table} category "
        f"WHERE category.question_id = q.id AND category.{column} IN ({placeholders}))"
    )
    return db.execute(query, filters["values"]).fetchone()["count"]


def official_cell_filter(eco_domain, methodology):
    method_values = (
        ("agile_hybrid", "agile", "hybrid")
        if methodology == "agile_hybrid"
        else (methodology,)
    )
    method_placeholders = ",".join("%s" for _ in method_values)
    return (
        "q.active = 1 "
        "AND EXISTS (SELECT 1 FROM question_performance_domains performance "
        "WHERE performance.question_id = q.id) "
        "AND EXISTS (SELECT 1 FROM question_eco_domains eco "
        "WHERE eco.question_id = q.id AND eco.eco_domain_id = %s) "
        "AND EXISTS (SELECT 1 FROM question_methodologies methodology "
        "WHERE methodology.question_id = q.id "
        f"AND methodology.methodology_id IN ({method_placeholders}))",
        (eco_domain, *method_values),
    )


def official_cell_query(db, eco_domain, methodology, limit=None):
    conditions, params = official_cell_filter(eco_domain, methodology)
    limit_sql = " LIMIT %s" if limit is not None else ""
    query = (
        "SELECT q.id FROM questions q "
        f"WHERE {conditions} "
        f"ORDER BY RANDOM(){limit_sql}"
    )
    if limit is not None:
        params += (limit,)
    return db.execute(query, params).fetchall()


def official_cell_count(db, eco_domain, methodology):
    conditions, params = official_cell_filter(eco_domain, methodology)
    query = (
        "SELECT COUNT(*) AS count FROM questions q "
        f"WHERE {conditions}"
    )
    return db.execute(query, params).fetchone()["count"]


def count_official_question_availability(db, requested_count):
    distribution = OFFICIAL_EXAM_DISTRIBUTIONS[requested_count]
    available = 0
    shortages = []
    for (eco_domain, methodology), required in distribution.items():
        count = official_cell_count(db, eco_domain, methodology)
        available += count
        if count < required:
            shortages.append(
                {
                    "eco_domain": eco_domain,
                    "methodology": methodology,
                    "available": count,
                    "required": required,
                }
            )
    return available, shortages


def official_exam_questions(db, requested_count):
    distribution = OFFICIAL_EXAM_DISTRIBUTIONS[requested_count]
    questions = []
    shortages = []
    for (eco_domain, methodology), required in distribution.items():
        cell_questions = official_cell_query(db, eco_domain, methodology, required)
        if len(cell_questions) < required:
            shortages.append(
                {
                    "eco_domain": eco_domain,
                    "methodology": methodology,
                    "available": len(cell_questions),
                    "required": required,
                }
            )
        questions.extend(cell_questions)
    random.shuffle(questions)
    return questions, shortages


def practice_consistency(db, user_id):
    axis_tables = {
        "performance": (
            "question_performance_domains",
            "domain_id",
            PERFORMANCE_DOMAINS,
            "Ruta 1 · Dominios de desempeño",
        ),
        "eco": (
            "question_eco_domains",
            "eco_domain_id",
            ECO_DOMAINS,
            "Ruta 2 · Dominios ECO",
        ),
        "methodology": (
            "question_methodologies",
            "methodology_id",
            METHODOLOGIES,
            "Ruta 3 · Enfoques de desarrollo",
        ),
    }
    result = []
    for axis, (table, column, categories, route_label) in axis_tables.items():
        rows = db.execute(
            f"SELECT e.id AS exam_id, e.filters_json, e.ended_at, "
            f"q.question_type, q.correct_answer_json, q.correct_option, "
            f"a.response_json, a.selected_option, category.{column} AS category_id "
            f"FROM exams e JOIN exam_questions eq ON eq.exam_id = e.id "
            f"JOIN questions q ON q.id = eq.question_id "
            f"JOIN {table} category ON category.question_id = q.id "
            f"LEFT JOIN exam_answers a ON a.exam_id = e.id "
            f"AND a.question_id = q.id "
            f"WHERE e.user_id = %s AND e.status IN ('completed', 'expired') "
            f"AND e.requested_count IN (15, 30) "
            f"AND e.filters_json::jsonb ->> 'axis' = %s "
            f"ORDER BY e.ended_at DESC, e.id DESC",
            (user_id, axis),
        ).fetchall()
        attempts = {}
        exam_totals = {}
        for row in rows:
            filters = json.loads(row["filters_json"])
            category_id = row["category_id"]
            if (
                filters.get("mode", "practice") != "practice"
                or filters.get("axis") != axis
                or category_id not in filters.get("values", [])
            ):
                continue
            key = (row["exam_id"], category_id)
            total = exam_totals.setdefault(
                key, {"ended_at": row["ended_at"], "correct": 0, "total": 0}
            )
            total["total"] += 1
            if exam_response_is_correct(row):
                total["correct"] += 1
        for (exam_id, category_id), score in exam_totals.items():
            attempts.setdefault(category_id, []).append((score["ended_at"], exam_id, score))
        for category_id, label in categories.items():
            category_attempts = sorted(
                attempts.get(category_id, []),
                key=lambda item: (item[0] or 0, item[1]),
                reverse=True,
            )[:3]
            result.append(
                {
                    "route": route_label,
                    "label": label,
                    "attempt_count": len(category_attempts),
                    "consistency": (
                        round(
                            sum(
                                attempt["correct"] / attempt["total"] * 100
                                for _, _, attempt in category_attempts
                            )
                            / len(category_attempts),
                            1,
                        )
                        if category_attempts
                        else None
                    ),
                }
            )
    return result


def owned_exam(db, exam_id):
    exam = db.execute(
        "SELECT * FROM exams WHERE id = %s AND user_id = %s",
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
        "UPDATE exams SET status = %s, ended_at = %s WHERE id = %s AND status = 'active'",
        ("expired" if expired else "completed", now, exam["id"]),
    )
    if updated.rowcount != 1:
        db.commit()
        return
    db.execute(
        "INSERT INTO user_question_states "
        "(user_id, question_id, pending, seen_count, updated_at) "
        "SELECT %s, eq.question_id, "
        "CASE WHEN eq.marked_saved = 1 OR a.selected_option IS NULL THEN 1 ELSE 0 END, "
        "1, %s FROM exam_questions eq LEFT JOIN exam_answers a "
        "ON a.exam_id = eq.exam_id AND a.question_id = eq.question_id "
        "WHERE eq.exam_id = %s "
        "ON CONFLICT(user_id, question_id) DO UPDATE SET "
        "pending = CASE WHEN excluded.pending = 1 THEN 1 ELSE 0 END, "
        "seen_count = user_question_states.seen_count + 1, updated_at = excluded.updated_at",
        (exam["user_id"], now, exam["id"]),
    )
    db.commit()


def g_user_id():
    return session.get("user_id")


app = create_app(initialize_database=False)


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
