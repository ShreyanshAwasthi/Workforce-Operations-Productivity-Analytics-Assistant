"""
app.py
------streamlit run app.py
AI-Powered Workforce Operations Analytics Assistant.

Flow: Dashboard -> Monitor -> Ask -> Analyze -> Decide

An Operations Manager opens the app and immediately sees a live snapshot of
workforce operations (filterable by date range, facility, department, shift,
and employment type), spots issues via a simple operational-alerts panel,
and can then ask plain-English questions that get translated to SQL and
answered with a short business insight.
"""

import os
import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

import database
import analytics
import sql_generator

st.markdown(
    """
    <style>
    .stAppDeployButton {
        visibility: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True
)

load_dotenv()

st.set_page_config(page_title="Workforce Operations Analytics Assistant", page_icon="👥", layout="wide")

DB_PATH = "workforce.db"


# ---------------------------------------------------------------------------
# Cached data access
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def cached_filter_options():
    return database.get_filter_options(DB_PATH)


@st.cache_data(show_spinner=False)
def cached_load_filtered_data(start_date, end_date, facilities, departments, shifts, emp_types):
    filters = analytics.Filters(
        start_date=start_date, end_date=end_date,
        facility_ids=facilities or None, departments=departments or None,
        shifts=shifts or None, employment_types=emp_types or None,
    )
    data = analytics.load_filtered_data(filters, DB_PATH)
    return data, filters


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

def render_sidebar():
    st.sidebar.title("Filters")

    if not os.path.exists(DB_PATH):
        st.sidebar.error("workforce.db not found. Run `python generate_data.py` first.")
        st.stop()

    options = cached_filter_options()
    min_date = pd.to_datetime(options["min_date"]).date()
    max_date = pd.to_datetime(options["max_date"]).date()

    date_range = st.sidebar.date_input(
        "Date Range", value=(min_date, max_date), min_value=min_date, max_value=max_date,
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = min_date, max_date

    facility_labels = {f"{name} ({fid})": fid for fid, name in options["facilities"]}
    selected_facility_labels = st.sidebar.multiselect("Facility", options=list(facility_labels.keys()))
    selected_facilities = [facility_labels[label] for label in selected_facility_labels]

    selected_departments = st.sidebar.multiselect("Department", options=options["departments"])
    selected_shifts = st.sidebar.multiselect("Shift", options=options["shifts"])
    selected_emp_types = st.sidebar.multiselect("Employment Type", options=options["employment_types"])

    st.sidebar.divider()
    st.sidebar.caption("Operational Alert Thresholds")
    high_ot = st.sidebar.slider("High overtime (hrs/day)", 1.0, 6.0, analytics.DEFAULT_THRESHOLDS["high_overtime_hours_per_day"], 0.5)
    low_eff = st.sidebar.slider("Low efficiency (%)", 50, 95, int(analytics.DEFAULT_THRESHOLDS["low_efficiency_pct"]))
    high_abs = st.sidebar.slider("High absenteeism (%)", 2, 25, int(analytics.DEFAULT_THRESHOLDS["high_absenteeism_pct"]))
    high_err = st.sidebar.slider("High error rate (per 100 units)", 1, 20, int(analytics.DEFAULT_THRESHOLDS["high_error_rate_pct"]))

    thresholds = {
        "high_overtime_hours_per_day": high_ot,
        "low_efficiency_pct": low_eff,
        "high_absenteeism_pct": high_abs,
        "high_error_rate_pct": high_err,
    }

    return str(start_date), str(end_date), selected_facilities, selected_departments, selected_shifts, selected_emp_types, thresholds


# ---------------------------------------------------------------------------
# Dashboard sections
# ---------------------------------------------------------------------------

def render_overview(kpis, attrition_rate):
    st.subheader("Workforce Overview")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Employees", f"{kpis['total_employees']:,}")
    c2.metric("Attendance Rate", f"{kpis['attendance_rate']}%")
    c3.metric("Absenteeism Rate", f"{kpis['absenteeism_rate']}%")
    c4.metric("Total Overtime (hrs)", f"{kpis['total_overtime']:,.0f}")
    c5.metric("Attrition Rate", f"{attrition_rate}%")

    with st.expander("More workforce & productivity KPIs"):
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Active Employees", f"{kpis['active_employees']:,}")
        d1.metric("Contract / Temporary", f"{kpis['contract_employees']:,}")
        d2.metric("Permanent", f"{kpis['permanent_employees']:,}")
        d2.metric("Late Rate", f"{kpis['late_rate']}%")
        d3.metric("Avg Efficiency", f"{kpis['avg_efficiency']}%")
        d3.metric("Error Rate (/100 units)", f"{kpis['error_rate']}%")
        d4.metric("Avg Overtime / Employee (hrs)", f"{kpis['avg_overtime_per_employee']}")
        d4.metric("Employees w/ High Overtime", f"{kpis['high_overtime_employee_count']:,}")


def render_charts(data):
    st.subheader("Operations Performance")

    row1_col1, row1_col2 = st.columns(2)
    with row1_col1:
        trend = analytics.attendance_trend(data)
        if not trend.empty:
            fig = px.line(trend, x="date", y=["attendance_rate", "absenteeism_rate"],
                           title="Attendance & Absenteeism Trend", labels={"value": "%", "date": "Date"})
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No attendance data for the selected filters.")

    with row1_col2:
        prod = analytics.efficiency_by_department(data)
        if not prod.empty:
            fig = px.bar(prod, x="department", y="avg_efficiency", title="Average Efficiency by Department")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No performance data for the selected filters.")

    row2_col1, row2_col2 = st.columns(2)
    with row2_col1:
        abs_shift = analytics.absenteeism_by_shift(data)
        if not abs_shift.empty:
            fig = px.bar(abs_shift, x="shift_type", y="absenteeism_rate", title="Absenteeism by Shift")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No shift attendance data available.")

    with row2_col2:
        abs_fac = analytics.absenteeism_by_facility(data)
        if not abs_fac.empty:
            fig = px.bar(abs_fac, x="facility_id", y="absenteeism_rate", title="Absenteeism by Facility")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No facility attendance data available.")

    with st.expander("Workforce distribution & overtime detail"):
        e1, e2, e3 = st.columns(3)
        with e1:
            by_dept = analytics.workforce_by_department(data)
            if not by_dept.empty:
                st.plotly_chart(px.pie(by_dept, names="department", values="count", title="Employees by Department"), use_container_width=True)
        with e2:
            by_fac = analytics.workforce_by_facility(data)
            if not by_fac.empty:
                st.plotly_chart(px.pie(by_fac, names="facility_id", values="count", title="Employees by Facility"), use_container_width=True)
        with e3:
            by_type = analytics.workforce_by_employment_type(data)
            if not by_type.empty:
                st.plotly_chart(px.pie(by_type, names="employment_type", values="count", title="Employees by Employment Type"), use_container_width=True)

        f1, f2 = st.columns(2)
        with f1:
            ot_dept = analytics.overtime_by_department(data)
            if not ot_dept.empty:
                st.plotly_chart(px.bar(ot_dept, x="department", y="total_overtime", title="Overtime by Department"), use_container_width=True)
        with f2:
            ot_trend = analytics.overtime_trend(data)
            if not ot_trend.empty:
                st.plotly_chart(px.line(ot_trend, x="date", y="total_overtime", title="Overtime Trend"), use_container_width=True)


def render_alerts(data, thresholds):
    st.subheader("Operational Alerts")
    st.caption("Help the Operations Manager know where to look — transparent, threshold-based rules")
    alerts = analytics.generate_alerts(data, thresholds)
    if not alerts:
        st.success("No thresholds breached for the selected filters. Operations look healthy.")
    else:
        for alert in alerts:
            icon = "🔴" if alert["level"] == "critical" else "⚠️"
            st.markdown(f"{icon} {alert['message']}")


def render_assistant():
    st.subheader("Ask the Workforce Assistant")
    st.caption("Ask an operational question in plain English. Example: \"Which department has the highest overtime?\"")

    question = st.text_input("Ask a question:", placeholder="e.g., Which facility has the highest absenteeism?")
    submit = st.button("Ask Assistant")

    if not submit:
        return
    if not question.strip():
        st.warning("Please enter a question before submitting.")
        return

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        st.error("GOOGLE_API_KEY is missing. Add it to your .env file to use the assistant.")
        return
    
    try:
        with st.spinner("Generating SQL query..."):
            sql_query = sql_generator.generate_sql(question)
    except ValueError as e:  
        st.warning(str(e))
        return
    except Exception as e:
        st.error(f"Could not reach the Gemini API: {e}")
        return

    st.markdown("**Question**")
    st.write(question)

    st.markdown("**Generated SQL**")
    st.code(sql_query, language="sql")

    with st.spinner("Executing query..."):
        columns, rows, error = database.read_sql_query(sql_query, DB_PATH)

    if error:
        st.error(error)
        return

    st.markdown("**Result**")
    if rows:
        df = pd.DataFrame(rows, columns=columns)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No matching records found in the workforce database.")

    st.markdown("**Business Insight**")
    with st.spinner("Generating insight..."):
        insight = sql_generator.generate_insight(question, columns, rows)
    st.info(insight)


def render_example_questions():
    with st.expander("Example questions you can ask"):
        examples = [
            "Which facility has the highest absenteeism?",
            "Which department has the highest overtime?",
            "Show me employees who worked more than 3 hours of overtime this month.",
            "Compare productivity between morning and night shifts.",
            "Which department has the lowest average efficiency?",
            "How many contract employees are currently active?",
            "Which facility processed the highest number of units?",
            "Show absenteeism trends for the last 3 months.",
            "Which employees have high overtime and low efficiency?",
            "What is the attrition rate by facility?",
        ]
        for q in examples:
            st.markdown(f"- {q}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.title("👥 Workforce Operations Analytics Assistant ")
    st.caption("Dashboard → Monitor → Ask → Analyze → Decide")

    start_date, end_date, facilities, departments, shifts, emp_types, thresholds = render_sidebar()

    data, _filters = cached_load_filtered_data(start_date, end_date, facilities, departments, shifts, emp_types)

    if data["employees"].empty:
        st.warning("No employees match the selected filters. Try broadening your filter selection.")
        return

    kpis = analytics.compute_kpis(data)
    attrition_filters = analytics.Filters(start_date=start_date, end_date=end_date,
                                           facility_ids=facilities or None, departments=departments or None,
                                           shifts=shifts or None, employment_types=emp_types or None)
    attrition_rate = analytics.compute_attrition_rate(attrition_filters, DB_PATH)

    render_overview(kpis, attrition_rate)
    st.divider()
    render_charts(data)
    st.divider()
    render_alerts(data, thresholds)
    st.divider()
    render_assistant()
    render_example_questions()


if __name__ == "__main__":
    main()
