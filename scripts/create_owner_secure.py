import getpass
import os
import subprocess
import sys
from pathlib import Path

import psycopg2
from psycopg2.extensions import parse_dsn


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL_VARIABLES = (
    "DATABASE_URL",
    "POSTGRES_URL",
    "POSTGRES_URL_NON_POOLING",
)
SECURE_SSL_MODES = {"require", "verify-ca", "verify-full"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def validate_database_url(database_url):
    try:
        settings = parse_dsn(database_url)
    except psycopg2.Error as error:
        raise ValueError("La URL de PostgreSQL no tiene un formato válido.") from error

    required = ("host", "dbname", "user", "password")
    if any(not settings.get(key) for key in required):
        raise ValueError("La URL debe incluir host, base, usuario y contraseña.")

    host = settings["host"].strip("[]").lower()
    if host in LOCAL_HOSTS or host.endswith(".localhost"):
        raise ValueError("Usa la URL remota de Neon, no una base de datos local.")
    if settings.get("sslmode") not in SECURE_SSL_MODES:
        raise ValueError(
            "La conexión debe exigir TLS: agrega sslmode=require a la URL de Neon."
        )
    return settings


def get_database_url():
    database_url = next(
        (os.environ[name] for name in DATABASE_URL_VARIABLES if os.environ.get(name)),
        None,
    )
    if database_url is None:
        database_url = getpass.getpass("URL de conexión de Neon (entrada oculta): ")
    if not database_url.strip():
        raise ValueError("No se proporcionó una URL de PostgreSQL.")
    return database_url.strip()


def main():
    try:
        database_url = get_database_url()
        settings = validate_database_url(database_url)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    print(
        "Base de destino: "
        f"{settings['dbname']} en {settings['host']} (TLS: {settings['sslmode']})."
    )
    confirmation = input(
        "Escribe SI para comprobar la conexión y crear el Owner: "
    ).strip()
    if confirmation != "SI":
        print("Operación cancelada.")
        return 0

    try:
        connection = psycopg2.connect(database_url)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_user")
                database_name, database_user = cursor.fetchone()
        finally:
            connection.close()
    except psycopg2.Error as error:
        print(f"No se pudo conectar a PostgreSQL: {error}", file=sys.stderr)
        return 1

    print(f"Conexión comprobada: base '{database_name}', usuario '{database_user}'.")
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "flask", "--app", "app", "create-owner"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
