"""Configuración y conexión PostgreSQL para la aplicación."""

import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor, register_uuid


def database_config_from_env():
    """Lee los parámetros de PostgreSQL de variables de entorno."""
    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "database": os.environ.get("DB_NAME", "SimuladorPMP"),
        "user": os.environ.get("DB_USER", "postgres"),
        "password": os.environ.get("DB_PASSWORD", "Sa123456"),
        "port": int(os.environ.get("DB_PORT", "5432")),
    }


class DatabaseConnection:
    """Conexión psycopg2 con métodos execute compatibles con la aplicación."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, query, params=None):
        cursor = self._connection.cursor()
        cursor.execute(query, params)
        return cursor

    def executemany(self, query, params_seq):
        cursor = self._connection.cursor()
        cursor.executemany(query, params_seq)
        return cursor

    def __getattr__(self, name):
        return getattr(self._connection, name)


def connect_database(config=None):
    """Conecta a la base PostgreSQL indicada por host, nombre, usuario y puerto."""
    settings = config or database_config_from_env()
    required = ("host", "database", "user", "password", "port")
    missing = [name for name in required if not settings.get(name)]
    if missing:
        env_names = {
            "host": "DB_HOST",
            "database": "DB_NAME",
            "user": "DB_USER",
            "password": "DB_PASSWORD",
            "port": "DB_PORT",
        }
        raise RuntimeError(
            "Faltan parámetros de conexión PostgreSQL: "
            + ", ".join(env_names[name] for name in missing)
        )

    extra_settings = {
        key: value for key, value in settings.items() if key not in required
    }
    connection = psycopg2.connect(
        host=settings["host"],
        database=settings["database"],
        user=settings["user"],
        password=settings["password"],
        port=settings["port"],
        cursor_factory=RealDictCursor,
        **extra_settings,
    )
    register_uuid(conn_or_curs=connection)
    return DatabaseConnection(connection)


def check_database_connection(config=None):
    """Comprueba que PostgreSQL acepte una consulta en la base configurada."""
    connection = connect_database(config)
    try:
        result = connection.execute(
            "SELECT 1 AS server_response, current_database() AS database_name"
        ).fetchone()
        if result is None or result["server_response"] != 1:
            raise RuntimeError("PostgreSQL no respondió a la consulta de comprobación.")
        return result
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        check_result = check_database_connection()
    except (psycopg2.Error, RuntimeError, ValueError) as error:
        print(f"Error al comprobar PostgreSQL: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(
        "Conexión correcta: PostgreSQL respondió y la base "
        f"'{check_result['database_name']}' está disponible."
    )
