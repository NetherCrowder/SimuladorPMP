import json
import os
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import Mock, patch
from urllib.parse import urlencode, urlsplit

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from psycopg2.extensions import parse_dsn
from app import (
    create_app,
    create_superuser_account,
    get_db,
    init_db,
    normalize_phone_number,
)
from conexion import connect_database, database_config_from_env
from werkzeug.security import check_password_hash


class DatabaseConfigurationTestCase(unittest.TestCase):
    def test_phone_numbers_require_international_country_code(self):
        self.assertEqual(
            normalize_phone_number("+57 (300) 123-4567", required=True),
            "+573001234567",
        )
        self.assertIsNone(normalize_phone_number(""))
        with self.assertRaisesRegex(ValueError, "código de país"):
            normalize_phone_number("3001234567", required=True)

    def test_database_config_reads_connection_fields_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "DB_HOST": "db.example.test",
                "DB_NAME": "pmp",
                "DB_USER": "owner",
                "DB_PASSWORD": "secret",
                "DB_PORT": "5433",
            },
        ):
            config = database_config_from_env()

        self.assertEqual(
            config,
            {
                "host": "db.example.test",
                "database": "pmp",
                "user": "owner",
                "password": "secret",
                "port": 5433,
            },
        )

    def test_password_is_required_for_database_connection(self):
        with self.assertRaisesRegex(RuntimeError, "DB_PASSWORD"):
            connect_database(
                {
                    "host": "localhost",
                    "database": "simulador_pmp",
                    "user": "postgres",
                    "password": "",
                    "port": 5432,
                }
            )

    @patch("conexion.connect_database")
    def test_manual_connection_check_queries_database_and_closes_connection(
        self, connect_database
    ):
        from conexion import check_database_connection

        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {
            "server_response": 1,
            "database_name": "pmp",
        }
        connect_database.return_value = connection

        result = check_database_connection()

        self.assertEqual(result["database_name"], "pmp")
        connection.execute.assert_called_once_with(
            "SELECT 1 AS server_response, current_database() AS database_name"
        )
        connection.close.assert_called_once_with()

    @patch("conexion.register_uuid")
    @patch("conexion.psycopg2.connect")
    def test_connection_uses_host_database_user_password_and_port(
        self, connect, register_uuid
    ):
        connection = Mock()
        connect.return_value = connection
        config = {
            "host": "db.example.test",
            "database": "pmp",
            "user": "owner",
            "password": "secret",
            "port": 5433,
        }

        db = connect_database(config)

        connect.assert_called_once_with(
            host="db.example.test",
            database="pmp",
            user="owner",
            password="secret",
            port=5433,
            cursor_factory=RealDictCursor,
        )
        self.assertIs(db._connection, connection)
        register_uuid.assert_called_once_with(conn_or_curs=connection)

    @patch("app.connect_database")
    def test_app_registers_protected_administration_routes(self, connect):
        connect.return_value = Mock()
        app = create_app(
            {
                "DATABASE_CONFIG": {
                    "host": "localhost",
                    "database": "test",
                    "user": "test",
                    "password": "test",
                    "port": 5432,
                },
                "SECRET_KEY": "test-secret",
                "TESTING": True,
            }
        )

        routes = {rule.rule for rule in app.url_map.iter_rules()}

        self.assertIn("/admin", routes)
        self.assertIn("/admin/users", routes)
        self.assertIn("/admin/administrators", routes)
        self.assertIn("/admin/questions", routes)
        self.assertIn("/admin/users/<uuid:user_id>/status", routes)
        self.assertIn("/admin/administrators/<uuid:admin_id>/status", routes)
        self.assertIn("/admin/questions/<kind>/<int:bank_id>/edit", routes)


class SimulatorTestCase(unittest.TestCase):
    def setUp(self):
        database_url = os.environ.get("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Define TEST_DATABASE_URL para ejecutar pruebas PostgreSQL.")
        dsn = parse_dsn(database_url)
        self.database_config = {
            "host": dsn.get("host", "localhost"),
            "database": dsn.get("dbname"),
            "user": dsn.get("user"),
            "password": dsn.get("password", ""),
            "port": int(dsn.get("port", "5432")),
            **({"sslmode": dsn["sslmode"]} if "sslmode" in dsn else {}),
        }
        self.schema = f"test_simulator_{uuid.uuid4().hex}"
        with psycopg2.connect(**self.database_config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema))
                )
        self.test_database_config = {
            **self.database_config,
            "options": f"-c search_path={self.schema}",
        }
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE_CONFIG": self.test_database_config,
                "SECRET_KEY": "test-secret",
                "WTF_CSRF_ENABLED": False,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        if hasattr(self, "schema"):
            with psycopg2.connect(**self.database_config) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("DROP SCHEMA {} CASCADE").format(
                            sql.Identifier(self.schema)
                        )
                    )

    @contextmanager
    def connect(self):
        with psycopg2.connect(
            **self.test_database_config, cursor_factory=RealDictCursor
        ) as connection:
            yield connection

    def register(self):
        return self.client.post(
            "/register",
            data={
                "username": "student",
                "first_name": "Ana",
                "second_name": "María",
                "first_surname": "Pérez",
                "second_surname": "López",
                "document_id": "CC-12345",
                "primary_phone": "+573001234567",
                "secondary_phone": "+573101234567",
                "email": "student@example.com",
                "password": "long-test-password",
                "confirmation": "long-test-password",
            },
            follow_redirects=True,
        )

    def add_privileged_account(self, role, username, email):
        with self.app.app_context():
            user_id = create_superuser_account(
                get_db(),
                username=username,
                first_name="Admin",
                first_surname="Test",
                primary_phone="+573001234567",
                email=email,
                password="long-test-password",
                role=role,
            )
            get_db().commit()
        return user_id

    def add_questions(self, count):
        with self.connect() as db:
            combination = db.execute(
                "SELECT methodology_id, eco_domain_id FROM valid_combinations LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(combination)
            for index in range(count):
                cursor = db.execute(
                    "INSERT INTO questions "
                    "(prompt, options_json, option_feedback_json, correct_option, "
                    "explanation, created_at) VALUES (%s, %s, %s, %s, %s, 1) "
                    "RETURNING id",
                    (
                        f"Pregunta ficticia {index + 1}",
                        json.dumps(["Respuesta correcta", "Respuesta alternativa"]),
                        json.dumps(
                            [
                                "Esta es la alternativa correcta para la pregunta.",
                                "Esta alternativa no coincide con la respuesta definida.",
                            ]
                        ),
                        0,
                        "La primera alternativa es correcta porque así se definió en el ejercicio.",
                    ),
                )
                question_id = cursor.fetchone()["id"]
                db.execute(
                    "INSERT INTO question_performance_domains "
                    "VALUES (%s, 'governance')",
                    (question_id,),
                )
                db.execute(
                    "INSERT INTO question_methodologies VALUES (%s, %s)",
                    (question_id, combination["methodology_id"]),
                )
                db.execute(
                    "INSERT INTO question_eco_domains VALUES (%s, %s)",
                    (question_id, combination["eco_domain_id"]),
                )

    def selected_filters(self):
        with self.connect() as db:
            combination = db.execute(
                "SELECT methodology_id, eco_domain_id FROM valid_combinations LIMIT 1"
            ).fetchone()
        return {
            "performance": ["governance"],
            "methodology": [combination["methodology_id"]],
            "eco": [combination["eco_domain_id"]],
        }

    def test_registration_login_and_password_hashing(self):
        response = self.register()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tu espacio de pr\xc3\xa1ctica", response.data)
        with self.connect() as db:
            user = db.execute(
                "SELECT id, password_hash, first_name, second_name, first_surname, "
                "second_surname, document_id, primary_phone, secondary_phone "
                "FROM users"
            ).fetchone()
            id_type = db.execute(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'users' AND column_name = 'id'"
            ).fetchone()["udt_name"]
            profile = db.execute(
                "SELECT email, registration_date, last_login_at, status, "
                "question_history_json FROM user_profiles"
            ).fetchone()
        self.assertTrue(check_password_hash(user["password_hash"], "long-test-password"))
        self.assertEqual(id_type, "uuid")
        uuid.UUID(str(user["id"]))
        self.assertIsNotNone(self.client.get_cookie("session"))
        self.assertEqual(user["first_name"], "Ana")
        self.assertEqual(user["second_name"], "María")
        self.assertEqual(user["first_surname"], "Pérez")
        self.assertEqual(user["second_surname"], "López")
        self.assertEqual(user["document_id"], "CC-12345")
        self.assertEqual(user["primary_phone"], "+573001234567")
        self.assertEqual(user["secondary_phone"], "+573101234567")
        self.assertNotEqual(user["password_hash"], "long-test-password")
        self.assertEqual(profile["email"], "student@example.com")
        self.assertIsNotNone(profile["registration_date"])
        self.assertIsNone(profile["last_login_at"])
        self.assertEqual(profile["status"], "active")
        self.assertEqual(
            json.loads(profile["question_history_json"]),
            {
                "new": [],
                "incorrect": [],
                "favorites": [],
                "correct": [],
                "mixed": [],
            },
        )

        self.client.post("/logout")
        response = self.client.post(
            "/login",
            data={"email": "student@example.com", "password": "long-test-password"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tu espacio de pr\xc3\xa1ctica", response.data)
        with self.connect() as db:
            profile = db.execute(
                "SELECT last_login_at FROM user_profiles"
            ).fetchone()
        self.assertIsNotNone(profile["last_login_at"])

    def test_database_initialization_backfills_profiles_idempotently(self):
        with self.connect() as db:
            db.execute(
                "INSERT INTO users "
                "(username, email, password_hash, created_at, first_name, "
                "first_surname, primary_phone) "
                "VALUES ('legacy', 'legacy@example.com', 'hash', 123.0, "
                "'Legacy', 'Account', '+573001234567')"
            )

        with self.app.app_context():
            init_db()
            init_db()

        with self.connect() as db:
            profile = db.execute(
                "SELECT email, registration_date FROM user_profiles "
                "WHERE email = 'legacy@example.com'"
            ).fetchone()
            profile_count = db.execute(
                "SELECT COUNT(*) AS count FROM user_profiles "
                "WHERE email = 'legacy@example.com'"
            ).fetchone()["count"]
        self.assertEqual(profile["email"], "legacy@example.com")
        self.assertEqual(profile["registration_date"], 123.0)
        self.assertEqual(profile_count, 1)

    def test_duplicate_account_is_rejected(self):
        self.register()
        response = self.client.post(
            "/register",
            data={
                "username": "another",
                "first_name": "Ana",
                "first_surname": "Pérez",
                "primary_phone": "+573001234567",
                "email": "STUDENT@example.com",
                "password": "long-test-password",
                "confirmation": "long-test-password",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"ya est", response.data)

    def test_registration_requires_first_name_surname_and_primary_phone(self):
        response = self.client.post(
            "/register",
            data={
                "username": "incomplete",
                "first_name": "Ana",
                "first_surname": "",
                "primary_phone": "",
                "email": "incomplete@example.com",
                "password": "long-test-password",
                "confirmation": "long-test-password",
            },
        )
        self.assertEqual(response.status_code, 400)
        with self.connect() as db:
            account_count = db.execute(
                "SELECT COUNT(*) AS count FROM users"
            ).fetchone()["count"]
        self.assertEqual(account_count, 0)

    def test_registration_rejects_phone_without_country_code(self):
        response = self.client.post(
            "/register",
            data={
                "username": "badphone",
                "first_name": "Ana",
                "first_surname": "Pérez",
                "primary_phone": "3001234567",
                "email": "badphone@example.com",
                "password": "long-test-password",
                "confirmation": "long-test-password",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("código de país".encode(), response.data)
        with self.connect() as db:
            account_count = db.execute(
                "SELECT COUNT(*) AS count FROM users"
            ).fetchone()["count"]
        self.assertEqual(account_count, 0)

    def test_mobile_browser_is_blocked(self):
        response = self.client.get(
            "/", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17) Mobile"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Acceso desde computadora", response.data)

    def test_all_methodology_and_eco_domain_pairs_are_allowed(self):
        with self.connect() as db:
            pairs = {
                (row["methodology_id"], row["eco_domain_id"])
                for row in db.execute(
                    "SELECT methodology_id, eco_domain_id FROM valid_combinations"
                )
            }
            question_count = db.execute(
                "SELECT COUNT(*) AS count FROM questions"
            ).fetchone()["count"]
        self.assertEqual(len(pairs), 9)
        self.assertEqual(question_count, 0)

    def test_admin_and_owner_panels_are_role_protected(self):
        self.add_privileged_account(
            "Administrator", "administrator", "administrator@example.com"
        )
        self.client.post("/logout")
        response = self.client.post(
            "/login",
            data={
                "email": "administrator@example.com",
                "password": "long-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin"))
        self.assertEqual(self.client.get("/admin").status_code, 200)
        self.assertEqual(self.client.get("/admin/users").status_code, 200)
        self.assertEqual(self.client.get("/admin/questions").status_code, 200)
        self.assertEqual(self.client.get("/admin/administrators").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/admin/administrators",
                data={
                    "username": "not-allowed",
                    "first_name": "No",
                    "first_surname": "Owner",
                    "primary_phone": "+573001234567",
                    "email": "no-owner@example.com",
                    "password": "long-test-password",
                    "confirmation": "long-test-password",
                },
            ).status_code,
            403,
        )

        self.client.post("/logout")
        self.add_privileged_account("Owner", "owner", "owner@example.com")
        response = self.client.post(
            "/login",
            data={"email": "owner@example.com", "password": "long-test-password"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/admin/administrators").status_code, 200)

    def test_admin_can_create_edit_and_validate_bank_questions(self):
        self.add_privileged_account(
            "Administrator", "administrator", "administrator@example.com"
        )
        self.client.post(
            "/login",
            data={
                "email": "administrator@example.com",
                "password": "long-test-password",
            },
        )
        question_data = {
            "prompt": "Pregunta administrativa",
            "options": "Primera opción\nSegunda opción",
            "correct_option": "2",
            "explanation": "La segunda opción es correcta.",
            "official_domains": ["processes"],
            "methodological_approaches": ["predictive"],
            "performance_domains": ["governance"],
        }

        response = self.client.post(
            "/admin/questions/new?kind=manual", data=question_data
        )

        self.assertEqual(response.status_code, 302)
        with self.connect() as db:
            manual = db.execute(
                "SELECT question_id, status FROM manual_question_bank "
                "WHERE question = %s",
                ("Pregunta administrativa",),
            ).fetchone()
            manual_question = db.execute(
                "SELECT source_type, correct_option, active FROM questions WHERE id = %s",
                (manual["question_id"],),
            ).fetchone()
        self.assertEqual(manual["status"], "active")
        self.assertEqual(manual_question["source_type"], "general")
        self.assertEqual(manual_question["correct_option"], 1)
        self.assertEqual(manual_question["active"], 1)

        response = self.client.post(
            "/admin/questions/new?kind=automated",
            data={**question_data, "prompt": "Pregunta automática"},
        )
        self.assertEqual(response.status_code, 302)
        with self.connect() as db:
            automated = db.execute(
                "SELECT id, question_id, status, validation_status "
                "FROM automated_question_bank WHERE question = %s",
                ("Pregunta automática",),
            ).fetchone()
        self.assertEqual(automated["status"], "inactive")
        self.assertEqual(automated["validation_status"], "pending")

        response = self.client.post(
            f"/admin/questions/automated/{automated['id']}/status",
            data={"validation_status": "approved"},
        )
        self.assertEqual(response.status_code, 302)
        with self.connect() as db:
            approved = db.execute(
                "SELECT b.status, b.validation_status, q.active "
                "FROM automated_question_bank b "
                "JOIN questions q ON q.id = b.question_id WHERE b.id = %s",
                (automated["id"],),
            ).fetchone()
        self.assertEqual(approved["status"], "active")
        self.assertEqual(approved["validation_status"], "approved")
        self.assertEqual(approved["active"], 1)

    def test_question_tags_must_use_a_registered_pair(self):
        with self.connect() as db:
            pair = db.execute(
                "SELECT methodology_id, eco_domain_id FROM valid_combinations LIMIT 1"
            ).fetchone()
            db.execute(
                "DELETE FROM valid_combinations WHERE methodology_id = %s "
                "AND eco_domain_id = %s",
                (pair["methodology_id"], pair["eco_domain_id"]),
            )
            question_id = db.execute(
                "INSERT INTO questions "
                "(prompt, options_json, correct_option, explanation, created_at) "
                "VALUES ('test', '[\"A\",\"B\"]', 0, 'test', 1) RETURNING id"
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO question_methodologies VALUES (%s, %s)",
                (question_id, pair["methodology_id"]),
            )
            with self.assertRaises(psycopg2.errors.RaiseException):
                db.execute(
                    "INSERT INTO question_eco_domains VALUES (%s, %s)",
                    (question_id, pair["eco_domain_id"]),
                )

    def test_exact_exam_size_is_required(self):
        self.register()
        filters = self.selected_filters()
        baseline = self.client.get(
            "/practice/availability",
            query_string=urlencode(filters, doseq=True),
        ).get_json()["available"]
        self.add_questions(4)
        response = self.client.post(
            "/practice/start",
            data={**filters, "length": "30"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f"Hay {baseline + 4} preguntas compatibles; se necesitan 30".encode(),
            response.data,
        )
        with self.connect() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS count FROM exams").fetchone()["count"],
                0,
            )

    def test_supported_sizes_use_exact_count_and_total_time(self):
        self.register()
        self.add_questions(180)
        filters = self.selected_filters()
        for length in (30, 60, 75, 180):
            response = self.client.post(
                "/practice/start",
                data={**filters, "length": str(length)},
            )
            self.assertEqual(response.status_code, 302)
            exam_id = int(urlsplit(response.headers["Location"]).path.rsplit("/", 1)[1])
            with self.connect() as db:
                exam = db.execute(
                    "SELECT requested_count, started_at, expires_at "
                    "FROM exams WHERE id = %s",
                    (exam_id,),
                ).fetchone()
                question_count = db.execute(
                    "SELECT COUNT(*) AS count FROM exam_questions WHERE exam_id = %s",
                    (exam_id,),
                ).fetchone()["count"]
                first_question = db.execute(
                    "SELECT question_id FROM exam_questions WHERE exam_id = %s "
                    "ORDER BY position LIMIT 1",
                    (exam_id,),
                ).fetchone()["question_id"]
            self.assertEqual(exam["requested_count"], length)
            self.assertEqual(question_count, length)
            self.assertEqual(exam["expires_at"] - exam["started_at"], length * 76)
            self.client.post(
                f"/exams/{exam_id}/answer",
                data={"question_id": first_question, "action": "finish"},
            )

    def test_exam_timer_answers_pending_and_review(self):
        self.register()
        self.add_questions(30)
        response = self.client.post(
            "/practice/start",
            data={**self.selected_filters(), "length": "30"},
        )
        self.assertEqual(response.status_code, 302)
        exam_path = response.headers["Location"]
        exam_id = int(urlsplit(exam_path).path.rsplit("/", 1)[1])
        with self.connect() as db:
            exam = db.execute(
                "SELECT * FROM exams WHERE id = %s", (exam_id,)
            ).fetchone()
            self.assertEqual(exam["expires_at"] - exam["started_at"], 30 * 76)
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS count FROM user_question_states"
                ).fetchone()["count"],
                30,
            )
            first_question = db.execute(
                "SELECT question_id FROM exam_questions "
                "WHERE exam_id = %s ORDER BY position LIMIT 1",
                (exam_id,),
            ).fetchone()["question_id"]
            second_question = db.execute(
                "SELECT question_id FROM exam_questions "
                "WHERE exam_id = %s ORDER BY position LIMIT 1 OFFSET 1",
                (exam_id,),
            ).fetchone()["question_id"]
            third_question = db.execute(
                "SELECT question_id FROM exam_questions "
                "WHERE exam_id = %s ORDER BY position LIMIT 1 OFFSET 2",
                (exam_id,),
            ).fetchone()["question_id"]
            fourth_question = db.execute(
                "SELECT question_id FROM exam_questions "
                "WHERE exam_id = %s ORDER BY position LIMIT 1 OFFSET 3",
                (exam_id,),
            ).fetchone()["question_id"]

        bank_page = self.client.get("/bank")
        self.assertEqual(bank_page.status_code, 200)

        page = self.client.get(exam_path)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Pregunta 1 de 30", page.data)

        self.client.post(
            f"/exams/{exam_id}/answer",
            data={
                "question_id": first_question,
                "selected_option": "0",
                "action": "answer",
            },
        )
        self.client.post(
            f"/exams/{exam_id}/answer",
            data={"question_id": second_question, "action": "save"},
        )
        self.client.post(f"/bank/{second_question}/favorite")
        self.client.post(
            f"/exams/{exam_id}/answer",
            data={
                "question_id": third_question,
                "selected_option": "1",
                "action": "answer",
            },
        )
        self.client.post(
            f"/exams/{exam_id}/answer",
            data={
                "question_id": fourth_question,
                "selected_option": "0",
                "action": "answer",
            },
        )
        self.client.post(
            f"/exams/{exam_id}/answer",
            data={"question_id": fourth_question, "action": "save"},
        )
        response = self.client.post(
            f"/exams/{exam_id}/answer",
            data={"question_id": second_question, "action": "finish"},
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.get(f"/exams/{exam_id}/results")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Buenas", response.data)
        self.assertIn(b"Guardadas", response.data)
        with self.connect() as db:
            status = db.execute(
                "SELECT status FROM exams WHERE id = %s", (exam_id,)
            ).fetchone()["status"]
            state_count = db.execute(
                "SELECT COUNT(*) AS count FROM user_question_states"
            ).fetchone()["count"]
            pending_count = db.execute(
                "SELECT COUNT(*) AS count FROM user_question_states WHERE pending = 1"
            ).fetchone()["count"]
            favorite_count = db.execute(
                "SELECT COUNT(*) AS count FROM user_question_states WHERE favorite = 1"
            ).fetchone()["count"]
            answer_count = db.execute(
                "SELECT COUNT(*) AS count FROM exam_answers WHERE exam_id = %s",
                (exam_id,),
            ).fetchone()["count"]
        self.assertEqual(status, "completed")
        self.assertEqual(state_count, 30)
        self.assertEqual(pending_count, 28)
        self.assertEqual(favorite_count, 1)
        self.assertEqual(answer_count, 2)

        correct_review = self.client.get(
            f"/exams/{exam_id}/review?kind=correct"
        )
        self.assertIn(b"Retroalimentaci", correct_review.data)
        incorrect_review = self.client.get(
            f"/exams/{exam_id}/review?kind=incorrect"
        )
        self.assertIn(b"Mala", incorrect_review.data)
        self.assertIn(b"Tu respuesta es incorrecta", incorrect_review.data)
        self.assertIn(b"no coincide con la respuesta definida", incorrect_review.data)
        self.assertIn(b"Retroalimentaci", incorrect_review.data)
        both_review = self.client.get(f"/exams/{exam_id}/review?kind=both")
        self.assertIn(b"Buena", both_review.data)
        self.assertIn(b"Mala", both_review.data)
        pending_review = self.client.get(
            f"/exams/{exam_id}/review?kind=pending"
        )
        self.assertIn(b"Sin respuesta registrada", pending_review.data)

    def test_expired_exam_is_finalized_as_expired(self):
        self.register()
        self.add_questions(30)
        response = self.client.post(
            "/practice/start",
            data={**self.selected_filters(), "length": "30"},
        )
        exam_id = int(urlsplit(response.headers["Location"]).path.rsplit("/", 1)[1])
        with self.connect() as db:
            db.execute("UPDATE exams SET expires_at = 0 WHERE id = %s", (exam_id,))
        response = self.client.get(f"/exams/{exam_id}/results")
        self.assertEqual(response.status_code, 200)
        with self.connect() as db:
            exam = db.execute(
                "SELECT status FROM exams WHERE id = %s", (exam_id,)
            ).fetchone()
        self.assertEqual(exam["status"], "expired")

    def test_exam_and_personal_bank_are_private_to_each_user(self):
        self.register()
        self.add_questions(30)
        response = self.client.post(
            "/practice/start",
            data={**self.selected_filters(), "length": "30"},
        )
        exam_id = int(urlsplit(response.headers["Location"]).path.rsplit("/", 1)[1])

        self.client.post("/logout")
        second_user = self.client.post(
            "/register",
            data={
                "username": "another",
                "email": "another@example.com",
                "password": "another-long-password",
                "confirmation": "another-long-password",
            },
        )
        self.assertEqual(second_user.status_code, 302)
        self.assertEqual(self.client.get(f"/exams/{exam_id}/results").status_code, 404)
        bank_page = self.client.get("/bank")
        self.assertIn(b"A\xc3\xban no hay preguntas", bank_page.data)


if __name__ == "__main__":
    unittest.main()
