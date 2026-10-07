import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit

from app import create_app
from werkzeug.security import check_password_hash


class SimulatorTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = os.path.join(self.temp_dir.name, "test.sqlite")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": database,
                "SECRET_KEY": "test-secret",
                "WTF_CSRF_ENABLED": False,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def register(self):
        return self.client.post(
            "/register",
            data={
                "username": "student",
                "email": "student@example.com",
                "password": "long-test-password",
                "confirmation": "long-test-password",
            },
            follow_redirects=True,
        )

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
                    "explanation, created_at) VALUES (?, ?, ?, ?, ?, 1)",
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
                question_id = cursor.lastrowid
                db.execute(
                    "INSERT INTO question_performance_domains VALUES (?, 'governance')",
                    (question_id,),
                )
                db.execute(
                    "INSERT INTO question_methodologies VALUES (?, ?)",
                    (question_id, combination["methodology_id"]),
                )
                db.execute(
                    "INSERT INTO question_eco_domains VALUES (?, ?)",
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
            user = db.execute("SELECT password_hash FROM users").fetchone()
        self.assertTrue(check_password_hash(user["password_hash"], "long-test-password"))
        self.assertNotEqual(user["password_hash"], "long-test-password")

        self.client.post("/logout")
        response = self.client.post(
            "/login",
            data={"email": "student@example.com", "password": "long-test-password"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tu espacio de pr\xc3\xa1ctica", response.data)

    def test_duplicate_account_is_rejected(self):
        self.register()
        response = self.client.post(
            "/register",
            data={
                "username": "another",
                "email": "STUDENT@example.com",
                "password": "long-test-password",
                "confirmation": "long-test-password",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"ya est", response.data)

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
        self.assertEqual(len(pairs), 9)

    def test_question_tags_must_use_a_registered_pair(self):
        with self.connect() as db:
            pair = db.execute(
                "SELECT methodology_id, eco_domain_id FROM valid_combinations LIMIT 1"
            ).fetchone()
            db.execute(
                "DELETE FROM valid_combinations WHERE methodology_id = ? "
                "AND eco_domain_id = ?",
                (pair["methodology_id"], pair["eco_domain_id"]),
            )
            question_id = db.execute(
                "INSERT INTO questions "
                "(prompt, options_json, correct_option, explanation, created_at) "
                "VALUES ('test', '[\"A\",\"B\"]', 0, 'test', 1)"
            ).lastrowid
            db.execute(
                "INSERT INTO question_methodologies VALUES (?, ?)",
                (question_id, pair["methodology_id"]),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO question_eco_domains VALUES (?, ?)",
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
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exams").fetchone()[0], 0)

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
                    "FROM exams WHERE id = ?",
                    (exam_id,),
                ).fetchone()
                question_count = db.execute(
                    "SELECT COUNT(*) FROM exam_questions WHERE exam_id = ?",
                    (exam_id,),
                ).fetchone()[0]
                first_question = db.execute(
                    "SELECT question_id FROM exam_questions WHERE exam_id = ? "
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
            exam = db.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
            self.assertEqual(exam["expires_at"] - exam["started_at"], 30 * 76)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM user_question_states").fetchone()[0], 30
            )
            first_question = db.execute(
                "SELECT question_id FROM exam_questions "
                "WHERE exam_id = ? ORDER BY position LIMIT 1",
                (exam_id,),
            ).fetchone()["question_id"]
            second_question = db.execute(
                "SELECT question_id FROM exam_questions "
                "WHERE exam_id = ? ORDER BY position LIMIT 1 OFFSET 1",
                (exam_id,),
            ).fetchone()["question_id"]
            third_question = db.execute(
                "SELECT question_id FROM exam_questions "
                "WHERE exam_id = ? ORDER BY position LIMIT 1 OFFSET 2",
                (exam_id,),
            ).fetchone()["question_id"]
            fourth_question = db.execute(
                "SELECT question_id FROM exam_questions "
                "WHERE exam_id = ? ORDER BY position LIMIT 1 OFFSET 3",
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
                "SELECT status FROM exams WHERE id = ?", (exam_id,)
            ).fetchone()["status"]
            state_count = db.execute(
                "SELECT COUNT(*) FROM user_question_states"
            ).fetchone()[0]
            pending_count = db.execute(
                "SELECT COUNT(*) FROM user_question_states WHERE pending = 1"
            ).fetchone()[0]
            favorite_count = db.execute(
                "SELECT COUNT(*) FROM user_question_states WHERE favorite = 1"
            ).fetchone()[0]
            answer_count = db.execute(
                "SELECT COUNT(*) FROM exam_answers WHERE exam_id = ?", (exam_id,)
            ).fetchone()[0]
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
            db.execute("UPDATE exams SET expires_at = 0 WHERE id = ?", (exam_id,))
        response = self.client.get(f"/exams/{exam_id}/results")
        self.assertEqual(response.status_code, 200)
        with self.connect() as db:
            exam = db.execute(
                "SELECT status FROM exams WHERE id = ?", (exam_id,)
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
