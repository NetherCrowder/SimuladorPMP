import json
import math
import os
import re
import secrets
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
from conexion import connect_database, database_config_from_env
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
        DATABASE_CONFIG=database_config_from_env(),
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

    @app.cli.command("create-owner")
    def create_owner_command():
        """Crea la cuenta inicial Owner mediante preguntas interactivas."""
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
                "SELECT q.id, q.prompt, q.options_json, eq.position, eq.marked_saved, "
                "a.selected_option "
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
            "WHERE eq.exam_id = %s AND q.id = %s",
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
                "VALUES (%s, %s, %s, %s) ON CONFLICT(exam_id, question_id) DO UPDATE SET "
                "selected_option = excluded.selected_option, answered_at = excluded.answered_at",
                (exam_id, question_id, selected_option, time.time()),
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
            "AND a.question_id = eq.question_id WHERE eq.exam_id = %s",
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
            "AND a.question_id = eq.question_id WHERE eq.exam_id = %s "
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
            "SELECT b.id, b.question_id, b.question, b.added_at, b.status, "
            "NULL AS validation_status, q.active "
            "FROM manual_question_bank b LEFT JOIN questions q ON q.id = b.question_id"
        ).fetchall()
        automated = db.execute(
            "SELECT b.id, b.question_id, b.question, b.added_at, b.status, "
            "b.validation_status, q.active "
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
            selected={"official": [], "methodology": [], "performance": []},
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
        selected = {
            "official": json.loads(bank_entry["official_domains_json"]),
            "methodology": json.loads(bank_entry["methodological_approaches_json"]),
            "performance": json.loads(bank_entry["performance_domains_json"]),
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
                (status, bank_id),
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
    return {
        "official": request.form.getlist("official_domains"),
        "methodology": request.form.getlist("methodological_approaches"),
        "performance": request.form.getlist("performance_domains"),
    }


def question_form_values(question, selected):
    item = dict(question)
    options = json.loads(item["options_json"])
    item["options_text"] = "\n".join(options)
    item["correct_option_form"] = item["correct_option"] + 1
    if request.method == "POST":
        item["prompt"] = request.form.get("prompt", item["prompt"])
        item["options_text"] = request.form.get("options", item["options_text"])
        item["correct_option_form"] = request.form.get(
            "correct_option", item["correct_option_form"]
        )
        item["explanation"] = request.form.get("explanation", item["explanation"])
    item["selected"] = selected
    return item


def save_admin_question(db, kind, bank_entry=None, existing_question=None):
    values = request.form
    prompt = values.get("prompt", "").strip()
    options = [option.strip() for option in values.get("options", "").splitlines()]
    options = [option for option in options if option]
    explanation = values.get("explanation", "").strip()
    try:
        correct_option = int(values.get("correct_option", "")) - 1
    except ValueError:
        correct_option = -1
    selected = admin_question_selections()
    errors = []
    if not prompt or not explanation:
        errors.append("La pregunta y su retroalimentación son obligatorias.")
    if len(options) < 2:
        errors.append("Agrega al menos dos opciones de respuesta, una por línea.")
    if not 0 <= correct_option < len(options):
        errors.append("El número de respuesta correcta no coincide con las opciones.")
    try:
        validate_filters(
            db,
            {
                "performance": selected["performance"],
                "methodology": selected["methodology"],
                "eco": selected["official"],
            },
        )
    except ValueError as error:
        errors.append(str(error))
    if errors:
        return errors

    option_feedback = [
        explanation if index == correct_option else "Consulta la retroalimentación de la pregunta."
        for index in range(len(options))
    ]
    answer = options[correct_option]
    active = int(
        kind == "manual"
        and (bank_entry is None or bank_entry["status"] == "active")
    )
    source_type = "general" if kind == "manual" else "automated"
    if existing_question is None:
        question = db.execute(
            "INSERT INTO questions "
            "(prompt, options_json, option_feedback_json, correct_option, explanation, "
            "source_type, active, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                prompt,
                json.dumps(options, ensure_ascii=False),
                json.dumps(option_feedback, ensure_ascii=False),
                correct_option,
                explanation,
                source_type,
                active,
                time.time(),
            ),
        ).fetchone()
        question_id = question["id"]
        for domain in selected["performance"]:
            db.execute(
                "INSERT INTO question_performance_domains VALUES (%s, %s)",
                (question_id, domain),
            )
        for methodology in selected["methodology"]:
            db.execute(
                "INSERT INTO question_methodologies VALUES (%s, %s)",
                (question_id, methodology),
            )
        for eco_domain in selected["official"]:
            db.execute(
                "INSERT INTO question_eco_domains VALUES (%s, %s)",
                (question_id, eco_domain),
            )
        table = f"{kind}_question_bank"
        db.execute(
            f"INSERT INTO {table} "
            "(question_id, question, official_domains_json, "
            "methodological_approaches_json, performance_domains_json, answer, "
            "feedback, added_at, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                question_id,
                prompt,
                json.dumps(selected["official"]),
                json.dumps(selected["methodology"]),
                json.dumps(selected["performance"]),
                answer,
                explanation,
                time.time(),
                "active" if kind == "manual" else "inactive",
            ),
        )
        if kind == "automated":
            db.execute(
                "UPDATE automated_question_bank SET validation_status = 'pending' "
                "WHERE question_id = %s",
                (question_id,),
            )
        return []

    question_id = existing_question["id"]
    db.execute(
        "UPDATE questions SET prompt = %s, options_json = %s, "
        "option_feedback_json = %s, correct_option = %s, explanation = %s, "
        "active = %s WHERE id = %s",
        (
            prompt,
            json.dumps(options, ensure_ascii=False),
            json.dumps(option_feedback, ensure_ascii=False),
            correct_option,
            explanation,
            active,
            question_id,
        ),
    )
    db.execute("DELETE FROM question_performance_domains WHERE question_id = %s", (question_id,))
    db.execute("DELETE FROM question_methodologies WHERE question_id = %s", (question_id,))
    db.execute("DELETE FROM question_eco_domains WHERE question_id = %s", (question_id,))
    for domain in selected["performance"]:
        db.execute(
            "INSERT INTO question_performance_domains VALUES (%s, %s)",
            (question_id, domain),
        )
    for methodology in selected["methodology"]:
        db.execute(
            "INSERT INTO question_methodologies VALUES (%s, %s)",
            (question_id, methodology),
        )
    for eco_domain in selected["official"]:
        db.execute(
            "INSERT INTO question_eco_domains VALUES (%s, %s)",
            (question_id, eco_domain),
        )
    table = f"{kind}_question_bank"
    if kind == "manual":
        db.execute(
            f"UPDATE {table} SET question = %s, official_domains_json = %s, "
            "methodological_approaches_json = %s, performance_domains_json = %s, "
            "answer = %s, feedback = %s WHERE id = %s",
            (
                prompt,
                json.dumps(selected["official"]),
                json.dumps(selected["methodology"]),
                json.dumps(selected["performance"]),
                answer,
                explanation,
                bank_entry["id"],
            ),
        )
    else:
        db.execute(
            f"UPDATE {table} SET question = %s, official_domains_json = %s, "
            "methodological_approaches_json = %s, performance_domains_json = %s, "
            "answer = %s, feedback = %s, status = 'inactive', "
            "validation_status = 'pending' WHERE id = %s",
            (
                prompt,
                json.dumps(selected["official"]),
                json.dumps(selected["methodology"]),
                json.dumps(selected["performance"]),
                answer,
                explanation,
                bank_entry["id"],
            ),
        )
    return []


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
        "INSERT INTO user_profiles (user_id, email, registration_date) "
        "SELECT id, email, created_at FROM users "
        "ON CONFLICT (user_id) DO NOTHING"
    )
    db.commit()


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
    performance_placeholders = ",".join("%s" for _ in filters["performance"])
    methodology_placeholders = ",".join("%s" for _ in filters["methodology"])
    eco_placeholders = ",".join("%s" for _ in filters["eco"])
    query = (
        "SELECT q.id FROM questions q "
        "WHERE q.active = 1 "
        "AND EXISTS (SELECT 1 FROM question_performance_domains d "
        f"WHERE d.question_id = q.id AND d.domain_id IN ({performance_placeholders})) "
        "AND EXISTS (SELECT 1 FROM question_methodologies m "
        f"WHERE m.question_id = q.id AND m.methodology_id IN ({methodology_placeholders})) "
        "AND EXISTS (SELECT 1 FROM question_eco_domains e "
        f"WHERE e.question_id = q.id AND e.eco_domain_id IN ({eco_placeholders})) "
        "ORDER BY RANDOM() LIMIT %s"
    )
    params = (
        *filters["performance"],
        *filters["methodology"],
        *filters["eco"],
        limit,
    )
    return db.execute(query, params).fetchall()


def count_matching_questions(db, filters):
    performance_placeholders = ",".join("%s" for _ in filters["performance"])
    methodology_placeholders = ",".join("%s" for _ in filters["methodology"])
    eco_placeholders = ",".join("%s" for _ in filters["eco"])
    query = (
        "SELECT COUNT(*) AS count FROM questions q "
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
    return db.execute(query, params).fetchone()["count"]


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


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
