"""
generate_data.py
-----------------
Generates a realistic synthetic workforce dataset and loads it into workforce.db.

Run this any time you want to regenerate the database from scratch:
    python generate_data.py

The dataset is randomized but reproducible (fixed random seed), and includes
deliberate but not-too-obvious operational patterns, e.g.:
  - Night shift tends to run higher overtime and higher absenteeism.
  - The Packaging department tends to run lower efficiency.
  - A couple of facilities run under more staffing pressure than others.
"""

import random
import sqlite3
from datetime import date, timedelta

random.seed(42)

DB_PATH = "workforce.db"

# ---------------------------------------------------------------------------
# Reference / dimension data
# ---------------------------------------------------------------------------

FACILITIES = [
    ("FAC-01", "North Distribution Hub", "North", "Sarah Connor"),
    ("FAC-02", "South Manufacturing Plant", "South", "John Smith"),
    ("FAC-03", "East Fulfillment Center", "East", "Meera Iyer"),
    ("FAC-04", "West Logistics Park", "West", "David Okafor"),
    ("FAC-05", "Central Packaging Unit", "Central", "Ananya Rao"),
    ("FAC-06", "North Cold Storage", "North", "Wei Chen"),
]

# Facilities under more staffing pressure -> higher overtime & absenteeism
HIGH_PRESSURE_FACILITIES = {"FAC-04", "FAC-06"}

SHIFTS = ["Morning", "Evening", "Night"]
# Shift-level tendencies (multipliers applied to base rates)
SHIFT_ABSENCE_MULT = {"Morning": 0.85, "Evening": 1.0, "Night": 1.35}
SHIFT_OT_MULT = {"Morning": 0.8, "Evening": 1.0, "Night": 1.4}

DEPARTMENTS = [
    "Warehouse", "Logistics", "Manufacturing", "Operations", "Quality",
    "Maintenance", "Packaging", "Dispatch", "Procurement", "Administration",
]
# Departments that tend to run lower efficiency / higher errors
LOW_EFFICIENCY_DEPTS = {"Packaging", "Dispatch"}
HIGH_PERFORMING_DEPTS = {"Quality", "Maintenance"}

ROLES_BY_DEPT = {
    "Warehouse": ["Warehouse Associate", "Forklift Driver", "Inventory Clerk"],
    "Logistics": ["Logistics Coordinator", "Route Planner", "Dispatcher"],
    "Manufacturing": ["Machine Operator", "Assembly Technician", "Line Supervisor"],
    "Operations": ["Operations Associate", "Operations Analyst", "Shift Lead"],
    "Quality": ["Quality Inspector", "QA Technician", "Compliance Officer"],
    "Maintenance": ["Maintenance Technician", "Electrician", "Facilities Engineer"],
    "Packaging": ["Packer", "Packaging Operator", "Label Technician"],
    "Dispatch": ["Dispatch Associate", "Loading Supervisor", "Yard Coordinator"],
    "Procurement": ["Procurement Associate", "Buyer", "Vendor Coordinator"],
    "Administration": ["HR Coordinator", "Admin Assistant", "Payroll Clerk"],
}

EMPLOYMENT_TYPES = ["Permanent", "Contract", "Temporary"]
EMPLOYMENT_TYPE_WEIGHTS = [0.60, 0.28, 0.12]

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Reyansh", "Krishna", "Ishaan",
    "Neha", "Priya", "Ananya", "Diya", "Sara", "Isha", "Kavya", "Meera",
    "Rohan", "Karan", "Aditi", "Pooja", "Ravi", "Suresh", "Anil", "Vikram",
    "Sunita", "Lakshmi", "Deepa", "Rekha", "Manoj", "Sanjay", "Ajay", "Rajesh",
    "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia", "Mason",
    "Wei", "Ming", "Li", "Chen", "Yuki", "Hana", "Carlos", "Maria",
]
LAST_NAMES = [
    "Patel", "Sharma", "Gupta", "Singh", "Kumar", "Reddy", "Nair", "Iyer",
    "Rao", "Mehta", "Joshi", "Verma", "Chen", "Wang", "Okafor", "Connor",
    "Smith", "Johnson", "Garcia", "Martinez", "Brown", "Davis", "Miller", "Wilson",
]

LEAVE_TYPES = ["Sick Leave", "Annual Vacation", "Casual Leave", "Emergency Leave", "Maternity/Paternity Leave"]
LEAVE_TYPE_WEIGHTS = [0.35, 0.30, 0.20, 0.10, 0.05]

# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------

NUM_EMPLOYEES = 650
SIM_MONTHS = 9  # ~9 months of daily attendance history
END_DATE = date(2026, 7, 31)
START_DATE = END_DATE - timedelta(days=30 * SIM_MONTHS)

TASK_TYPES_BY_DEPT = {
    "Warehouse": "Picking", "Logistics": "Routing", "Manufacturing": "Assembly",
    "Operations": "Processing", "Quality": "Inspection", "Maintenance": "Servicing",
    "Packaging": "Packing", "Dispatch": "Loading", "Procurement": "Sourcing",
    "Administration": "Documentation",
}


def build_schema(cursor):
    cursor.executescript("""
    DROP TABLE IF EXISTS LEAVE_REQUESTS;
    DROP TABLE IF EXISTS PERFORMANCE_METRICS;
    DROP TABLE IF EXISTS ATTENDANCE_LOGS;
    DROP TABLE IF EXISTS EMPLOYEE_MASTER;
    DROP TABLE IF EXISTS FACILITY_DIMENSION;

    CREATE TABLE FACILITY_DIMENSION (
        facility_id VARCHAR(20) PRIMARY KEY,
        facility_name VARCHAR(50),
        region VARCHAR(50),
        shift_name VARCHAR(20),
        manager_name VARCHAR(50)
    );

    CREATE TABLE EMPLOYEE_MASTER (
        employee_id VARCHAR(20) PRIMARY KEY,
        name VARCHAR(50),
        employment_type VARCHAR(20),
        role VARCHAR(50),
        department VARCHAR(50),
        facility_id VARCHAR(20),
        shift_type VARCHAR(20),
        join_date DATE,
        exit_date DATE,
        status VARCHAR(20),
        FOREIGN KEY(facility_id) REFERENCES FACILITY_DIMENSION(facility_id)
    );

    CREATE TABLE ATTENDANCE_LOGS (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id VARCHAR(20),
        date DATE,
        facility_id VARCHAR(20),
        shift_type VARCHAR(20),
        scheduled_hours DECIMAL(4, 2),
        actual_hours DECIMAL(4, 2),
        status VARCHAR(20),
        overtime_hours DECIMAL(4, 2),
        FOREIGN KEY(employee_id) REFERENCES EMPLOYEE_MASTER(employee_id),
        FOREIGN KEY(facility_id) REFERENCES FACILITY_DIMENSION(facility_id)
    );

    CREATE TABLE PERFORMANCE_METRICS (
        perf_id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id VARCHAR(20),
        date DATE,
        task_type VARCHAR(50),
        units_processed INT,
        target_units INT,
        efficiency_pct DECIMAL(5, 2),
        errors_flagged INT,
        FOREIGN KEY(employee_id) REFERENCES EMPLOYEE_MASTER(employee_id)
    );

    CREATE TABLE LEAVE_REQUESTS (
        leave_id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id VARCHAR(20),
        leave_type VARCHAR(30),
        start_date DATE,
        end_date DATE,
        approved_by VARCHAR(50),
        FOREIGN KEY(employee_id) REFERENCES EMPLOYEE_MASTER(employee_id)
    );

    CREATE INDEX idx_attendance_emp ON ATTENDANCE_LOGS(employee_id);
    CREATE INDEX idx_attendance_date ON ATTENDANCE_LOGS(date);
    CREATE INDEX idx_attendance_facility ON ATTENDANCE_LOGS(facility_id);
    CREATE INDEX idx_perf_emp ON PERFORMANCE_METRICS(employee_id);
    CREATE INDEX idx_perf_date ON PERFORMANCE_METRICS(date);
    CREATE INDEX idx_leave_emp ON LEAVE_REQUESTS(employee_id);
    CREATE INDEX idx_emp_facility ON EMPLOYEE_MASTER(facility_id);
    CREATE INDEX idx_emp_dept ON EMPLOYEE_MASTER(department);
    """)


def gen_employees():
    employees = []
    for i in range(1, NUM_EMPLOYEES + 1):
        emp_id = f"EMP-{1000 + i}"
        name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        department = random.choice(DEPARTMENTS)
        role = random.choice(ROLES_BY_DEPT[department])
        facility_id = random.choice(FACILITIES)[0]
        shift_type = random.choices(SHIFTS, weights=[0.45, 0.30, 0.25])[0]
        employment_type = random.choices(EMPLOYMENT_TYPES, weights=EMPLOYMENT_TYPE_WEIGHTS)[0]

        # Join date: spread over the last ~3 years, but always before END_DATE
        tenure_days = random.randint(30, 3 * 365)
        join_date = END_DATE - timedelta(days=tenure_days)
        if join_date < date(2022, 1, 1):
            join_date = date(2022, 1, 1) + timedelta(days=random.randint(0, 300))

        # ~9% of employees have exited (terminated / resigned) during the window
        status = "Active"
        exit_date = None
        if random.random() < 0.09 and join_date < START_DATE:
            exit_date = join_date + timedelta(days=random.randint(60, max(61, (END_DATE - join_date).days - 10)))
            if exit_date < END_DATE:
                status = "Terminated"
            else:
                exit_date = None

        employees.append({
            "employee_id": emp_id, "name": name, "employment_type": employment_type,
            "role": role, "department": department, "facility_id": facility_id,
            "shift_type": shift_type, "join_date": join_date, "exit_date": exit_date,
            "status": status,
        })
    return employees


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def gen_attendance_and_performance(employees):
    attendance_rows = []
    performance_rows = []

    for emp in employees:
        facility_id = emp["facility_id"]
        shift = emp["shift_type"]
        dept = emp["department"]

        # Each employee gets a personal "quality" trait affecting efficiency/errors
        base_efficiency = random.gauss(92, 8)
        if dept in LOW_EFFICIENCY_DEPTS:
            base_efficiency -= 12
        if dept in HIGH_PERFORMING_DEPTS:
            base_efficiency += 6
        base_efficiency = max(55, min(base_efficiency, 118))

        error_prone = random.random() < 0.15  # a subset of employees drive most errors

        # Attendance window: from max(join_date, START_DATE) to min(exit_date or END_DATE, END_DATE)
        emp_start = max(emp["join_date"], START_DATE)
        emp_end = emp["exit_date"] if emp["exit_date"] else END_DATE
        emp_end = min(emp_end, END_DATE)
        if emp_start > emp_end:
            continue

        absence_base = 0.05
        if facility_id in HIGH_PRESSURE_FACILITIES:
            absence_base += 0.02
        absence_rate = absence_base * SHIFT_ABSENCE_MULT[shift]

        ot_base = 0.9  # average base OT hours on a "worked" day, before multiplier
        if facility_id in HIGH_PRESSURE_FACILITIES:
            ot_base += 0.6
        ot_mult = SHIFT_OT_MULT[shift]

        for d in daterange(emp_start, emp_end):
            # Skip roughly 2 days a week to represent weekly off / rest days (~5-day week)
            if d.weekday() in (5, 6) and random.random() < 0.75:
                continue
            # Sparse sampling to keep volume manageable while still "several months daily"
            if random.random() < 0.15:
                continue

            roll = random.random()
            if roll < absence_rate:
                att_status = "Absent"
                scheduled = 8.0
                actual = 0.0
                overtime = 0.0
            elif roll < absence_rate + 0.06:
                att_status = "Late"
                scheduled = 8.0
                actual = round(random.uniform(5.5, 7.5), 2)
                overtime = 0.0
            else:
                att_status = "Present"
                scheduled = 8.0
                overtime = max(0.0, round(random.gauss(ot_base, 1.1) * ot_mult, 2))
                overtime = min(overtime, 6.0)
                actual = round(scheduled + overtime, 2)

            attendance_rows.append((
                emp["employee_id"], d.isoformat(), facility_id, shift,
                scheduled, actual, att_status, overtime,
            ))

            # Performance metrics only logged on days actually worked
            if att_status != "Absent" and random.random() < 0.7:
                target = random.choice([80, 90, 100, 110, 120])
                eff_noise = random.gauss(0, 6)
                # High overtime that day nudges efficiency down a bit (fatigue effect)
                fatigue_penalty = overtime * random.uniform(0.8, 1.6)
                efficiency = max(45, base_efficiency + eff_noise - fatigue_penalty)
                units = int(round(target * (efficiency / 100.0)))
                errors = max(0, int(random.gauss(3 if error_prone else 1, 2)))
                if efficiency < 75:
                    errors += random.randint(0, 3)

                performance_rows.append((
                    emp["employee_id"], d.isoformat(), TASK_TYPES_BY_DEPT[dept],
                    units, target, round(efficiency, 2), errors,
                ))

    return attendance_rows, performance_rows


def gen_leave_requests(employees):
    rows = []
    approvers = [f[3] for f in FACILITIES]
    for emp in employees:
        emp_start = max(emp["join_date"], START_DATE)
        emp_end = emp["exit_date"] if emp["exit_date"] else END_DATE
        emp_end = min(emp_end, END_DATE)
        if emp_start > emp_end:
            continue
        num_requests = random.choices([0, 1, 2, 3], weights=[0.35, 0.35, 0.20, 0.10])[0]
        for _ in range(num_requests):
            span = (emp_end - emp_start).days
            if span <= 1:
                continue
            offset = random.randint(0, span - 1)
            leave_start = emp_start + timedelta(days=offset)
            duration = random.randint(1, 7)
            leave_end = min(leave_start + timedelta(days=duration), emp_end)
            leave_type = random.choices(LEAVE_TYPES, weights=LEAVE_TYPE_WEIGHTS)[0]
            approver = random.choice(approvers)
            rows.append((
                emp["employee_id"], leave_type, leave_start.isoformat(),
                leave_end.isoformat(), approver,
            ))
    return rows


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    build_schema(cur)

    print("Generating facilities...")
    cur.executemany(
        "INSERT INTO FACILITY_DIMENSION VALUES (?, ?, ?, ?, ?)",
        [(fid, fname, region, "Mixed", manager) for fid, fname, region, manager in FACILITIES],
    )
    # facility shift_name reflects the dominant shift but the org runs mixed shifts;
    # kept simple as a label since real per-employee shift lives on EMPLOYEE_MASTER.

    print(f"Generating {NUM_EMPLOYEES} employees...")
    employees = gen_employees()
    cur.executemany(
        "INSERT INTO EMPLOYEE_MASTER VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(
            e["employee_id"], e["name"], e["employment_type"], e["role"], e["department"],
            e["facility_id"], e["shift_type"], e["join_date"].isoformat(),
            e["exit_date"].isoformat() if e["exit_date"] else None, e["status"],
        ) for e in employees],
    )

    print("Generating attendance & performance history (this may take a moment)...")
    attendance_rows, performance_rows = gen_attendance_and_performance(employees)
    cur.executemany(
        "INSERT INTO ATTENDANCE_LOGS (employee_id, date, facility_id, shift_type, "
        "scheduled_hours, actual_hours, status, overtime_hours) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        attendance_rows,
    )
    cur.executemany(
        "INSERT INTO PERFORMANCE_METRICS (employee_id, date, task_type, units_processed, "
        "target_units, efficiency_pct, errors_flagged) VALUES (?, ?, ?, ?, ?, ?, ?)",
        performance_rows,
    )

    print("Generating leave requests...")
    leave_rows = gen_leave_requests(employees)
    cur.executemany(
        "INSERT INTO LEAVE_REQUESTS (employee_id, leave_type, start_date, end_date, "
        "approved_by) VALUES (?, ?, ?, ?, ?)",
        leave_rows,
    )

    conn.commit()

    print("\n--- Dataset Summary ---")
    for table in ["FACILITY_DIMENSION", "EMPLOYEE_MASTER", "ATTENDANCE_LOGS", "PERFORMANCE_METRICS", "LEAVE_REQUESTS"]:
        count = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {count:,} rows")

    conn.close()
    print("\nDone. workforce.db is ready.")


if __name__ == "__main__":
    main()
