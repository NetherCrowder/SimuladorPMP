import json
import os
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import Mock, patch
from urllib.parse import urlencode, urlsplit

import psycopg2
from flask import Flask
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from psycopg2.extensions import parse_dsn
from app import (
    app as vercel_app,
    create_app,
    create_superuser_account,
    exam_response_is_correct,
    get_db,
    init_db,
    matching_questions,
    normalize_phone_number,
    parse_option_indexes,
    parse_exam_response,
    prepare_exam_question,
    question_form_values,
    question_interaction_config,
    render_rich_option,
    validate_filters,
    count_matching_questions,
    OFFICIAL_EXAM_DISTRIBUTIONS,
    practice_consistency,
)
from conexion import connect_database, database_config_from_env
from werkzeug.security import check_password_hash


class DatabaseConfigurationTestCase(unittest.TestCase):
    def test_vercel_entrypoint_is_a_flask_application(self):
        self.assertIsInstance(vercel_app, Flask)

    @patch("app.init_db")
    def test_serverless_app_initializes_database_on_first_request(self, init_db):
        app = create_app(
            {"SECRET_KEY": "test-secret", "TESTING": True},
            initialize_database=False,
        )
        client = app.test_client()

        init_db.assert_not_called()
        self.assertEqual(client.get("/not-found").status_code, 404)
        self.assertEqual(client.get("/not-found").status_code, 404)
        init_db.assert_called_once_with()

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

    def test_database_config_prefers_database_url_when_configured(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "deployment-url"},
            clear=True,
        ):
            config = database_config_from_env()

        self.assertEqual(config, {"url": "deployment-url"})

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

    @patch("conexion.register_uuid")
    @patch("conexion.psycopg2.connect")
    def test_connection_uses_database_url(self, connect, register_uuid):
        connection = Mock()
        connect.return_value = connection
        database_url = "postgresql://owner:secret@db.example.test/pmp?sslmode=require"

        db = connect_database({"url": database_url})

        connect.assert_called_once_with(
            database_url,
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
        client = app.test_client()
        mobile_response = client.get(
            "/", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17) Mobile"}
        )
        self.assertEqual(mobile_response.status_code, 403)
        desktop_response = client.get(
            "/", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        self.assertEqual(desktop_response.status_code, 200)


class QuestionTypeResponseTestCase(unittest.TestCase):
    def test_question_json_formats_match_their_interaction_contracts(self):
        cases = question_interaction_config(
            "case_set",
            "Texto del caso",
            [],
            [3],
            {
                "questions": [
                    {
                        "prompt": "Pregunta",
                        "options": ["A", "B", "C", "D"],
                        "correct_option": 3,
                    }
                ]
            },
        )
        self.assertEqual(cases["case_text"], "Texto del caso")
        self.assertEqual(cases["sub_questions"][0]["correct_answer"], "D")
        self.assertEqual(cases["sub_questions"][0]["options"]["A"], "A")

        media = question_interaction_config(
            "hotspot",
            "Señala el punto",
            [],
            {"x": 10, "y": 15, "width": 20, "height": 25},
            {
                "image_path": "uploads/questions/map.png",
                "alt_text": "Mapa",
                "hotspot": {"x": 10, "y": 15, "width": 20, "height": 25},
                "hotspot_pixels": {
                    "x_min": 120,
                    "y_min": 45,
                    "x_max": 200,
                    "y_max": 150,
                },
            },
        )
        self.assertEqual(media["interaction_mode"], "hotspot")
        self.assertEqual(media["target_area"]["x_min"], 120)
        self.assertEqual(media["target_area"]["x_max"], 200)
        self.assertEqual(media["alt_text"], "Mapa")

        multiple = question_interaction_config(
            "multiple_responses", "Elige", ["Uno", "Dos"], [0, 1], {}
        )
        self.assertEqual(multiple["max_Selections"], 2)
        self.assertEqual(multiple["correct_answers"], ["A", "B"])
        single = question_interaction_config(
            "multiple_choice", "Elige una", ["Uno", "Dos"], 1, {}
        )
        self.assertEqual(single["max_Selections"], 1)
        self.assertEqual(single["correct_answers"], ["B"])

        visual = question_interaction_config(
            "visual_based",
            "Interpreta la imagen",
            ["Uno", "Dos"],
            0,
            {"image_path": "uploads/questions/chart.png", "alt_text": "Gráfico"},
        )
        self.assertEqual(visual["interaction_mode"], "multiple_choice")
        self.assertEqual(visual["media_url"], "/static/uploads/questions/chart.png")
        self.assertEqual(visual["options"]["A"], "Uno")

        drag = question_interaction_config(
            "drag_drop",
            "Relaciona",
            ["Rápido", "Paralelo"],
            [1, 0],
            {"targets": ["Costo", "Riesgo"]},
        )
        self.assertEqual(
            [target["accepts"] for target in drag["target_boxes"]],
            ["s2", "s1"],
        )

        pull_down = question_interaction_config(
            "pull_down",
            "Enfoque [[blank]] y luego [[blank]]",
            ["Ágil", "Predictivo"],
            [0, 1],
            {},
        )
        self.assertEqual(
            pull_down["template_text"],
            "Enfoque [BLANK_1] y luego [BLANK_2]",
        )
        self.assertEqual(pull_down["dropdowns"]["BLANK_2"]["correct"], "Predictivo")

    def test_filter_validation_accepts_one_axis_and_rejects_mixed_axis_data(self):
        self.assertEqual(
            validate_filters(
                None, {"axis": "performance", "values": ["governance", "scope"]}
            ),
            {"axis": "performance", "values": ["governance", "scope"]},
        )
        self.assertEqual(
            validate_filters(None, {"axis": "eco", "values": ["people"]}),
            {"axis": "eco", "values": ["people"]},
        )
        self.assertEqual(
            validate_filters(
                None, {"axis": "methodology", "values": ["agile_hybrid"]}
            ),
            {"axis": "methodology", "values": ["agile_hybrid"]},
        )
        with self.assertRaisesRegex(ValueError, "eje de filtrado"):
            validate_filters(None, {"axis": "all", "values": ["people"]})
        with self.assertRaisesRegex(ValueError, "categoría no válida"):
            validate_filters(None, {"axis": "eco", "values": ["agile"]})
        with self.assertRaisesRegex(ValueError, "seleccionar una sola"):
            validate_filters(None, {"axis": "eco", "values": ["people", "processes"]})
        with self.assertRaisesRegex(ValueError, "seleccionar una sola"):
            validate_filters(
                None,
                {"axis": "methodology", "values": ["predictive", "agile_hybrid"]},
            )

    def test_question_queries_filter_only_the_selected_axis(self):
        db = Mock()
        filters = {"axis": "methodology", "values": ["agile_hybrid"]}
        matching_questions(db, filters, 30)
        query, parameters = db.execute.call_args.args
        self.assertIn("FROM question_methodologies category", query)
        self.assertIn("EXISTS (SELECT 1 FROM question_eco_domains", query)
        self.assertIn("EXISTS (SELECT 1 FROM question_performance_domains", query)
        self.assertEqual(parameters, ("agile_hybrid", 30))

        db.reset_mock()
        db.execute.return_value.fetchone.return_value = {"count": 7}
        self.assertEqual(count_matching_questions(db, filters), 7)
        query, parameters = db.execute.call_args.args
        self.assertIn("FROM question_methodologies category", query)
        self.assertEqual(parameters, ["agile_hybrid"])

    def test_official_exam_quotas_match_required_marginal_percentages(self):
        for exam_count, distribution in OFFICIAL_EXAM_DISTRIBUTIONS.items():
            self.assertEqual(sum(distribution.values()), exam_count)
            eco_counts = {
                eco: sum(
                    amount
                    for (selected_eco, _), amount in distribution.items()
                    if selected_eco == eco
                )
                for eco in ("people", "processes", "business_environment")
            }
            approach_counts = {
                approach: sum(
                    amount
                    for (_, selected_approach), amount in distribution.items()
                    if selected_approach == approach
                )
                for approach in ("agile_hybrid", "predictive")
            }
            self.assertEqual(eco_counts, (
                {"people": 20, "processes": 25, "business_environment": 15}
                if exam_count == 60
                else {"people": 59, "processes": 74, "business_environment": 47}
            ))
            self.assertEqual(
                approach_counts,
                {"agile_hybrid": exam_count * 3 // 5, "predictive": exam_count * 2 // 5},
            )

    def test_consistency_averages_only_the_three_latest_attempts_per_theme(self):
        rows = []
        for exam_id, ended_at, responses in (
            (4, 4, (0, 0)),
            (3, 3, (0, 1)),
            (2, 2, (1, 1)),
            (1, 1, (0, 0)),
        ):
            for response in responses:
                rows.append(
                    {
                        "exam_id": exam_id,
                        "filters_json": json.dumps(
                            {
                                "mode": "practice",
                                "axis": "performance",
                                "values": ["governance"],
                            }
                        ),
                        "ended_at": ended_at,
                        "question_type": "multiple_choice",
                        "correct_answer_json": "0",
                        "correct_option": 0,
                        "response_json": json.dumps(response),
                        "selected_option": response,
                        "category_id": "governance",
                    }
                )
        db = Mock()
        db.execute.side_effect = [
            Mock(fetchall=Mock(return_value=rows)),
            Mock(fetchall=Mock(return_value=[])),
            Mock(fetchall=Mock(return_value=[])),
        ]

        consistency = practice_consistency(db, "user-id")

        self.assertEqual(consistency[0]["label"], "Gobernanza (Governance)")
        self.assertEqual(consistency[0]["attempt_count"], 3)
        self.assertEqual(consistency[0]["consistency"], 50.0)

    def test_rich_option_formatting_escapes_html(self):
        rendered = str(render_rich_option("**bold** and *italic* <script>alert(1)</script>"))
        self.assertIn("<strong>bold</strong>", rendered)
        self.assertIn("<em>italic</em>", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_prepared_exam_question_keeps_selection_count_not_answer_key(self):
        question = {
            "options_json": '["A","B","C","D"]',
            "content_json": "{}",
            "correct_answer_json": "[1,3]",
            "response_json": None,
            "selected_option": None,
            "question_type": "multiple_responses",
        }
        prepare_exam_question(question)
        self.assertEqual(question["selection_limit"], 2)
        self.assertNotIn("correct_answer", question)

    def test_multiple_response_requires_exact_selected_count(self):
        app = Flask(__name__)
        question = {
            "question_type": "multiple_responses",
            "options_json": '["A","B","C","D"]',
            "content_json": "{}",
            "correct_answer_json": "[0,2]",
        }
        with app.test_request_context(
            "/", method="POST", data={"selected_options": ["0"]}
        ):
            with self.assertRaisesRegex(ValueError, "exactamente 2"):
                parse_exam_response(question)
        with app.test_request_context(
            "/", method="POST", data={"selected_options": ["0", "1", "2"]}
        ):
            with self.assertRaisesRegex(ValueError, "exactamente 2"):
                parse_exam_response(question)

    def test_fill_in_blanks_can_reuse_an_option(self):
        errors = []
        self.assertEqual(
            parse_option_indexes(
                "2\n2", 4, errors, allow_duplicates=True
            ),
            [1, 1],
        )
        self.assertEqual(errors, [])

    def test_multiple_response_and_case_answers_are_parsed(self):
        app = Flask(__name__)
        with app.test_request_context("/", method="POST", data={"selected_option": "2"}):
            self.assertEqual(
                parse_exam_response(
                    {
                        "question_type": "multiple_choice",
                        "options_json": '["A","B","C","D"]',
                        "content_json": "{}",
                    }
                ),
                2,
            )

        with app.test_request_context(
            "/", method="POST", data={"selected_options": ["0", "2"]}
        ):
            self.assertEqual(
                parse_exam_response(
                    {
                        "question_type": "multiple_responses",
                        "options_json": '["A","B","C","D"]',
                        "content_json": "{}",
                        "correct_answer_json": "[0,2]",
                    }
                ),
                [0, 2],
            )

        with app.test_request_context(
            "/", method="POST", data={"case_answers": ["1", "2", "0"]}
        ):
            self.assertEqual(
                parse_exam_response(
                    {
                        "question_type": "case_set",
                        "options_json": "[]",
                        "content_json": json.dumps(
                            {
                                "questions": [
                                    {"options": ["A", "B", "C", "D"]},
                                    {"options": ["A", "B", "C", "D"]},
                                    {"options": ["A", "B", "C", "D"]},
                                ]
                            }
                        ),
                    }
                ),
                [1, 2, 0],
            )

        with app.test_request_context(
            "/", method="POST", data={"drag_match": ["1", "0"]}
        ):
            self.assertEqual(
                parse_exam_response(
                    {
                        "question_type": "drag_drop",
                        "options_json": '["A","B"]',
                        "content_json": '{"targets":["X","Y"]}',
                    }
                ),
                [1, 0],
            )

    def test_hotspot_and_blank_responses_are_parsed(self):
        app = Flask(__name__)
        with app.test_request_context(
            "/", method="POST", data={"hotspot_point": "20.5,45"}
        ):
            self.assertEqual(
                parse_exam_response(
                    {
                        "question_type": "hotspot",
                        "options_json": "[]",
                        "content_json": "{}",
                    }
                ),
                {"x": 20.5, "y": 45.0},
            )

        with app.test_request_context(
            "/", method="POST", data={"blank_answers": ["1", "0"]}
        ):
            self.assertEqual(
                parse_exam_response(
                    {
                        "question_type": "pull_down",
                        "options_json": '["A","B"]',
                        "content_json": '{"prompt_parts":["A "," and "," C"]}',
                    }
                ),
                [1, 0],
            )

    def test_structured_answers_are_scored_as_a_whole(self):
        self.assertTrue(
            exam_response_is_correct(
                {
                    "question_type": "multiple_responses",
                    "correct_answer_json": "[0, 2]",
                    "response_json": "[2, 0]",
                }
            )
        )
        self.assertTrue(
            exam_response_is_correct(
                {
                    "question_type": "case_set",
                    "correct_answer_json": "[0, 1, 2]",
                    "response_json": "[0, 1, 2]",
                }
            )
        )
        self.assertFalse(
            exam_response_is_correct(
                {
                    "question_type": "multiple_responses",
                    "correct_answer_json": "[0, 2]",
                    "response_json": "[0]",
                }
            )
        )
        self.assertTrue(
            exam_response_is_correct(
                {
                    "question_type": "hotspot",
                    "correct_answer_json": '{"x":20,"y":30,"width":10,"height":15}',
                    "response_json": '{"x":25,"y":40}',
                }
            )
        )


class QuestionFormValuesTestCase(unittest.TestCase):
    def test_invalid_edit_preserves_the_selected_correct_option(self):
        app = Flask(__name__)
        question = {
            "prompt": "Pregunta original",
            "explanation": "Retroalimentación original",
            "question_type": "multiple_choice",
            "options_json": '["A","B","C","D"]',
            "content_json": "{}",
            "correct_answer_json": "0",
        }
        submitted = {
            "prompt": "Pregunta editada",
            "explanation": "Retroalimentación editada",
            "question_type": "multiple_choice",
            "options": "A\nB\nC\nD",
            "correct_option": "3",
        }

        with app.test_request_context("/", method="POST", data=submitted):
            values = question_form_values(
                question,
                {"official": [], "methodology": [], "performance": []},
            )

        self.assertEqual(values["prompt"], "Pregunta editada")
        self.assertEqual(values["correct_option"], "3")


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
        question_cells = [
            cell
            for cell, quota in OFFICIAL_EXAM_DISTRIBUTIONS[180].items()
            for _ in range(quota)
        ]
        with self.connect() as db:
            for index in range(count):
                eco_domain, methodology = question_cells[index % len(question_cells)]
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
                    "INSERT INTO question_eco_domains VALUES (%s, %s)",
                    (question_id, eco_domain),
                )
                db.execute(
                    "INSERT INTO question_methodologies VALUES (%s, %s)",
                    (question_id, methodology),
                )

    def selected_filters(self):
        return {"axis": "performance", "category": ["governance"]}

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
        self.assertEqual(len(pairs), 6)
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
            "question_type": "multiple_choice",
            "options": "Primera opción\nSegunda opción\nTercera opción\nCuarta opción",
            "correct_option": "2",
            "explanation": "La segunda opción es correcta.",
            "performance_domain": "governance",
            "eco_domain": "processes",
            "methodology": "agile_hybrid",
        }

        response = self.client.post(
            "/admin/questions/new?kind=manual", data=question_data
        )

        self.assertEqual(response.status_code, 302)
        with self.connect() as db:
            manual = db.execute(
                "SELECT question_id, status, category_type, category_value, "
                "interaction_config, feedback FROM manual_question_bank "
                "WHERE stem = %s",
                ("Pregunta administrativa",),
            ).fetchone()
            manual_question = db.execute(
                "SELECT source_type, correct_option, question_type, active "
                "FROM questions WHERE id = %s",
                (manual["question_id"],),
            ).fetchone()
            tags = {
                "performance": db.execute(
                    "SELECT domain_id FROM question_performance_domains "
                    "WHERE question_id = %s",
                    (manual["question_id"],),
                ).fetchone()["domain_id"],
                "eco": db.execute(
                    "SELECT eco_domain_id FROM question_eco_domains "
                    "WHERE question_id = %s",
                    (manual["question_id"],),
                ).fetchone()["eco_domain_id"],
                "methodology": db.execute(
                    "SELECT methodology_id FROM question_methodologies "
                    "WHERE question_id = %s",
                    (manual["question_id"],),
                ).fetchone()["methodology_id"],
            }
        self.assertTrue(manual["status"])
        self.assertEqual(manual["feedback"], "La segunda opción es correcta.")
        self.assertEqual(manual["category_type"], "performance_domain")
        self.assertEqual(manual["category_value"], "governance")
        self.assertEqual(manual["interaction_config"]["max_Selections"], 1)
        self.assertEqual(manual["interaction_config"]["correct_answers"], ["B"])
        self.assertEqual(manual_question["source_type"], "general")
        self.assertEqual(manual_question["correct_option"], 1)
        self.assertEqual(manual_question["question_type"], "multiple_choice")
        self.assertEqual(manual_question["active"], 1)
        self.assertEqual(
            tags,
            {
                "performance": "governance",
                "eco": "processes",
                "methodology": "agile_hybrid",
            },
        )

        response = self.client.post(
            "/admin/questions/new?kind=manual",
            data={
                **question_data,
                "prompt": "Selecciona ambas respuestas.",
                "question_type": "multiple_responses",
                "correct_options": "1\n3",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.connect() as db:
            multiple = db.execute(
                "SELECT q.question_type, q.correct_answer_json "
                "FROM questions q JOIN manual_question_bank b ON b.question_id = q.id "
                "WHERE b.stem = %s",
                ("Selecciona ambas respuestas.",),
            ).fetchone()
        self.assertEqual(multiple["question_type"], "multiple_responses")
        self.assertEqual(json.loads(multiple["correct_answer_json"]), [0, 2])

        case_data = {
            **question_data,
            "prompt": "Caso de proyecto: el equipo detecta un riesgo crítico.",
            "question_type": "case_set",
        }
        for index in range(1, 4):
            case_data[f"case_prompt_{index}"] = f"Decisión encadenada {index}?"
            case_data[f"case_options_{index}"] = (
                "Primera\nSegunda\nTercera\nCuarta"
            )
            case_data[f"case_correct_{index}"] = str(index)
        response = self.client.post(
            "/admin/questions/new?kind=manual", data=case_data
        )
        self.assertEqual(response.status_code, 302)
        with self.connect() as db:
            case = db.execute(
                "SELECT q.content_json, q.correct_answer_json "
                "FROM questions q JOIN manual_question_bank b ON b.question_id = q.id "
                "WHERE b.stem = %s",
                (case_data["prompt"],),
            ).fetchone()
        self.assertEqual(len(json.loads(case["content_json"])["questions"]), 3)
        self.assertEqual(json.loads(case["correct_answer_json"]), [0, 1, 2])

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

    def test_question_has_one_tag_per_axis_and_can_cross_the_three_axes(self):
        with self.connect() as db:
            question_id = db.execute(
                "INSERT INTO questions "
                "(prompt, options_json, correct_option, explanation, created_at) "
                "VALUES ('test', '[\"A\",\"B\"]', 0, 'test', 1) RETURNING id"
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO question_performance_domains VALUES (%s, 'governance')",
                (question_id,),
            )
            db.execute(
                "INSERT INTO question_methodologies VALUES (%s, 'agile_hybrid')",
                (question_id,),
            )
            db.execute(
                "INSERT INTO question_eco_domains VALUES (%s, 'people')",
                (question_id,),
            )
            with self.assertRaises(psycopg2.errors.RaiseException):
                db.execute(
                    "INSERT INTO question_performance_domains VALUES (%s, 'scope')",
                    (question_id,),
                )
            db.rollback()

    def test_exact_exam_size_is_required(self):
        self.register()
        filters = self.selected_filters()
        baseline = self.client.get(
            "/practice/availability",
            query_string=urlencode(
                {**filters, "mode": "practice", "length": "30"}, doseq=True
            ),
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
        for length in (15, 30, 60, 180):
            mode = "practice" if length in (15, 30) else "official"
            response = self.client.post(
                "/practice/start",
                data={
                    **(filters if mode == "practice" else {}),
                    "mode": mode,
                    "length": str(length),
                },
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
            self.assertEqual(exam["expires_at"] - exam["started_at"], length * 80)
            if mode == "official":
                with self.connect() as db:
                    distributions = db.execute(
                        "SELECT eco.eco_domain_id, methodology.methodology_id, "
                        "COUNT(*) AS count FROM exam_questions eq "
                        "JOIN question_eco_domains eco ON eco.question_id = eq.question_id "
                        "JOIN question_methodologies methodology "
                        "ON methodology.question_id = eq.question_id "
                        "WHERE eq.exam_id = %s "
                        "GROUP BY eco.eco_domain_id, methodology.methodology_id",
                        (exam_id,),
                    ).fetchall()
                self.assertEqual(
                    {
                        (row["eco_domain_id"], row["methodology_id"]): row["count"]
                        for row in distributions
                    },
                    OFFICIAL_EXAM_DISTRIBUTIONS[length],
                )
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
            self.assertEqual(exam["expires_at"] - exam["started_at"], 30 * 80)
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
