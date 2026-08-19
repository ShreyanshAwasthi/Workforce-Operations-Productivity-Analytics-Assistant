"""
analytics.py
------------
Business logic for the Operations Dashboard.

All KPIs are computed dynamically from the SQLite database using pandas,
filtered by the operational segment the manager selects (date range,
facility, department, shift, employment type).

Business-logic definitions (kept explicit so the numbers are defensible):

  Attendance Rate      = Present days / Total logged days
  Absenteeism Rate     = Absent days / Total logged days
  Late Rate            = Late days / Total logged days
  Overtime             = Sum of overtime_hours on ATTENDANCE_LOGS (already a
                          discrete daily field, so no double-counting risk)
  Efficiency %          = Mean of PERFORMANCE_METRICS.efficiency_pct (each
                          record already normalizes units_processed/target_units)
  Attrition Rate        = Employees who exited within the selected date range
                          divided by the average active headcount over that
                          same range (not just a raw termination count).
"""

from dataclasses import dataclass
import pandas as pd
from database import get_connection


@dataclass
class Filters:
    start_date: str
    end_date: str
    facility_ids: list | None = None   # None / [] = all
    departments: list | None = None
    shifts: list | None = None
    employment_types: list | None = None


def _in_clause(values):
    """Build a safe SQL IN (...) fragment from a trusted, internal list of values."""
    if not values:
        return None
    escaped = ",".join("'" + str(v).replace("'", "''") + "'" for v in values)
    return f"({escaped})"


def load_filtered_data(filters: Filters, db_path: str = "workforce.db") -> dict:
    """
    Load the employee, attendance, performance, and leave data relevant to the
    selected filters into pandas DataFrames. This is the single source of
    truth the rest of the dashboard reads from.
    """
    conn = get_connection(db_path)

    emp_where = ["1=1"]
    if filters.facility_ids:
        emp_where.append(f"facility_id IN {_in_clause(filters.facility_ids)}")
    if filters.departments:
        emp_where.append(f"department IN {_in_clause(filters.departments)}")
    if filters.shifts:
        emp_where.append(f"shift_type IN {_in_clause(filters.shifts)}")
    if filters.employment_types:
        emp_where.append(f"employment_type IN {_in_clause(filters.employment_types)}")
    emp_sql = f"SELECT * FROM EMPLOYEE_MASTER WHERE {' AND '.join(emp_where)}"
    employees = pd.read_sql_query(emp_sql, conn)

    emp_ids = employees["employee_id"].tolist()
    if not emp_ids:
        conn.close()
        empty = pd.DataFrame()
        return {"employees": employees, "attendance": empty, "performance": empty, "leave": empty}

    emp_ids_clause = _in_clause(emp_ids)

    attendance = pd.read_sql_query(
        f"""SELECT * FROM ATTENDANCE_LOGS
            WHERE employee_id IN {emp_ids_clause}
              AND date BETWEEN ? AND ?""",
        conn, params=(filters.start_date, filters.end_date),
    )

    performance = pd.read_sql_query(
        f"""SELECT * FROM PERFORMANCE_METRICS
            WHERE employee_id IN {emp_ids_clause}
              AND date BETWEEN ? AND ?""",
        conn, params=(filters.start_date, filters.end_date),
    )

    leave = pd.read_sql_query(
        f"""SELECT * FROM LEAVE_REQUESTS
            WHERE employee_id IN {emp_ids_clause}
              AND start_date <= ? AND end_date >= ?""",
        conn, params=(filters.end_date, filters.start_date),
    )

    conn.close()

    # Enrich attendance/performance with employee dimension columns for easy grouping
    emp_dim = employees[["employee_id", "department", "facility_id", "shift_type", "employment_type", "name"]]
    if not attendance.empty:
        attendance = attendance.merge(emp_dim, on="employee_id", suffixes=("", "_emp"))
    if not performance.empty:
        performance = performance.merge(emp_dim, on="employee_id", suffixes=("", "_emp"))

    return {"employees": employees, "attendance": attendance, "performance": performance, "leave": leave}


def compute_kpis(data: dict) -> dict:
    """Compute the headline KPI set used in the dashboard's overview strip and detail cards."""
    employees = data["employees"]
    attendance = data["attendance"]
    performance = data["performance"]
    leave = data["leave"]

    total_employees = len(employees)
    active_employees = int((employees["status"] == "Active").sum()) if total_employees else 0
    contract_employees = int((employees["employment_type"].isin(["Contract", "Temporary"])).sum()) if total_employees else 0
    permanent_employees = int((employees["employment_type"] == "Permanent").sum()) if total_employees else 0

    total_logs = len(attendance)
    if total_logs:
        present = (attendance["status"] == "Present").sum()
        absent = (attendance["status"] == "Absent").sum()
        late = (attendance["status"] == "Late").sum()
        attendance_rate = round(100 * present / total_logs, 1)
        absenteeism_rate = round(100 * absent / total_logs, 1)
        late_rate = round(100 * late / total_logs, 1)
        total_overtime = round(attendance["overtime_hours"].sum(), 1)
        avg_overtime_per_employee = round(total_overtime / max(total_employees, 1), 2)
        high_overtime_employee_count = int(
            attendance.groupby("employee_id")["overtime_hours"].mean().gt(3.0).sum()
        )
    else:
        attendance_rate = absenteeism_rate = late_rate = 0.0
        total_overtime = avg_overtime_per_employee = 0.0
        high_overtime_employee_count = 0

    if len(performance):
        avg_efficiency = round(performance["efficiency_pct"].mean(), 1)
        total_units = int(performance["units_processed"].sum())
        total_target = int(performance["target_units"].sum())
        total_errors = int(performance["errors_flagged"].sum())
        error_rate = round(100 * total_errors / max(total_units, 1), 2)
    else:
        avg_efficiency = 0.0
        total_units = total_target = total_errors = 0
        error_rate = 0.0

    active_leaves = int(len(leave))
    leave_by_type = leave["leave_type"].value_counts().to_dict() if len(leave) else {}

    return {
        "total_employees": total_employees,
        "active_employees": active_employees,
        "contract_employees": contract_employees,
        "permanent_employees": permanent_employees,
        "attendance_rate": attendance_rate,
        "absenteeism_rate": absenteeism_rate,
        "late_rate": late_rate,
        "total_overtime": total_overtime,
        "avg_overtime_per_employee": avg_overtime_per_employee,
        "high_overtime_employee_count": high_overtime_employee_count,
        "avg_efficiency": avg_efficiency,
        "total_units": total_units,
        "total_target": total_target,
        "total_errors": total_errors,
        "error_rate": error_rate,
        "active_leaves": active_leaves,
        "leave_by_type": leave_by_type,
    }


def compute_attrition_rate(filters: Filters, db_path: str = "workforce.db") -> float:
    """
    Attrition rate = employees who exited within [start_date, end_date]
    divided by the average active headcount over that window, expressed as a %.
    This avoids mislabeling a raw termination count as a rate.
    """
    conn = get_connection(db_path)
    exited = pd.read_sql_query(
        "SELECT COUNT(*) AS c FROM EMPLOYEE_MASTER WHERE exit_date BETWEEN ? AND ?",
        conn, params=(filters.start_date, filters.end_date),
    )["c"].iloc[0]

    headcount_start = pd.read_sql_query(
        "SELECT COUNT(*) AS c FROM EMPLOYEE_MASTER WHERE join_date <= ? AND (exit_date IS NULL OR exit_date >= ?)",
        conn, params=(filters.start_date, filters.start_date),
    )["c"].iloc[0]
    headcount_end = pd.read_sql_query(
        "SELECT COUNT(*) AS c FROM EMPLOYEE_MASTER WHERE join_date <= ? AND (exit_date IS NULL OR exit_date >= ?)",
        conn, params=(filters.end_date, filters.end_date),
    )["c"].iloc[0]
    conn.close()

    avg_headcount = max((headcount_start + headcount_end) / 2, 1)
    return round(100 * exited / avg_headcount, 2)


# ---------------------------------------------------------------------------
# Chart-ready aggregations
# ---------------------------------------------------------------------------

def workforce_by_department(data: dict) -> pd.DataFrame:
    if data["employees"].empty:
        return pd.DataFrame(columns=["department", "count"])
    return data["employees"].groupby("department").size().reset_index(name="count").sort_values("count", ascending=False)


def workforce_by_facility(data: dict) -> pd.DataFrame:
    if data["employees"].empty:
        return pd.DataFrame(columns=["facility_id", "count"])
    return data["employees"].groupby("facility_id").size().reset_index(name="count").sort_values("count", ascending=False)


def workforce_by_employment_type(data: dict) -> pd.DataFrame:
    if data["employees"].empty:
        return pd.DataFrame(columns=["employment_type", "count"])
    return data["employees"].groupby("employment_type").size().reset_index(name="count")


def attendance_trend(data: dict) -> pd.DataFrame:
    """Daily attendance & absence rate over time."""
    att = data["attendance"]
    if att.empty:
        return pd.DataFrame(columns=["date", "attendance_rate", "absenteeism_rate"])
    grouped = att.groupby("date").agg(
        total=("status", "count"),
        present=("status", lambda s: (s == "Present").sum()),
        absent=("status", lambda s: (s == "Absent").sum()),
    ).reset_index()
    grouped["attendance_rate"] = round(100 * grouped["present"] / grouped["total"], 1)
    grouped["absenteeism_rate"] = round(100 * grouped["absent"] / grouped["total"], 1)
    return grouped.sort_values("date")


def absenteeism_by_shift(data: dict) -> pd.DataFrame:
    att = data["attendance"]
    if att.empty:
        return pd.DataFrame(columns=["shift_type", "absenteeism_rate"])
    grouped = att.groupby("shift_type").agg(
        total=("status", "count"), absent=("status", lambda s: (s == "Absent").sum())
    ).reset_index()
    grouped["absenteeism_rate"] = round(100 * grouped["absent"] / grouped["total"], 1)
    return grouped.sort_values("absenteeism_rate", ascending=False)


def absenteeism_by_facility(data: dict) -> pd.DataFrame:
    att = data["attendance"]
    if att.empty:
        return pd.DataFrame(columns=["facility_id", "absenteeism_rate"])
    grouped = att.groupby("facility_id").agg(
        total=("status", "count"), absent=("status", lambda s: (s == "Absent").sum())
    ).reset_index()
    grouped["absenteeism_rate"] = round(100 * grouped["absent"] / grouped["total"], 1)
    return grouped.sort_values("absenteeism_rate", ascending=False)


def efficiency_by_department(data: dict) -> pd.DataFrame:
    perf = data["performance"]
    if perf.empty:
        return pd.DataFrame(columns=["department", "avg_efficiency"])
    grouped = perf.groupby("department")["efficiency_pct"].mean().round(1).reset_index(name="avg_efficiency")
    return grouped.sort_values("avg_efficiency", ascending=False)


def target_vs_actual(data: dict) -> pd.DataFrame:
    perf = data["performance"]
    if perf.empty:
        return pd.DataFrame(columns=["department", "units_processed", "target_units"])
    grouped = perf.groupby("department").agg(
        units_processed=("units_processed", "sum"), target_units=("target_units", "sum")
    ).reset_index()
    return grouped.sort_values("target_units", ascending=False)


def productivity_by_shift(data: dict) -> pd.DataFrame:
    perf = data["performance"]
    if perf.empty:
        return pd.DataFrame(columns=["shift_type", "avg_efficiency"])
    grouped = perf.groupby("shift_type")["efficiency_pct"].mean().round(1).reset_index(name="avg_efficiency")
    return grouped.sort_values("avg_efficiency", ascending=False)


def overtime_by_department(data: dict) -> pd.DataFrame:
    att = data["attendance"]
    if att.empty:
        return pd.DataFrame(columns=["department", "total_overtime"])
    grouped = att.groupby("department")["overtime_hours"].sum().round(1).reset_index(name="total_overtime")
    return grouped.sort_values("total_overtime", ascending=False)


def overtime_by_facility(data: dict) -> pd.DataFrame:
    att = data["attendance"]
    if att.empty:
        return pd.DataFrame(columns=["facility_id", "total_overtime"])
    grouped = att.groupby("facility_id")["overtime_hours"].sum().round(1).reset_index(name="total_overtime")
    return grouped.sort_values("total_overtime", ascending=False)


def overtime_trend(data: dict) -> pd.DataFrame:
    att = data["attendance"]
    if att.empty:
        return pd.DataFrame(columns=["date", "total_overtime"])
    grouped = att.groupby("date")["overtime_hours"].sum().round(1).reset_index(name="total_overtime")
    return grouped.sort_values("date")


# ---------------------------------------------------------------------------
# Operational alerts (transparent, threshold-based business rules)
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    "high_overtime_hours_per_day": 3.0,   # avg overtime hours/day considered "high" for a group
    "low_efficiency_pct": 80.0,           # efficiency % below this is "low"
    "high_absenteeism_pct": 10.0,         # absence rate above this is "high"
    "high_error_rate_pct": 5.0,           # errors per 100 units above this is "high"
}


def generate_alerts(data: dict, thresholds: dict = None) -> list:
    """
    Surface simple, explainable operational alerts using transparent business
    rules (no ML). Returns a list of dicts: {level, message}.
    """
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    alerts = []

    att = data["attendance"]
    perf = data["performance"]

    # High overtime by shift
    if not att.empty:
        ot_by_shift = att.groupby("shift_type")["overtime_hours"].mean()
        for shift, val in ot_by_shift.items():
            if val > t["high_overtime_hours_per_day"]:
                alerts.append({
                    "level": "warning",
                    "message": f"High average overtime in {shift} shift ({val:.1f} hrs/day, threshold {t['high_overtime_hours_per_day']}).",
                })

        # High absenteeism by facility
        abs_by_fac = att.groupby("facility_id").apply(
            lambda g: 100 * (g["status"] == "Absent").sum() / len(g)
        )
        for fac, val in abs_by_fac.items():
            if val > t["high_absenteeism_pct"]:
                alerts.append({
                    "level": "warning",
                    "message": f"Absenteeism above threshold at {fac} ({val:.1f}%, threshold {t['high_absenteeism_pct']}%).",
                })

    # Low efficiency by department
    if not perf.empty:
        eff_by_dept = perf.groupby("department")["efficiency_pct"].mean()
        for dept, val in eff_by_dept.items():
            if val < t["low_efficiency_pct"]:
                alerts.append({
                    "level": "critical",
                    "message": f"Low efficiency in {dept} ({val:.1f}%, below {t['low_efficiency_pct']}% target).",
                })

        # High error rate by department
        err_by_dept = perf.groupby("department").apply(
            lambda g: 100 * g["errors_flagged"].sum() / max(g["units_processed"].sum(), 1)
        )
        for dept, val in err_by_dept.items():
            if val > t["high_error_rate_pct"]:
                alerts.append({
                    "level": "critical",
                    "message": f"High error rate in {dept} ({val:.1f} errors per 100 units, threshold {t['high_error_rate_pct']}).",
                })

    return alerts


def drilldown_facility(data: dict, facility_id: str) -> dict:
    """
    Simple analytical drill-down for a facility: breaks its attendance and
    performance down by shift, department, and employment type so a manager
    can investigate contributing factors without a black-box RCA engine.
    """
    att = data["attendance"]
    perf = data["performance"]
    fac_att = att[att["facility_id"] == facility_id] if not att.empty else att
    fac_perf = perf[perf["facility_id"] == facility_id] if not perf.empty else perf

    result = {}
    if not fac_att.empty:
        result["by_shift"] = fac_att.groupby("shift_type").agg(
            avg_overtime=("overtime_hours", "mean"),
            absenteeism_rate=("status", lambda s: 100 * (s == "Absent").sum() / len(s)),
        ).round(2).reset_index()
        result["by_department"] = fac_att.groupby("department").agg(
            avg_overtime=("overtime_hours", "mean"),
            absenteeism_rate=("status", lambda s: 100 * (s == "Absent").sum() / len(s)),
        ).round(2).reset_index()
        result["by_employment_type"] = fac_att.groupby("employment_type").agg(
            avg_overtime=("overtime_hours", "mean"),
            absenteeism_rate=("status", lambda s: 100 * (s == "Absent").sum() / len(s)),
        ).round(2).reset_index()
    if not fac_perf.empty:
        result["efficiency_by_department"] = fac_perf.groupby("department")["efficiency_pct"].mean().round(1).reset_index()

    return result
