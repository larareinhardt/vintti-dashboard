from __future__ import annotations

import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=10,
    )


def run_query(sql, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or {})
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_schema_info(table: str | None = None) -> list:
    """Lista las tablas de la base (sin filtro) o las columnas de una tabla puntual
    (con filtro), para que el agente pueda armar una consulta SQL sin adivinar
    nombres de tablas/columnas."""
    if table:
        return run_query(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %(table)s
            ORDER BY ordinal_position
            """,
            {"table": table},
        )
    return run_query(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' ORDER BY table_name
        """
    )


MAX_FREE_QUERY_ROWS = 500


def run_readonly_query(sql: str) -> list:
    """
    Ejecuta una consulta SQL de solo lectura, escrita libremente por el agente
    (no una funcion pre-armada y verificada). Pensada para preguntas puntuales
    que no tienen una herramienta propia todavia.

    Guardas de seguridad (ademas de que el usuario de la base ya es de solo
    lectura): solo se permite un unico SELECT (nada de INSERT/UPDATE/DELETE/DDL
    ni multiples sentencias encadenadas con ";"), con un tope de filas y un
    timeout, para que una consulta rara no se cuelgue ni traiga medio millon
    de filas.
    """
    cleaned = sql.strip().rstrip(";").strip()
    if ";" in cleaned:
        raise ValueError("Solo se permite una sola sentencia SELECT, sin ';' en el medio.")
    if not cleaned.lower().startswith("select"):
        raise ValueError("Solo se permiten consultas SELECT.")

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '10s'")
        cur.execute(cleaned)
        rows = cur.fetchmany(MAX_FREE_QUERY_ROWS)
        truncated = cur.fetchone() is not None
        cur.close()
        result = [dict(r) for r in rows]
        if truncated:
            result.append({"_aviso": f"se corto en {MAX_FREE_QUERY_ROWS} filas, hay mas resultados"})
        return result
    finally:
        conn.close()
