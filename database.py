"""
database.py
------------
Thin data-access layer for the workforce SQLite database.

Responsibilities:
  - Open connections to workforce.db
  - Run arbitrary read queries safely (used by the NL assistant)
  - Provide basic SQL validation to block destructive statements
  - Provide a couple of typed helpers used by analytics.py
"""

import sqlite3
import pandas as pd

DB_PATH = "workforce.db"

# Statements that should never be allowed through the natural-language assistant.
# This is intentionally simple keyword-based validation, not a full SQL parser -
# it is meant as a basic safety net, not enterprise-grade protection.
BLOCKED_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "CREATE", "REPLACE", "ATTACH", "DETACH", "PRAGMA", "VACUUM",
]


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Return a new SQLite connection to the workforce database."""
    return sqlite3.connect(db_path)


def validate_sql(sql: str) -> tuple[bool, str]:
    """
    Basic guardrail for LLM-generated SQL.

    Returns (is_valid, reason). Only single SELECT statements are allowed.
    """
    if not sql or not sql.strip():
        return False, "Empty query generated."

    cleaned = sql.strip().rstrip(";")

    # Must start with SELECT (allow leading whitespace/parens for CTEs like WITH ... SELECT)
    lowered = cleaned.lower().lstrip()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False, "Only SELECT queries are allowed."

    # Block multiple statements chained with semicolons
    if ";" in sql.strip().rstrip(";"):
        return False, "Multiple SQL statements are not allowed."

    upper_sql = cleaned.upper()
    for keyword in BLOCKED_KEYWORDS:
        # Match as a whole word to avoid false positives (e.g. "updated_at" column)
        import re
        if re.search(rf"\b{keyword}\b", upper_sql):
            return False, f"Query contains a disallowed operation: {keyword}."

    return True, "OK"


def read_sql_query(sql: str, db_path: str = DB_PATH) -> tuple[list, list, str | None]:
    """
    Execute a validated read-only SQL query.

    Returns (column_names, rows, error_message). error_message is None on success.
    """
    is_valid, reason = validate_sql(sql)
    if not is_valid:
        return [], [], f"Query blocked for safety: {reason}"

    try:
        conn = get_connection(db_path)
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        column_names = [d[0] for d in cur.description] if cur.description else []
        conn.close()
        return column_names, rows, None
    except sqlite3.Error as e:
        return [], [], f"Database error: {e}"


def query_df(sql: str, db_path: str = DB_PATH, params: tuple = ()) -> pd.DataFrame:
    """Run a trusted (internally-authored) SQL query and return a DataFrame.

    Used by analytics.py for dashboard calculations - not exposed to the LLM.
    """
    conn = get_connection(db_path)
    try:
        df = pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()
    return df


def get_filter_options(db_path: str = DB_PATH) -> dict:
    """Return the distinct values available for each dashboard filter."""
    conn = get_connection(db_path)
    try:
        facilities = pd.read_sql_query(
            "SELECT facility_id, facility_name FROM FACILITY_DIMENSION ORDER BY facility_name", conn
        )
        departments = pd.read_sql_query(
            "SELECT DISTINCT department FROM EMPLOYEE_MASTER ORDER BY department", conn
        )["department"].tolist()
        shifts = pd.read_sql_query(
            "SELECT DISTINCT shift_type FROM EMPLOYEE_MASTER ORDER BY shift_type", conn
        )["shift_type"].tolist()
        employment_types = pd.read_sql_query(
            "SELECT DISTINCT employment_type FROM EMPLOYEE_MASTER ORDER BY employment_type", conn
        )["employment_type"].tolist()
        date_bounds = pd.read_sql_query(
            "SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM ATTENDANCE_LOGS", conn
        )
    finally:
        conn.close()

    return {
        "facilities": list(zip(facilities["facility_id"], facilities["facility_name"])),
        "departments": departments,
        "shifts": shifts,
        "employment_types": employment_types,
        "min_date": date_bounds["min_date"].iloc[0],
        "max_date": date_bounds["max_date"].iloc[0],
    }
