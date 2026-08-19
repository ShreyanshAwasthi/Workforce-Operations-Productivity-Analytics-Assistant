"""
sql_generator.py
-----------------
Natural-language -> SQL translation (and a lightweight business-insight
summary) powered by Gemini, using Google's current unified SDK (google-genai).

This module intentionally stays a single Gemini call for SQL generation and
one optional, simple call for the plain-English insight - no multi-agent
orchestration.
"""

import os
import re
from google import genai

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

_client = None


def get_client():
    """Lazily create the Gemini client using GOOGLE_API_KEY from the environment."""
    global _client
    if _client is None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is missing. Add it to your .env file.")
        _client = genai.Client(api_key=api_key)
    return _client


SCHEMA_CONTEXT = """
You are an expert data analyst who converts plain-English workforce operations
questions into a single valid SQLite SELECT query.

DATABASE: workforce.db - an HR / workforce operations database with 5 tables.

TABLE 1: FACILITY_DIMENSION (one row per physical site)
  - facility_id   (PK, e.g. 'FAC-01')
  - facility_name (e.g. 'North Distribution Hub')
  - region        (e.g. 'North', 'South', 'East', 'West', 'Central')
  - shift_name    (label only; per-employee shift lives on EMPLOYEE_MASTER.shift_type)
  - manager_name

TABLE 2: EMPLOYEE_MASTER (anchor table, one row per employee)
  - employee_id      (PK, e.g. 'EMP-1001')
  - name
  - employment_type  ('Permanent', 'Contract', 'Temporary')
  - role
  - department       (Warehouse, Logistics, Manufacturing, Operations, Quality,
                       Maintenance, Packaging, Dispatch, Procurement, Administration)
  - facility_id       (FK -> FACILITY_DIMENSION)
  - shift_type        ('Morning', 'Evening', 'Night')
  - join_date         (DATE)
  - exit_date         (DATE, NULL if still employed)
  - status            ('Active' or 'Terminated')

TABLE 3: ATTENDANCE_LOGS (one row per employee per logged day)
  - log_id            (PK)
  - employee_id       (FK -> EMPLOYEE_MASTER)
  - date              (DATE)
  - facility_id       (FK -> FACILITY_DIMENSION)
  - shift_type
  - scheduled_hours
  - actual_hours
  - status            ('Present', 'Absent', 'Late')
  - overtime_hours    (hours beyond scheduled_hours that day; already a discrete
                        per-day figure, so SUM() does not double-count overtime)

TABLE 4: PERFORMANCE_METRICS (one row per employee per logged workday)
  - perf_id
  - employee_id  (FK -> EMPLOYEE_MASTER)
  - date
  - task_type
  - units_processed
  - target_units
  - efficiency_pct   (already computed per record: units_processed / target_units * 100)
  - errors_flagged

TABLE 5: LEAVE_REQUESTS (one row per leave request)
  - leave_id
  - employee_id (FK -> EMPLOYEE_MASTER)
  - leave_type  (e.g. 'Sick Leave', 'Annual Vacation', 'Casual Leave')
  - start_date
  - end_date
  - approved_by

BUSINESS DEFINITIONS (use these exactly - do not invent alternate formulas):
  - Attendance Rate  = COUNT(status='Present') / COUNT(*) over ATTENDANCE_LOGS, as a percentage.
  - Absenteeism Rate = COUNT(status='Absent')  / COUNT(*) over ATTENDANCE_LOGS, as a percentage.
  - Overtime         = SUM(overtime_hours) over ATTENDANCE_LOGS. Never multiply or double-count.
  - Efficiency %      = AVG(efficiency_pct) over PERFORMANCE_METRICS for the relevant group.
  - Attrition should be based on employees whose exit_date falls in the relevant window,
    NOT simply a count of status='Terminated' with no time bound, unless the user asks
    for a raw headcount.
  - "Currently active" means status = 'Active'.
  - "This month" / "last 3 months" etc. should be interpreted relative to the MAX(date)
    present in ATTENDANCE_LOGS (the dataset's most recent date), since this is historical
    synthetic data, not live data anchored to today's real-world date.

EXAMPLES:

Q: Which facility has the highest absenteeism?
A: SELECT facility_id, ROUND(100.0 * SUM(CASE WHEN status='Absent' THEN 1 ELSE 0 END) / COUNT(*), 1) AS absenteeism_rate FROM ATTENDANCE_LOGS GROUP BY facility_id ORDER BY absenteeism_rate DESC LIMIT 1;

Q: Which department has the highest overtime?
A: SELECT e.department, ROUND(SUM(a.overtime_hours), 1) AS total_overtime FROM ATTENDANCE_LOGS a JOIN EMPLOYEE_MASTER e ON a.employee_id = e.employee_id GROUP BY e.department ORDER BY total_overtime DESC LIMIT 1;

Q: Show me employees who worked more than 3 hours of overtime this month.
A: SELECT e.name, a.date, a.overtime_hours FROM ATTENDANCE_LOGS a JOIN EMPLOYEE_MASTER e ON a.employee_id = e.employee_id WHERE a.overtime_hours > 3.0 AND a.date >= date((SELECT MAX(date) FROM ATTENDANCE_LOGS), '-1 month') ORDER BY a.overtime_hours DESC;

Q: Compare productivity between morning and night shifts.
A: SELECT e.shift_type, ROUND(AVG(p.efficiency_pct), 1) AS avg_efficiency FROM PERFORMANCE_METRICS p JOIN EMPLOYEE_MASTER e ON p.employee_id = e.employee_id WHERE e.shift_type IN ('Morning', 'Night') GROUP BY e.shift_type;

Q: Which department has the lowest average efficiency?
A: SELECT e.department, ROUND(AVG(p.efficiency_pct), 1) AS avg_efficiency FROM PERFORMANCE_METRICS p JOIN EMPLOYEE_MASTER e ON p.employee_id = e.employee_id GROUP BY e.department ORDER BY avg_efficiency ASC LIMIT 1;

Q: How many contract employees are currently active?
A: SELECT COUNT(*) AS active_contract_employees FROM EMPLOYEE_MASTER WHERE employment_type = 'Contract' AND status = 'Active';

Q: List contract workers with overtime > 2 hrs.
A: SELECT e.name, a.date, a.overtime_hours FROM EMPLOYEE_MASTER e JOIN ATTENDANCE_LOGS a ON e.employee_id = a.employee_id WHERE e.employment_type = 'Contract' AND a.overtime_hours > 2.0;

Q: Which employees have high overtime and low efficiency?
A: SELECT e.name, ROUND(AVG(a.overtime_hours), 2) AS avg_overtime, ROUND(AVG(p.efficiency_pct), 1) AS avg_efficiency FROM EMPLOYEE_MASTER e JOIN ATTENDANCE_LOGS a ON e.employee_id = a.employee_id JOIN PERFORMANCE_METRICS p ON e.employee_id = p.employee_id AND a.date = p.date GROUP BY e.employee_id HAVING avg_overtime > 3.0 AND avg_efficiency < 80.0 ORDER BY avg_overtime DESC;

CRITICAL RULES:
1. Output ONLY the raw SQL query - a single SELECT (or WITH ... SELECT) statement.
2. Do NOT enclose it in markdown code blocks like ```sql or ```.
3. Do NOT include any explanation, comments, or the word 'sql' anywhere in the output.
4. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, or any statement that modifies data.
5. Only reference tables and columns listed above.
"""

DESTRUCTIVE_INTENT_PATTERNS = [
    r"\bdelete\b",
    r"\bremove\b",
    r"\btruncate\b",
    r"\bdrop\b",
    r"\balter\b",
    r"\binsert\b",
    r"\bupdate\b",
    r"\bmodify\b",
    r"\bchange\b.*\bdata\b",
    r"\bcreate\b.*\btable\b",
]


def is_read_only_question(question: str) -> tuple[bool, str]:
    """Reject questions that explicitly request database modifications."""
    normalized = " ".join(question.lower().split())

    for pattern in DESTRUCTIVE_INTENT_PATTERNS:
        if re.search(pattern, normalized):
            return (
                False,
                "This assistant supports read-only workforce analytics and "
                "cannot modify, delete, or create database records."
            )

    return True, "OK"

def clean_sql(raw_text: str) -> str:
    """Strip markdown fences / stray labels the model might still add."""
    text = raw_text.strip()
    text = re.sub(r"^```sql", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^```", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return text.strip().rstrip(";").strip() + ";"

def generate_sql(question: str) -> str:
    """Turn a natural-language operations question into a SQL query via Gemini."""
    is_safe, reason = is_read_only_question(question)

    if not is_safe:
        raise ValueError(reason)

    client = get_client()

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=f"{SCHEMA_CONTEXT}\n\nQuestion: {question}\nSQL query:",
    )

    return clean_sql(response.text)


def generate_insight(question: str, columns: list, rows: list) -> str:
    """
    Produce a short, plain-English interpretation of a query result.
    Falls back to a deterministic summary if Gemini is unavailable so the
    app degrades gracefully instead of breaking.
    """
    if not rows:
        return "No matching records were found for this question."

    preview_rows = rows[:15]
    table_preview = " | ".join(columns) + "\n" + "\n".join(
        " | ".join(str(v) for v in r) for r in preview_rows
    )

    try:
        client = get_client()
        prompt = (
            "You are a workforce operations analyst. In 1-2 concise sentences, "
            "give the operational insight an Operations Manager would take away "
            "from this result. Be specific with numbers where available. "
            "Do not restate the question or invent data not shown below.\n\n"
            f"Question: {question}\n\nResult:\n{table_preview}\n\nInsight:"
        )
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text.strip()
    except Exception:
        # Deterministic fallback: just describe the shape of the result.
        if len(rows) == 1 and len(columns) <= 2:
            return f"Result: {dict(zip(columns, rows[0]))}"
        return f"Returned {len(rows)} row(s) across {len(columns)} column(s). See the table below for details."
