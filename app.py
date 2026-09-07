"""
PhonePe Pulse Dashboard — Complete Fixed Version
=================================================
Bug fixes applied:
  1. map_transaction: metric field is a LIST [{'count':X,'amount':Y}], not a dict.
     Fixed with _safe_metric() helper that handles both list and dict forms.
  2. top_transaction: same list-vs-dict issue in pincodes[].metric.
  3. map_user: hoverData is a dict of {district: {registeredUsers, appOpens}}, not a list.
  4. Hardcoded Windows path replaced with a cross-platform auto-detect.
  5. All extractors use try/except per file so one bad JSON never kills the whole run.
  6. Database path is relative so it works anywhere, not just D:\\Guvi project\\...
  7. State names normalised to match the GeoJSON (Title Case with spaces).
  8. Choropleth uses a reliable, always-available GeoJSON endpoint.
  9. @st.cache_data used instead of deprecated @st.cache_resource for the DB object.
 10. Quarterly trend chart added alongside yearly trend.
"""

import os
import json
import sqlite3
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────────────────────
#  0.  CONSTANTS & PATH AUTO-DETECT
# ─────────────────────────────────────────────────────────────────────────────
# Make DB path absolute relative to the script directory to prevent working directory issues
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phonepe_pulse.db")
GEOJSON_URL = (
    "https://gist.githubusercontent.com/jbrobst/56c13bbbf9d97d187fea01ca62ea5112"
    "/raw/e388c4cae20aa53cb5090210a42ebb9b765c0a36/india_states.geojson"
)

# Common locations where the cloned pulse repo might live
_CANDIDATE_PATHS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "pulse"),
    r"D:\Guvi project\Phone pay\pulse",
    r"D:\phonepe\pulse",
    os.path.join(os.path.expanduser("~"), "pulse"),
    os.path.join(os.getcwd(), "pulse"),
    os.path.join(os.getcwd(), "phonepe_pulse_data"),
]

def _find_pulse_root() -> str:
    """Return the first candidate path that actually contains pulse data."""
    for p in _CANDIDATE_PATHS:
        probe = os.path.join(p, "data", "aggregated", "transaction")
        if os.path.isdir(probe):
            return p
    return _CANDIDATE_PATHS[0]          # fallback – will show a friendly error

PULSE_ROOT = _find_pulse_root()


# ─────────────────────────────────────────────────────────────────────────────
#  1.  DATA EXTRACTION  (all bugs fixed)
# ─────────────────────────────────────────────────────────────────────────────
class PhonePeDataExtractor:
    def __init__(self, base_path: str = PULSE_ROOT):
        self.base_path = base_path

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_metric(raw) -> dict:
        """
        The PhonePe JSON stores 'metric' in two ways depending on the folder:
          • aggregated / top  →  {'count': N, 'amount': X}       (dict)
          • map               →  [{'count': N, 'amount': X}]     (list)
        This helper normalises both into a plain dict.
        """
        if isinstance(raw, list):
            return raw[0] if raw else {}
        if isinstance(raw, dict):
            return raw
        return {}

    def _iter_json(self, rel_path: str):
        """
        Walk  base_path/rel_path/<state>/<year>/<quarter>.json
        and yield (state_slug, year_int, quarter_int, parsed_dict).
        Skips files that cannot be parsed.
        """
        base = os.path.join(self.base_path, rel_path)
        if not os.path.isdir(base):
            return

        for state in sorted(os.listdir(base)):
            state_path = os.path.join(base, state)
            if not os.path.isdir(state_path):
                continue
            for year_str in sorted(os.listdir(state_path)):
                year_path = os.path.join(state_path, year_str)
                if not os.path.isdir(year_path):
                    continue
                try:
                    year = int(year_str)
                except ValueError:
                    continue
                for qfile in sorted(os.listdir(year_path)):
                    if not qfile.endswith(".json"):
                        continue
                    try:
                        quarter = int(qfile.replace(".json", ""))
                    except ValueError:
                        continue
                    fpath = os.path.join(year_path, qfile)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            yield state, year, quarter, json.load(fh)
                    except Exception:
                        pass   # skip corrupt file silently

    @staticmethod
    def _fmt_state(slug: str) -> str:
        """
        Convert a PhonePe state slug such as 'andaman-&-nicobar-islands'
        into the Title-Case form used in the GeoJSON: 'Andaman & Nicobar Islands'.
        """
        return slug.replace("-", " ").title().replace(" & ", " & ")

    # ── extractors ───────────────────────────────────────────────────────────

    def extract_aggregated_transaction(self) -> pd.DataFrame:
        rows = []
        rel = os.path.join("data", "aggregated", "transaction",
                           "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for txn in (jd.get("data") or {}).get("transactionData") or []:
                instruments = txn.get("paymentInstruments") or []
                pi = instruments[0] if instruments else {}
                rows.append({
                    "State":              self._fmt_state(state),
                    "Year":               year,
                    "Quarter":            quarter,
                    "Transaction_Type":   txn.get("name", ""),
                    "Transaction_Count":  int(pi.get("count", 0)),
                    "Transaction_Amount": float(pi.get("amount", 0)),
                })
        return pd.DataFrame(rows)

    def extract_aggregated_user(self) -> pd.DataFrame:
        rows = []
        rel = os.path.join("data", "aggregated", "user",
                           "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for u in (jd.get("data") or {}).get("usersByDevice") or []:
                rows.append({
                    "State":           self._fmt_state(state),
                    "Year":            year,
                    "Quarter":         quarter,
                    "User_Type":       u.get("brand", "Unknown"),
                    "User_Count":      int(u.get("count", 0)),
                    "User_Percentage": float(u.get("percentage", 0)),
                })
        return pd.DataFrame(rows)

    def extract_map_transaction(self) -> pd.DataFrame:
        """
        BUG FIX: hoverDataList items have  metric: [{'count':N,'amount':X}]
        (a list), not a dict.  Use _safe_metric() to normalise it.
        """
        rows = []
        rel = os.path.join("data", "map", "transaction",
                           "hover", "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for d in (jd.get("data") or {}).get("hoverDataList") or []:
                metric = self._safe_metric(d.get("metric"))   # ← THE FIX
                rows.append({
                    "State":              self._fmt_state(state),
                    "Year":               year,
                    "Quarter":            quarter,
                    "District":           d.get("name", ""),
                    "Transaction_Count":  int(metric.get("count", 0)),
                    "Transaction_Amount": float(metric.get("amount", 0)),
                })
        return pd.DataFrame(rows)

    def extract_map_user(self) -> pd.DataFrame:
        """
        BUG FIX: hoverData is a plain dict  {districtName: {registeredUsers, appOpens}}
        NOT a list.  Previous code tried to iterate it as a list.
        """
        rows = []
        rel = os.path.join("data", "map", "user",
                           "hover", "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            hover = (jd.get("data") or {}).get("hoverData") or {}
            if isinstance(hover, dict):
                for district, vals in hover.items():
                    rows.append({
                        "State":            self._fmt_state(state),
                        "Year":             year,
                        "Quarter":          quarter,
                        "District":         district,
                        "Registered_Users": int((vals or {}).get("registeredUsers", 0)),
                        "App_Opens":        int((vals or {}).get("appOpens", 0)),
                    })
        return pd.DataFrame(rows)

    def extract_top_transaction(self) -> pd.DataFrame:
        """
        BUG FIX: pincodes[].metric is also a list in some repo versions.
        Use _safe_metric() to handle both.
        """
        rows = []
        rel = os.path.join("data", "top", "transaction",
                           "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for p in (jd.get("data") or {}).get("pincodes") or []:
                metric = self._safe_metric(p.get("metric"))   # ← THE FIX
                rows.append({
                    "State":              self._fmt_state(state),
                    "Year":               year,
                    "Quarter":            quarter,
                    "Pincode":            str(p.get("name", p.get("entityName", ""))),
                    "Transaction_Count":  int(metric.get("count", 0)),
                    "Transaction_Amount": float(metric.get("amount", 0)),
                })
        return pd.DataFrame(rows)

    def extract_top_user(self) -> pd.DataFrame:
        rows = []
        rel = os.path.join("data", "top", "user",
                           "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for d in (jd.get("data") or {}).get("districts") or []:
                rows.append({
                    "State":            self._fmt_state(state),
                    "Year":             year,
                    "Quarter":          quarter,
                    "District":         d.get("name", ""),
                    "Registered_Users": int(d.get("registeredUsers", 0)),
                })
        return pd.DataFrame(rows)

    def extract_all(self) -> dict:
        results = {}
        steps = [
            ("aggregated_transaction", self.extract_aggregated_transaction),
            ("aggregated_user",        self.extract_aggregated_user),
            ("map_transaction",        self.extract_map_transaction),
            ("map_user",               self.extract_map_user),
            ("top_transaction",        self.extract_top_transaction),
            ("top_user",               self.extract_top_user),
        ]
        bar = st.progress(0, text="Starting extraction…")
        for i, (name, fn) in enumerate(steps):
            bar.progress(i / len(steps), text=f"Extracting {name}…")
            try:
                df = fn()
                results[name] = df
                st.caption(f"✅ {name}: {len(df):,} rows")
            except Exception as e:
                st.warning(f"⚠️ {name} extraction failed: {e}")
                results[name] = pd.DataFrame()
        bar.progress(1.0, text="Extraction complete!")
        return results


# ─────────────────────────────────────────────────────────────────────────────
#  2.  DATABASE MANAGER
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def run_cached_query(sql_query: str) -> pd.DataFrame:
    """Helper to run and cache SQLite queries globally."""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        return pd.read_sql_query(sql_query, conn)

class DatabaseManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _connect(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def create_tables(self):
        ddl = """
        CREATE TABLE IF NOT EXISTS aggregated_transaction (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            State TEXT, Year INTEGER, Quarter INTEGER,
            Transaction_Type TEXT, Transaction_Count INTEGER,
            Transaction_Amount REAL
        );
        CREATE TABLE IF NOT EXISTS aggregated_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            State TEXT, Year INTEGER, Quarter INTEGER,
            User_Type TEXT, User_Count INTEGER, User_Percentage REAL
        );
        CREATE TABLE IF NOT EXISTS map_transaction (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            State TEXT, Year INTEGER, Quarter INTEGER,
            District TEXT, Transaction_Count INTEGER, Transaction_Amount REAL
        );
        CREATE TABLE IF NOT EXISTS map_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            State TEXT, Year INTEGER, Quarter INTEGER,
            District TEXT, Registered_Users INTEGER, App_Opens INTEGER
        );
        CREATE TABLE IF NOT EXISTS top_transaction (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            State TEXT, Year INTEGER, Quarter INTEGER,
            Pincode TEXT, Transaction_Count INTEGER, Transaction_Amount REAL
        );
        CREATE TABLE IF NOT EXISTS top_user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            State TEXT, Year INTEGER, Quarter INTEGER,
            District TEXT, Registered_Users INTEGER
        );
        """
        with self._connect() as conn:
            conn.executescript(ddl)
        self.create_indexes()

    def create_indexes(self):
        """Create read-optimized indexes when the SQLite file is writable."""
        ddl = """
        CREATE INDEX IF NOT EXISTS idx_agg_txn_yqs
            ON aggregated_transaction (Year, Quarter, State);
        CREATE INDEX IF NOT EXISTS idx_agg_txn_type
            ON aggregated_transaction (Transaction_Type);
        CREATE INDEX IF NOT EXISTS idx_agg_user_yqs
            ON aggregated_user (Year, Quarter, State);
        CREATE INDEX IF NOT EXISTS idx_map_txn_yqsd
            ON map_transaction (Year, Quarter, State, District);
        CREATE INDEX IF NOT EXISTS idx_map_user_yqsd
            ON map_user (Year, Quarter, State, District);
        CREATE INDEX IF NOT EXISTS idx_top_txn_yqsp
            ON top_transaction (Year, Quarter, State, Pincode);
        """
        try:
            with self._connect() as conn:
                conn.executescript(ddl)
        except sqlite3.OperationalError:
            # Some classroom/shared folders mount the DB read-only; cached reads still work.
            return

    def insert_dataframe(self, table: str, df: pd.DataFrame):
        if df.empty:
            return
        with self._connect() as conn:
            df.to_sql(table, conn, if_exists="append", index=False)

    def query(self, sql: str) -> pd.DataFrame:
        return run_cached_query(sql)

    def table_row_count(self, table: str) -> int:
        try:
            with self._connect() as conn:
                res = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                return int(res[0]) if res else 0
        except Exception:
            return 0

    def clear_all(self):
        tables = ["aggregated_transaction", "aggregated_user",
                  "map_transaction", "map_user",
                  "top_transaction",  "top_user"]
        with self._connect() as conn:
            for t in tables:
                conn.execute(f"DELETE FROM {t}")


# ─────────────────────────────────────────────────────────────────────────────
#  3.  SQL QUERY HELPERS
# ─────────────────────────────────────────────────────────────────────────────
class Q:
    @staticmethod
    def top_states(state=None, year=None, quarter=None, limit=10):
        f = _where(state=state, year=year, quarter=quarter)
        return f"""
            SELECT State,
                   SUM(Transaction_Amount) AS Total_Amount,
                   SUM(Transaction_Count)  AS Total_Transactions
            FROM aggregated_transaction {f}
            GROUP BY State ORDER BY Total_Amount DESC LIMIT {limit}
        """

    @staticmethod
    def by_type(state=None, year=None, quarter=None):
        f = _where(state=state, year=year, quarter=quarter)
        return f"""
            SELECT Transaction_Type,
                   SUM(Transaction_Amount) AS Total_Amount,
                   SUM(Transaction_Count)  AS Total_Count
            FROM aggregated_transaction {f}
            GROUP BY Transaction_Type ORDER BY Total_Amount DESC
        """

    @staticmethod
    def yearly_trend(state=None):
        f = _where(state=state)
        return f"""
            SELECT Year, SUM(Transaction_Amount) AS Total_Amount,
                   SUM(Transaction_Count) AS Total_Count
            FROM aggregated_transaction {f}
            GROUP BY Year ORDER BY Year
        """

    @staticmethod
    def quarterly_trend(state=None, year=None):
        f = _where(state=state, year=year)
        return f"""
            SELECT Year, Quarter,
                   SUM(Transaction_Amount) AS Total_Amount,
                   SUM(Transaction_Count)  AS Total_Count
            FROM aggregated_transaction {f}
            GROUP BY Year, Quarter ORDER BY Year, Quarter
        """

    @staticmethod
    def top_districts(state=None, year=None, quarter=None, limit=15):
        f = _where(state=state, year=year, quarter=quarter)
        return f"""
            SELECT State, District,
                   SUM(Transaction_Amount) AS Total_Amount,
                   SUM(Transaction_Count)  AS Total_Count
            FROM map_transaction {f}
            GROUP BY State, District ORDER BY Total_Amount DESC LIMIT {limit}
        """

    @staticmethod
    def top_pincodes(state=None, year=None, quarter=None, limit=15):
        f = _where(state=state, year=year, quarter=quarter)
        return f"""
            SELECT State, Pincode,
                   SUM(Transaction_Amount) AS Total_Amount,
                   SUM(Transaction_Count)  AS Total_Transactions
            FROM top_transaction {f}
            GROUP BY State, Pincode ORDER BY Total_Amount DESC LIMIT {limit}
        """

    @staticmethod
    def user_by_device(state=None, year=None, quarter=None, limit=10):
        f = _where(state=state, year=year, quarter=quarter)
        return f"""
            SELECT User_Type,
                   SUM(User_Count) AS Total_Users,
                   AVG(User_Percentage) AS Avg_Pct
            FROM aggregated_user {f}
            GROUP BY User_Type ORDER BY Total_Users DESC LIMIT {limit}
        """

    @staticmethod
    def latest_device_period(state=None, year=None, quarter=None):
        clauses = []
        if year and quarter:
            clauses.append(
                f"(Year * 4 + Quarter) <= ({int(year)} * 4 + {int(quarter)})"
            )
        if state and state != "All":
            clauses.append(f"State = {_sql_quote(state)}")
        ceiling = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return f"""
            SELECT Year, Quarter
            FROM aggregated_user {ceiling}
            GROUP BY Year, Quarter
            ORDER BY Year DESC, Quarter DESC
            LIMIT 1
        """

    @staticmethod
    def user_by_state(state=None, year=None, quarter=None):
        f = _where(state=state, year=year, quarter=quarter)
        return f"""
            SELECT State,
                   SUM(Registered_Users) AS Registered_Users,
                   SUM(App_Opens)        AS App_Opens
            FROM map_user {f}
            GROUP BY State ORDER BY Registered_Users DESC
        """

    @staticmethod
    def growth_rate(state=None):
        f = _where(state=state)
        return f"""
            SELECT a.Year,
                   a.Total AS Current_Year,
                   b.Total AS Prev_Year,
                   ROUND(100.0*(a.Total-b.Total)/NULLIF(b.Total,0),2) AS Growth_Pct
            FROM (SELECT Year, SUM(Transaction_Amount) AS Total
                  FROM aggregated_transaction {f}
                  GROUP BY Year) a
            LEFT JOIN
                 (SELECT Year, SUM(Transaction_Amount) AS Total
                  FROM aggregated_transaction {f}
                  GROUP BY Year) b
            ON a.Year = b.Year+1
            ORDER BY a.Year
        """

    @staticmethod
    def state_period_summary(state=None):
        f = _where(state=state)
        return f"""
            SELECT Year, Quarter,
                   SUM(Transaction_Amount) AS Total_Amount,
                   SUM(Transaction_Count) AS Total_Count,
                   ROUND(
                       SUM(Transaction_Amount) /
                       NULLIF(SUM(Transaction_Count), 0), 2
                   ) AS Avg_Ticket
            FROM aggregated_transaction {f}
            GROUP BY Year, Quarter
            ORDER BY Year, Quarter
        """

    @staticmethod
    def state_compare(states, year=None, quarter=None):
        state_filter = ""
        clean_states = [s for s in states if s and s != "All"]
        if clean_states:
            quoted = ", ".join(_sql_quote(s) for s in clean_states)
            state_filter = f"State IN ({quoted})"
        f = _where(year=year, quarter=quarter, extra=state_filter)
        return f"""
            SELECT State,
                   SUM(Transaction_Amount) AS Total_Amount,
                   SUM(Transaction_Count) AS Total_Count,
                   ROUND(
                       SUM(Transaction_Amount) /
                       NULLIF(SUM(Transaction_Count), 0), 2
                   ) AS Avg_Ticket
            FROM aggregated_transaction {f}
            GROUP BY State
            ORDER BY Total_Amount DESC
        """

    @staticmethod
    def district_drilldown(state, year=None, quarter=None, limit=25):
        f = _where(state=state, year=year, quarter=quarter)
        return f"""
            SELECT District,
                   SUM(Transaction_Amount) AS Total_Amount,
                   SUM(Transaction_Count) AS Total_Count,
                   ROUND(
                       SUM(Transaction_Amount) /
                       NULLIF(SUM(Transaction_Count), 0), 2
                   ) AS Avg_Ticket
            FROM map_transaction {f}
            GROUP BY District
            ORDER BY Total_Amount DESC
            LIMIT {int(limit)}
        """

    @staticmethod
    def engagement_segments(year=None, quarter=None):
        f = _where(year=year, quarter=quarter)
        return f"""
            WITH txn AS (
                SELECT State,
                       SUM(Transaction_Amount) AS Total_Amount,
                       SUM(Transaction_Count) AS Total_Count
                FROM aggregated_transaction {f}
                GROUP BY State
            ),
            users AS (
                SELECT State,
                       SUM(Registered_Users) AS Registered_Users,
                       SUM(App_Opens) AS App_Opens
                FROM map_user {f}
                GROUP BY State
            )
            SELECT users.State,
                   users.Registered_Users,
                   users.App_Opens,
                   ROUND(
                       CAST(users.App_Opens AS REAL) /
                       NULLIF(users.Registered_Users, 0), 2
                   ) AS Engagement_Ratio,
                   txn.Total_Amount,
                   txn.Total_Count,
                   ROUND(
                       txn.Total_Amount / NULLIF(txn.Total_Count, 0), 2
                   ) AS Avg_Ticket
            FROM users
            JOIN txn ON txn.State = users.State
            ORDER BY Total_Amount DESC
        """


def _sql_quote(value) -> str:
    """Return a safe SQL string literal for trusted dashboard filters."""
    return "'" + str(value).replace("'", "''") + "'"


def _where(state=None, year=None, quarter=None, extra=None) -> str:
    clauses = []
    if state and state != "All":
        clauses.append(f"State = {_sql_quote(state)}")
    if year:
        clauses.append(f"Year = {int(year)}")
    if quarter:
        clauses.append(f"Quarter = {int(quarter)}")
    if extra:
        clauses.append(extra)
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""


# ─────────────────────────────────────────────────────────────────────────────
#  4.  FORMATTING HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def fmt_cr(v):
    """Format a rupee value as X.XX Cr."""
    if v is None or (hasattr(v, '__float__') and v != v):
        return "₹0"
    v = float(v)
    if v >= 1e7:
        return f"₹{v/1e7:.2f} Cr"
    if v >= 1e5:
        return f"₹{v/1e5:.2f} L"
    return f"₹{v:,.0f}"

def fmt_m(v):
    if v is None:
        return "0"
    v = float(v)
    if v >= 1e7:
        return f"{v/1e7:.2f} Cr"
    if v >= 1e6:
        return f"{v/1e6:.2f} M"
    if v >= 1e3:
        return f"{v/1e3:.1f} K"
    return f"{v:.0f}"


def add_period_label(df: pd.DataFrame) -> pd.DataFrame:
    """Add stable period labels used by trend charts."""
    if df.empty:
        return df
    out = df.copy()
    out["Period"] = out["Year"].astype(str) + " Q" + out["Quarter"].astype(str)
    return out


def add_customer_segments(df: pd.DataFrame) -> pd.DataFrame:
    """Add rule-based opportunity segments to the state summary table."""
    if df.empty:
        return df
    out = df.copy()
    spend_cutoff = out["Avg_Ticket"].quantile(0.75)
    engagement_cutoff = out["Engagement_Ratio"].median()

    def segment(row):
        if row["Avg_Ticket"] >= spend_cutoff and row["Engagement_Ratio"] >= engagement_cutoff:
            return "High value"
        if row["Engagement_Ratio"] >= engagement_cutoff:
            return "High engagement"
        if row["Avg_Ticket"] >= spend_cutoff:
            return "High spend, low engagement"
        return "Needs activation"

    out["Segment"] = out.apply(segment, axis=1)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  5.  STREAMLIT PAGE CONFIG & CSS & GRAPHICS HELPERS
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PhonePe Pulse Dashboard",
    page_icon="💜",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Outfit:wght@400;500;600;700;800&display=swap');
    
    /* Core Layout & Styling */
    html, body, [class*="css"] {
        font-family: 'Outfit', 'Plus Jakarta Sans', sans-serif;
    }
    
    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }
    
    /* Base Background of App */
    .stApp {
        background: radial-gradient(circle at 50% 0%, #16113a 0%, #0b071e 60%, #05030f 100%) !important;
        background-attachment: fixed !important;
    }
    
    /* Sidebar Styling */
    [data-testid="stSidebar"] {
        background-color: rgba(11, 7, 30, 0.95) !important;
        border-right: 1px solid rgba(139, 92, 246, 0.15) !important;
        backdrop-filter: blur(10px);
    }
    [data-testid="stSidebar"] * {
        color: #f5f3ff !important;
    }
    
    /* Glassmorphic Container Cards for plots */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: rgba(17, 12, 46, 0.45) !important;
        backdrop-filter: blur(16px) !important;
        -webkit-backdrop-filter: blur(16px) !important;
        border: 1px solid rgba(139, 92, 246, 0.18) !important;
        border-radius: 20px !important;
        box-shadow: 0 12px 30px 0 rgba(0, 0, 0, 0.4), inset 0 1px 0 0 rgba(255, 255, 255, 0.05) !important;
        padding: 24px !important;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
        margin-bottom: 15px;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {
        border-color: rgba(168, 85, 247, 0.45) !important;
        box-shadow: 0 12px 40px 0 rgba(168, 85, 247, 0.12), inset 0 1px 0 0 rgba(255, 255, 255, 0.1) !important;
        transform: translateY(-2px);
    }
    
    /* Segmented Control / Modern Custom Tabs Styling */
    div[data-testid="stSegmentedControl"] {
        background: rgba(13, 8, 30, 0.6) !important;
        border-radius: 16px !important;
        padding: 6px !important;
        border: 1px solid rgba(139, 92, 246, 0.2) !important;
        backdrop-filter: blur(10px);
        margin: 15px 0 25px 0 !important;
        display: flex;
        justify-content: center;
    }
    div[data-testid="stSegmentedControl"] button {
        background: transparent !important;
        border: none !important;
        color: #c084fc !important;
        border-radius: 12px !important;
        font-weight: 600 !important;
        padding: 10px 24px !important;
        transition: all 0.2s ease-in-out !important;
    }
    div[data-testid="stSegmentedControl"] button[aria-checked="true"] {
        background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%) !important;
        color: #ffffff !important;
        box-shadow: 0 4px 15px rgba(139, 92, 246, 0.4) !important;
    }
    
    /* Standard Selectboxes, Sliders and Inputs */
    .stSelectbox > div [data-baseweb="select"] {
        background: rgba(20, 15, 50, 0.6) !important;
        border: 1px solid rgba(139, 92, 246, 0.25) !important;
        border-radius: 12px !important;
        color: #f5f3ff !important;
    }
    .stSelectbox > label {
        color: #c084fc !important;
        font-weight: 600 !important;
        font-size: 14px;
        margin-bottom: 6px;
    }
    
    /* Custom KPI Cards */
    .kpi-container {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 20px;
        margin-bottom: 25px;
        width: 100%;
    }
    .kpi-card {
        background: linear-gradient(135deg, rgba(20, 15, 50, 0.65) 0%, rgba(13, 8, 30, 0.8) 100%);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(139, 92, 246, 0.2);
        border-radius: 18px;
        padding: 22px 24px;
        color: white;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.35);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        position: relative;
        overflow: hidden;
    }
    .kpi-card::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.05) 0%, transparent 100%);
        pointer-events: none;
    }
    .kpi-card:hover {
        transform: translateY(-4px);
        border-color: rgba(168, 85, 247, 0.5);
        box-shadow: 0 12px 30px rgba(168, 85, 247, 0.15);
    }
    .kpi-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 12px;
    }
    .kpi-title {
        color: #a78bfa;
        font-size: 13px;
        font-weight: 600;
        letter-spacing: 0.5px;
        text-transform: uppercase;
    }
    .kpi-icon {
        font-size: 18px;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 38px;
        height: 38px;
        border-radius: 12px;
        background: rgba(139, 92, 246, 0.15);
        box-shadow: 0 4px 10px rgba(0,0,0,0.15);
    }
    .kpi-value {
        font-size: 28px;
        font-weight: 800;
        color: #f5f3ff;
        line-height: 1.2;
        letter-spacing: -0.5px;
    }
    .kpi-delta-container {
        display: flex;
        align-items: center;
        margin-top: 10px;
        font-size: 12px;
    }
    .kpi-delta-val {
        font-weight: 700;
        color: #10b981;
        margin-left: 4px;
    }
    .kpi-delta-lbl {
        color: #6b7280;
        margin-left: 6px;
    }
    
    /* Header Gradient Text and Styling */
    .header-container {
        text-align: center;
        padding: 24px 0 10px 0;
        position: relative;
    }
    .header-title {
        font-family: 'Outfit', sans-serif;
        font-size: 42px;
        font-weight: 900;
        background: linear-gradient(135deg, #a78bfa 10%, #ec4899 50%, #6366f1 90%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 8px;
        letter-spacing: -1px;
        animation: pulse-glow 6s infinite alternate;
    }
    .header-subtitle {
        color: #9ca3af;
        font-size: 16px;
        font-weight: 500;
        letter-spacing: 0.5px;
    }
    
    /* Custom Insights Cards */
    .insight-card {
        background: linear-gradient(135deg, rgba(20, 15, 50, 0.6) 0%, rgba(13, 8, 30, 0.7) 100%);
        border: 1px solid rgba(139, 92, 246, 0.2);
        border-left: 5px solid #8b5cf6;
        border-radius: 14px;
        padding: 16px 20px;
        margin: 12px 0;
        color: #e9d5ff;
        font-size: 14.5px;
        line-height: 1.5;
        box-shadow: 0 6px 20px rgba(0,0,0,0.2);
        transition: all 0.2s ease;
    }
    .insight-card:hover {
        border-left-width: 7px;
        transform: translateX(3px);
        background: linear-gradient(135deg, rgba(24, 18, 60, 0.7) 0%, rgba(16, 10, 36, 0.8) 100%);
    }

    .mini-note {
        color: #c4b5fd;
        font-size: 13px;
        line-height: 1.45;
        margin-top: -6px;
        margin-bottom: 12px;
    }

    .segment-pill {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: 4px 10px;
        margin: 2px 4px 2px 0;
        font-size: 12px;
        font-weight: 700;
        color: #f8fafc;
        background: rgba(139, 92, 246, 0.18);
        border: 1px solid rgba(167, 139, 250, 0.3);
    }

    /* Keyframes */
    @keyframes pulse-glow {
        0% { filter: drop-shadow(0 0 2px rgba(167, 139, 250, 0.2)); }
        100% { filter: drop-shadow(0 0 15px rgba(236, 72, 153, 0.4)); }
    }
    
    /* Animations */
    .fade-in {
        animation: fadeIn 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    }
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
    }
</style>
""", unsafe_allow_html=True)


def render_kpi_cards(cards: list):
    """Render a horizontal set of custom styled glassmorphic KPI cards."""
    cards_html = []
    for c in cards:
        delta_html = ""
        if c.get("delta"):
            delta_html = (
                f'<div class="kpi-delta-container">'
                f'<span class="kpi-delta-val" style="color: {c.get("delta_color", "#10b981")}">{c["delta"]}</span>'
                f'<span class="kpi-delta-lbl">{c.get("delta_lbl", "")}</span>'
                f'</div>'
            )
        card_html = (
            f'<div class="kpi-card" style="border-left: 4px solid {c["accent"]};">'
            f'<div class="kpi-header">'
            f'<span class="kpi-title">{c["title"]}</span>'
            f'<span class="kpi-icon" style="background: {c["accent"]}20; color: {c["accent"]};">{c["icon"]}</span>'
            f'</div>'
            f'<div>'
            f'<div class="kpi-value">{c["value"]}</div>'
            f'{delta_html}'
            f'</div>'
            f'</div>'
        )
        cards_html.append(card_html)
        
    html = f'<div class="kpi-container fade-in">{"".join(cards_html)}</div>'
    st.html(html)


def customize_plotly_fig(fig):
    """Apply unified styling configuration to any Plotly figure for a premium cyber-dark look."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_family="Outfit, sans-serif",
        font_color="#f5f3ff",
        margin=dict(t=40, b=40, l=45, r=40),
        hoverlabel=dict(
            bgcolor="rgba(11, 7, 30, 0.95)",
            bordercolor="rgba(167, 139, 250, 0.4)",
            font_size=13,
            font_family="Outfit, sans-serif",
            font_color="#f5f3ff",
        ),
    )
    # Check if axes are present and style them
    if hasattr(fig, "layout") and fig.layout:
        if "xaxis" in fig.layout and fig.layout.xaxis:
            fig.update_xaxes(
                gridcolor="rgba(167, 139, 250, 0.08)",
                zerolinecolor="rgba(167, 139, 250, 0.15)",
                tickfont=dict(color="#c084fc"),
                title_font=dict(color="#a78bfa")
            )
        if "yaxis" in fig.layout and fig.layout.yaxis:
            fig.update_yaxes(
                gridcolor="rgba(167, 139, 250, 0.08)",
                zerolinecolor="rgba(167, 139, 250, 0.15)",
                tickfont=dict(color="#c084fc"),
                title_font=dict(color="#a78bfa")
            )
        if "coloraxis" in fig.layout and fig.layout.coloraxis:
            fig.update_layout(
                coloraxis_colorbar=dict(
                    tickfont=dict(color="#a78bfa"),
                    title_font=dict(color="#c084fc")
                )
            )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
#  6.  DATA LOADING  (with re-extract button)
# ─────────────────────────────────────────────────────────────────────────────
def load_db() -> DatabaseManager:
    """Load (or build) the SQLite database and return a DatabaseManager."""
    db = DatabaseManager(DB_PATH)
    db.create_tables()

    if db.table_row_count("aggregated_transaction") == 0:
        if not os.path.isdir(PULSE_ROOT):
            return db   # caller will show a friendly error

        with st.status("Building database from PhonePe Pulse data…", expanded=True):
            extractor = PhonePeDataExtractor(PULSE_ROOT)
            data = extractor.extract_all()

        with st.status("Saving to SQLite…", expanded=False):
            for table_name, df in data.items():
                if not df.empty:
                    db.insert_dataframe(table_name, df)
                    st.write(f"✅ {table_name}: {len(df):,} rows saved")

    return db


# ─────────────────────────────────────────────────────────────────────────────
#  7.  SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style='padding:20px 0 12px;'>
        <h2 style='color:#f5f3ff;font-size:22px;font-weight:800;margin:0;'>💜 PhonePe Pulse</h2>
        <p style='color:#a78bfa;font-size:12px;margin:4px 0 0;'>India Payment Intelligence</p>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("---")

    if st.button("🔄 Re-extract Data", use_container_width=True):
        db_tmp = DatabaseManager(DB_PATH)
        db_tmp.create_tables()
        db_tmp.clear_all()
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")

db = load_db()

# Check DB health
if db.table_row_count("aggregated_transaction") == 0:
    st.error(
        f"**No transaction data found.**\n\n"
        f"The PhonePe Pulse data folder was not found at:\n`{PULSE_ROOT}`\n\n"
        "Please:\n"
        "1. Clone the repo: `git clone https://github.com/PhonePe/pulse.git`\n"
        "2. Update `PULSE_ROOT` at the top of `app.py` to point to the cloned folder.\n"
        "3. Click **Re-extract Data** in the sidebar."
    )
    st.stop()

# Sidebar filters – populated from actual DB
with st.sidebar:
    years = db.query("SELECT DISTINCT Year FROM aggregated_transaction ORDER BY Year")["Year"].tolist()
    selected_year = st.selectbox("📅 Year", years, index=len(years) - 1)

    quarters = db.query(
        f"SELECT DISTINCT Quarter FROM aggregated_transaction WHERE Year={selected_year} ORDER BY Quarter"
    )["Quarter"].tolist()
    selected_quarter = st.selectbox("📆 Quarter", quarters)

    states_list = db.query(
        "SELECT DISTINCT State FROM aggregated_transaction ORDER BY State"
    )["State"].tolist()
    selected_state = st.selectbox("📍 State", ["All"] + states_list)

    st.markdown("---")
    st.caption("Data: github.com/PhonePe/pulse")
    st.caption(f"DB rows: {db.table_row_count('aggregated_transaction'):,}")


# ─────────────────────────────────────────────────────────────────────────────
#  8.  MAIN DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <div class="header-container fade-in">
        <h1 class="header-title">💜 PHONEPE PULSE DASHBOARD</h1>
        <p class="header-subtitle">
            India Payment Intelligence & Analytics Dashboard · Q{selected_quarter} {selected_year}
            {" · " + selected_state if selected_state != "All" else ""}
    </div>
    """,
    unsafe_allow_html=True,
)
st.markdown("<hr style='border-color:rgba(167,139,250,0.2); margin-top: 5px; margin-bottom: 25px;'>", unsafe_allow_html=True)

# Modern horizontal selector to act as tabs (enabling lazy-rendering)
active_tab = st.segmented_control(
    "Navigation Menu",
    options=[
        "📊 Transactions",
        "👥 Users & Devices",
        "🗺️ Geo Explorer",
        "💡 Insights",
        "State Lab",
    ],
    default="📊 Transactions",
    label_visibility="collapsed"

)


# ══════════════════════════════════════════════════════════════════════════════
#  VIEW 1 — TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════════
if active_tab == "📊 Transactions":
    st.markdown("<div class='fade-in'>", unsafe_allow_html=True)
    # KPIs
    amt_q = Q.top_states(
        state=selected_state,
        year=selected_year,
        quarter=selected_quarter,
        limit=1000,
    )
    all_states_df = db.query(amt_q)
    total_amount = all_states_df["Total_Amount"].sum() if not all_states_df.empty else 0
    total_txn    = all_states_df["Total_Transactions"].sum() if not all_states_df.empty else 0
    avg_val      = total_amount / total_txn if total_txn else 0
    top_state_row = all_states_df.iloc[0] if not all_states_df.empty else None

    # Custom premium glassmorphic KPI cards
    render_kpi_cards([
        {"title": "Total Value", "value": fmt_cr(total_amount), "icon": "💰", "accent": "#8b5cf6"},
        {"title": "Transactions", "value": fmt_m(total_txn), "icon": "🔄", "accent": "#6366f1"},
        {"title": "Avg Transaction", "value": fmt_cr(avg_val), "icon": "💵", "accent": "#06b6d4"},
        {
            "title": "Top State",
            "value": top_state_row["State"] if top_state_row is not None else "—",
            "icon": "🏆",
            "accent": "#ec4899",
            "delta": fmt_cr(top_state_row["Total_Amount"]) if top_state_row is not None else "",
            "delta_color": "#c084fc",
            "delta_lbl": "Contribution"
        }
    ])

    st.markdown("<br/>", unsafe_allow_html=True)

    # Choropleth India map
    st.subheader("🗺️ Transaction Volume by State")
    map_df = db.query(Q.top_states(
        state=selected_state,
        year=selected_year,
        quarter=selected_quarter,
        limit=100,
    ))
    if not map_df.empty:
        fig_map = px.choropleth(
            map_df,
            geojson=GEOJSON_URL,
            featureidkey="properties.ST_NM",
            locations="State",
            color="Total_Amount",
            color_continuous_scale=["#1a0c30", "#5b21b6", "#8b5cf6", "#c084fc", "#f3e8ff"],
            hover_name="State",
            hover_data={"Total_Amount": ":,.0f", "Total_Transactions": ":,.0f"},
            labels={"Total_Amount": "Amount (₹)", "Total_Transactions": "Transactions"},
        )
        fig_map.update_geos(fitbounds="locations", visible=False, bgcolor="rgba(0,0,0,0)")
        fig_map.update_traces(marker_line_color="rgba(167, 139, 250, 0.45)", marker_line_width=1.0)
        customize_plotly_fig(fig_map)
        fig_map.update_layout(height=520, margin=dict(t=10, b=0, l=0, r=0))
        st.plotly_chart(fig_map, use_container_width=True)

    st.markdown("---")

    # Transaction type + Yearly trend
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📊 Transaction by Type")
        type_df = db.query(Q.by_type(
            state=selected_state,
            year=selected_year,
            quarter=selected_quarter,
        ))
        if not type_df.empty:
            fig = px.pie(
                type_df, values="Total_Amount", names="Transaction_Type", hole=0.55,
                color_discrete_sequence=["#6366f1", "#8b5cf6", "#a855f7", "#ec4899", "#d946ef"],
            )
            fig.update_traces(textposition="outside", textinfo="percent+label", textfont_size=12,
                              marker=dict(line=dict(color='rgba(11, 7, 30, 0.8)', width=2)))
            customize_plotly_fig(fig)
            fig.update_layout(showlegend=False, margin=dict(t=20, b=20, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("📈 Yearly Transaction Trend")
        yearly_df = db.query(Q.yearly_trend(state=selected_state))
        if not yearly_df.empty:
            fig = px.bar(
                yearly_df, x="Year", y="Total_Amount",
                color="Total_Amount",
                color_continuous_scale=["#3b0764", "#6b21a8", "#a855f7", "#c084fc"],
                text_auto=".2s",
            )
            fig.update_traces(textfont_color="#fff", marker_line_color="rgba(167,139,250,0.2)", marker_line_width=1)
            customize_plotly_fig(fig)
            fig.update_layout(
                showlegend=False,
                coloraxis_showscale=False,
                xaxis=dict(showgrid=False, tickmode="linear"),
                margin=dict(t=20)
            )
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # Top states + quarterly trend
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🏅 Top 10 States")
        top_states_df = db.query(Q.top_states(
            state=selected_state,
            year=selected_year,
            quarter=selected_quarter,
        ))
        if not top_states_df.empty:
            fig = px.bar(
                top_states_df, x="Total_Amount", y="State", orientation="h",
                color="Total_Amount",
                color_continuous_scale=["#3b0764", "#6b21a8", "#a855f7", "#c084fc"],
                text_auto=".2s",
            )
            fig.update_traces(textfont_color="#fff", marker_line_color="rgba(167,139,250,0.2)", marker_line_width=1)
            customize_plotly_fig(fig)
            fig.update_layout(
                showlegend=False, coloraxis_showscale=False,
                yaxis=dict(autorange="reversed", showgrid=False),
                height=420, margin=dict(t=20)
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("📉 Quarterly Trend")
        q_trend = db.query(Q.quarterly_trend(state=selected_state))
        if not q_trend.empty:
            q_trend["Period"] = q_trend["Year"].astype(str) + " Q" + q_trend["Quarter"].astype(str)
            fig = px.line(
                q_trend, x="Period", y="Total_Amount", markers=True,
                color_discrete_sequence=["#a855f7"],
            )
            fig.update_traces(line_width=3, marker_size=8, marker_color="#ec4899", marker_line_color="#fff", marker_line_width=1.5)
            customize_plotly_fig(fig)
            fig.update_layout(
                xaxis=dict(showgrid=False, tickangle=-45),
                height=420, margin=dict(t=20)
            )
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # Top districts + pincodes
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🏙️ Top Districts")
        dist_df = db.query(Q.top_districts(
            state=selected_state, year=selected_year, quarter=selected_quarter
        ))
        if not dist_df.empty:
            fig = px.bar(
                dist_df, x="Total_Amount", y="District", orientation="h",
                color="Total_Amount",
                color_continuous_scale=["#3b0764", "#6b21a8", "#a855f7", "#c084fc"],
                text_auto=".2s",
            )
            fig.update_traces(textfont_color="#fff", marker_line_color="rgba(167,139,250,0.2)", marker_line_width=1)
            customize_plotly_fig(fig)
            fig.update_layout(
                showlegend=False, coloraxis_showscale=False,
                yaxis=dict(autorange="reversed", showgrid=False),
                height=500, margin=dict(t=20)
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("📍 Top Pincodes")
        pin_df = db.query(Q.top_pincodes(
            state=selected_state,
            year=selected_year,
            quarter=selected_quarter,
        ))
        if not pin_df.empty:
            fig = px.scatter(
                pin_df, x="Total_Transactions", y="Total_Amount",
                size="Total_Amount", color="State", hover_name="Pincode",
                size_max=50,
                color_discrete_sequence=px.colors.qualitative.Bold,
            )
            customize_plotly_fig(fig)
            fig.update_layout(
                height=500, margin=dict(t=20)
            )
            st.plotly_chart(fig, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  VIEW 2 — USERS & DEVICES
# ══════════════════════════════════════════════════════════════════════════════
elif active_tab == "👥 Users & Devices":
    st.markdown("<div class='fade-in'>", unsafe_allow_html=True)
    user_state = db.query(Q.user_by_state(
        state=selected_state,
        year=selected_year,
        quarter=selected_quarter,
    ))
    device_period = db.query(Q.latest_device_period(
        state=selected_state,
        year=selected_year,
        quarter=selected_quarter,
    ))
    if device_period.empty:
        device_year, device_quarter = selected_year, selected_quarter
    else:
        device_year = int(device_period.iloc[0]["Year"])
        device_quarter = int(device_period.iloc[0]["Quarter"])
    user_df = db.query(Q.user_by_device(
        state=selected_state,
        year=device_year,
        quarter=device_quarter,
    ))

    # KPIs
    total_users  = user_state["Registered_Users"].sum() if not user_state.empty else 0
    total_brands = len(user_df) if not user_df.empty else 0
    top_brand    = user_df.iloc[0]["User_Type"] if not user_df.empty else "—"
    top_brand_pct = (user_df.iloc[0]["Avg_Pct"] * 100) if not user_df.empty else 0

    render_kpi_cards([
        {"title": "Registered Users", "value": fmt_m(total_users), "icon": "👤", "accent": "#8b5cf6"},
        {"title": "Device Brands", "value": str(total_brands), "icon": "📱", "accent": "#6366f1"},
        {"title": "Top Brand", "value": top_brand, "icon": "🥇", "accent": "#ec4899"},
        {"title": "Top Brand Share", "value": f"{top_brand_pct:.1f}%", "icon": "📊", "accent": "#06b6d4"}
    ])

    st.markdown("<br/>", unsafe_allow_html=True)
    if (device_year, device_quarter) != (selected_year, selected_quarter):
        st.caption(
            f"Device-brand data is available through Q{device_quarter} {device_year}; "
            f"registered-user maps use Q{selected_quarter} {selected_year}."
        )

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📱 Device Brand Distribution")
        if not user_df.empty:
            fig = px.bar(
                user_df.head(10), x="Total_Users", y="User_Type", orientation="h",
                color="Total_Users",
                color_continuous_scale=["#3b0764", "#6b21a8", "#a855f7", "#c084fc"],
                text_auto=".2s",
            )
            fig.update_traces(textfont_color="#fff", marker_line_color="rgba(167,139,250,0.2)", marker_line_width=1)
            customize_plotly_fig(fig)
            fig.update_layout(
                showlegend=False, coloraxis_showscale=False,
                yaxis=dict(autorange="reversed", showgrid=False),
                height=450, margin=dict(t=20)
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("📱 Brand Market Share (Pie)")
        if not user_df.empty:
            fig = px.pie(
                user_df.head(8), values="Total_Users", names="User_Type", hole=0.55,
                color_discrete_sequence=["#6366f1", "#8b5cf6", "#a855f7", "#ec4899", "#d946ef", "#06b6d4", "#0d9488"],
            )
            fig.update_traces(textposition="outside", textinfo="percent+label",
                              marker=dict(line=dict(color='rgba(11, 7, 30, 0.8)', width=2)))
            customize_plotly_fig(fig)
            fig.update_layout(
                showlegend=False, margin=dict(t=20, b=40, l=20, r=20)
            )
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # Users by state
    st.subheader("🗺️ Registered Users by State")
    if not user_state.empty:
        fig = px.choropleth(
            user_state,
            geojson=GEOJSON_URL,
            featureidkey="properties.ST_NM",
            locations="State",
            color="Registered_Users",
            color_continuous_scale=["#1a0c30", "#5b21b6", "#8b5cf6", "#c084fc", "#f3e8ff"],
            hover_name="State",
            hover_data={"Registered_Users": ":,.0f", "App_Opens": ":,.0f"},
            labels={"Registered_Users": "Users", "App_Opens": "App Opens"},
        )
        fig.update_geos(fitbounds="locations", visible=False, bgcolor="rgba(0,0,0,0)")
        fig.update_traces(marker_line_color="rgba(167, 139, 250, 0.45)", marker_line_width=1.0)
        customize_plotly_fig(fig)
        fig.update_layout(
            height=500, margin=dict(t=10, b=0, l=0, r=0)
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📊 Top States by Registered Users")
        if not user_state.empty:
            fig = px.bar(
                user_state.head(10), x="Registered_Users", y="State", orientation="h",
                color="Registered_Users",
                color_continuous_scale=["#3b0764", "#6b21a8", "#a855f7", "#c084fc"],
                text_auto=".2s",
            )
            fig.update_traces(textfont_color="#fff", marker_line_color="rgba(167,139,250,0.2)", marker_line_width=1)
            customize_plotly_fig(fig)
            fig.update_layout(
                showlegend=False, coloraxis_showscale=False,
                yaxis=dict(autorange="reversed", showgrid=False),
                height=420, margin=dict(t=20)
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("📈 App Opens vs Registered Users")
        if not user_state.empty:
            fig = px.scatter(
                user_state, x="Registered_Users", y="App_Opens",
                hover_name="State",
                size="Registered_Users", size_max=40,
                color="Registered_Users",
                color_continuous_scale=["#3b0764", "#6b21a8", "#a855f7", "#c084fc"],
            )
            customize_plotly_fig(fig)
            fig.update_layout(
                showlegend=False, coloraxis_showscale=False,
                height=420, margin=dict(t=20)
            )
            st.plotly_chart(fig, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  VIEW 3 — GEO EXPLORER
# ══════════════════════════════════════════════════════════════════════════════
elif active_tab == "🗺️ Geo Explorer":
    st.markdown("<div class='fade-in'>", unsafe_allow_html=True)
    st.subheader("🗺️ Interactive India Map Explorer")
    metric_choice = st.radio(
        "Select Metric",
        ["Transaction Amount", "Transaction Count", "Registered Users"],
        horizontal=True,
    )
    all_years_check = st.checkbox("Show all years (ignore year/quarter filter)", value=False)

    if metric_choice in ["Transaction Amount", "Transaction Count"]:
        if all_years_check:
            geo_data = db.query(Q.top_states(state=selected_state, limit=100))
        else:
            geo_data = db.query(Q.top_states(
                state=selected_state,
                year=selected_year,
                quarter=selected_quarter,
                limit=100,
            ))
        color_col = "Total_Amount" if "Amount" in metric_choice else "Total_Transactions"
        label     = "Amount (₹)" if "Amount" in metric_choice else "Transactions"
    else:
        if all_years_check:
            geo_data = db.query(Q.user_by_state(state=selected_state))
        else:
            geo_data = db.query(Q.user_by_state(
                state=selected_state,
                year=selected_year,
                quarter=selected_quarter,
            ))
        color_col = "Registered_Users"
        label     = "Users"

    if not geo_data.empty and color_col in geo_data.columns:
        fig = px.choropleth(
            geo_data,
            geojson=GEOJSON_URL,
            featureidkey="properties.ST_NM",
            locations="State",
            color=color_col,
            color_continuous_scale=["#1a0c30", "#5b21b6", "#8b5cf6", "#c084fc", "#f3e8ff"],
            hover_name="State",
            labels={color_col: label},
        )
        fig.update_geos(fitbounds="locations", visible=False, bgcolor="rgba(0,0,0,0)")
        fig.update_traces(marker_line_color="rgba(167, 139, 250, 0.45)", marker_line_width=1.0)
        customize_plotly_fig(fig)
        fig.update_layout(
            height=580, margin=dict(t=10, b=0, l=0, r=0)
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")
        st.subheader("📋 State Rankings")
        ranked = geo_data.sort_values(color_col, ascending=False).reset_index(drop=True)
        ranked.index += 1
        st.dataframe(ranked.head(20), use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  VIEW 4 — BUSINESS INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════
elif active_tab == "💡 Insights":
    st.markdown("<div class='fade-in'>", unsafe_allow_html=True)
    st.subheader("💡 Automated Business Insights")

    top_states_data = db.query(Q.top_states(
        state=selected_state,
        year=selected_year,
        quarter=selected_quarter,
    ))
    type_data       = db.query(Q.by_type(
        state=selected_state,
        year=selected_year,
        quarter=selected_quarter,
    ))
    yearly_data     = db.query(Q.yearly_trend(state=selected_state))
    growth_data     = db.query(Q.growth_rate(state=selected_state))
    insight_device_period = db.query(Q.latest_device_period(
        state=selected_state,
        year=selected_year,
        quarter=selected_quarter,
    ))
    if insight_device_period.empty:
        insight_device_year = selected_year
        insight_device_quarter = selected_quarter
    else:
        insight_device_year = int(insight_device_period.iloc[0]["Year"])
        insight_device_quarter = int(insight_device_period.iloc[0]["Quarter"])
    user_data       = db.query(Q.user_by_device(
        state=selected_state,
        year=insight_device_year,
        quarter=insight_device_quarter,
    ))
    district_data   = db.query(Q.top_districts(year=selected_year, quarter=selected_quarter))

    # Calculate values
    all_states_df = db.query(Q.top_states(
        state=selected_state,
        year=selected_year,
        quarter=selected_quarter,
        limit=1000,
    ))
    total_amount = all_states_df["Total_Amount"].sum() if not all_states_df.empty else 0

    def insight(text):
        st.markdown(f"<div class='insight-card'>{text}</div>", unsafe_allow_html=True)

    # Insight 1 – market concentration
    if not top_states_data.empty and total_amount > 0:
        top3_sum  = top_states_data.head(3)["Total_Amount"].sum()
        top3_pct  = 100 * top3_sum / total_amount
        top_state = top_states_data.iloc[0]["State"]
        top_pct   = 100 * top_states_data.iloc[0]["Total_Amount"] / total_amount
        insight(
            f"🏆 <strong>{top_state}</strong> alone contributes "
            f"<strong>{top_pct:.1f}%</strong> of all transaction value in "
            f"Q{selected_quarter} {selected_year}. "
            f"The top 3 states together capture <strong>{top3_pct:.1f}%</strong> — "
            f"indicating high geographic concentration."
        )

    # Insight 2 – payment type
    if not type_data.empty and total_amount > 0:
        top_type     = type_data.iloc[0]["Transaction_Type"]
        type_share   = 100 * type_data.iloc[0]["Total_Amount"] / total_amount
        insight(
            f"💳 <strong>{top_type}</strong> is the leading payment category at "
            f"<strong>{type_share:.1f}%</strong> of total value. "
            f"Targeted promotions on this channel could yield the highest incremental volume."
        )

    # Insight 3 – YoY growth
    if not growth_data.empty:
        latest = growth_data.dropna(subset=["Growth_Pct"])
        if not latest.empty:
            g = latest.iloc[-1]["Growth_Pct"]
            yr = int(latest.iloc[-1]["Year"])
            color = "🟢" if g >= 0 else "🔴"
            insight(
                f"{color} Year-on-year growth in {yr}: "
                f"<strong>{'+' if g>=0 else ''}{g:.1f}%</strong>. "
                + ("Strong positive momentum across digital payments." if g >= 0
                   else "Investigate market factors, competition, or seasonal effects.")
            )

    # Insight 4 – district opportunity
    if not district_data.empty:
        top_district = district_data.iloc[0]
        insight(
            f"🏙️ <strong>{top_district['District']}</strong> "
            f"({top_district['State']}) is the highest-transaction district "
            f"with {fmt_cr(top_district['Total_Amount'])} in Q{selected_quarter} {selected_year}. "
            f"Expanding merchant networks here offers immediate ROI."
        )

    # Insight 5 – device strategy
    if not user_data.empty:
        top_dev  = user_data.iloc[0]["User_Type"]
        dev_pct  = user_data.iloc[0]["Avg_Pct"]
        insight(
            f"📱 <strong>{top_dev}</strong> users represent an average of "
            f"<strong>{dev_pct:.1f}%</strong> of the user base. "
            f"Ensure the PhonePe app is optimised for this hardware to maximise engagement."
        )

    # Insight 6 – silent users
    user_state_df = db.query(Q.user_by_state(
        state=selected_state,
        year=selected_year,
        quarter=selected_quarter,
    ))
    if not user_state_df.empty:
        total_reg   = user_state_df["Registered_Users"].sum()
        total_opens = user_state_df["App_Opens"].sum()
        if total_reg > 0:
            opens_ratio = total_opens / total_reg
            insight(
                f"👤 App opens-to-registered-user ratio: "
                f"<strong>{opens_ratio:.1f}×</strong>. "
                + ("High engagement — users are returning frequently." if opens_ratio >= 3
                   else "Consider re-engagement campaigns (push notifications, cashback) to activate dormant users.")
            )

    st.markdown("---")
    st.subheader("📊 Growth Rate by Year")
    if not growth_data.empty:
        fig = go.Figure()
        fig.add_bar(x=growth_data["Year"], y=growth_data["Current_Year"],
                    name="Transaction Amount", marker_color="#8b5cf6")
        valid_g = growth_data.dropna(subset=["Growth_Pct"])
        if not valid_g.empty:
            fig.add_scatter(x=valid_g["Year"], y=valid_g["Growth_Pct"],
                            name="YoY Growth %", yaxis="y2",
                            line=dict(color="#10b981", width=3),
                            marker=dict(size=8, color="#10b981", symbol="circle"))
        customize_plotly_fig(fig)
        fig.update_layout(
            yaxis=dict(title="Amount (₹)", color="#f5f3ff"),
            yaxis2=dict(title="Growth %", overlaying="y", side="right",
                        color="#10b981", showgrid=False),
            legend=dict(bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(showgrid=False, tickmode="linear"),
            height=420,
        )
        st.plotly_chart(fig, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
#  FOOTER
# ─────────────────────────────────────────────────────────────────────────────
elif active_tab == "State Lab":
    st.markdown("<div class='fade-in'>", unsafe_allow_html=True)
    st.subheader("State Lab: compare, drill down, and spot opportunities")
    st.markdown(
        "<p class='mini-note'>Compare states over time, inspect district-level "
        "performance, and classify states by engagement and spend.</p>",
        unsafe_allow_html=True,
    )

    state_defaults = [selected_state] if selected_state != "All" else states_list[:3]
    state_pick = st.multiselect(
        "Compare states",
        states_list,
        default=state_defaults,
        max_selections=6,
    )
    if not state_pick:
        st.info("Select at least one state to start the comparison.")
        st.stop()

    trend_tabs = st.tabs(["Trend", "Quarter snapshot", "District drilldown", "Segments"])

    with trend_tabs[0]:
        metric_choice = st.radio(
            "Trend metric",
            ["Total_Amount", "Total_Count", "Avg_Ticket"],
            horizontal=True,
            key="state_lab_metric",
        )
        trend_frames = []
        for state in state_pick:
            state_df = db.query(Q.state_period_summary(state=state))
            if not state_df.empty:
                state_df["State"] = state
                trend_frames.append(state_df)

        if trend_frames:
            trend_df = add_period_label(pd.concat(trend_frames, ignore_index=True))
            fig = px.line(
                trend_df,
                x="Period",
                y=metric_choice,
                color="State",
                markers=True,
                line_shape="spline",
                color_discrete_sequence=px.colors.qualitative.Bold,
            )
            customize_plotly_fig(fig)
            fig.update_layout(height=480, xaxis=dict(tickangle=-45, showgrid=False))
            st.plotly_chart(fig, use_container_width=True)
            st.download_button(
                "Download trend data",
                trend_df.to_csv(index=False).encode("utf-8"),
                "state_lab_trends.csv",
                "text/csv",
            )
        else:
            st.warning("No trend data is available for the selected states.")

    with trend_tabs[1]:
        compare_df = db.query(
            Q.state_compare(state_pick, year=selected_year, quarter=selected_quarter)
        )
        if compare_df.empty:
            st.warning("No comparison data is available for this quarter.")
        else:
            total_share = compare_df["Total_Amount"].sum()
            compare_df["Share_Pct"] = (
                100 * compare_df["Total_Amount"] / total_share if total_share else 0
            )
            c1, c2 = st.columns(2)
            with c1:
                fig = px.bar(
                    compare_df.sort_values("Total_Amount"),
                    x="Total_Amount",
                    y="State",
                    orientation="h",
                    color="Share_Pct",
                    color_continuous_scale=["#312e81", "#8b5cf6", "#f0abfc"],
                    text="Share_Pct",
                )
                fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
                customize_plotly_fig(fig)
                fig.update_layout(height=420, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig = px.scatter(
                    compare_df,
                    x="Total_Count",
                    y="Avg_Ticket",
                    size="Total_Amount",
                    color="State",
                    hover_name="State",
                    size_max=55,
                    color_discrete_sequence=px.colors.qualitative.Bold,
                )
                customize_plotly_fig(fig)
                fig.update_layout(height=420)
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(compare_df, use_container_width=True, hide_index=True)

    with trend_tabs[2]:
        drill_state = st.selectbox(
            "State for district drilldown",
            state_pick,
            key="state_lab_drill_state",
        )
        district_df = db.query(
            Q.district_drilldown(
                state=drill_state,
                year=selected_year,
                quarter=selected_quarter,
                limit=25,
            )
        )
        if district_df.empty:
            st.warning("No district data is available for this selection.")
        else:
            fig = px.bar(
                district_df.sort_values("Total_Amount"),
                x="Total_Amount",
                y="District",
                orientation="h",
                color="Avg_Ticket",
                color_continuous_scale=["#312e81", "#8b5cf6", "#f0abfc"],
                hover_data={"Total_Count": ":,", "Avg_Ticket": ":,.2f"},
            )
            customize_plotly_fig(fig)
            fig.update_layout(height=620, yaxis=dict(showgrid=False))
            st.plotly_chart(fig, use_container_width=True)

    with trend_tabs[3]:
        seg_df = add_customer_segments(
            db.query(Q.engagement_segments(year=selected_year, quarter=selected_quarter))
        )
        if seg_df.empty:
            st.warning("No segment data is available for this quarter.")
        else:
            segment_filter = st.multiselect(
                "Filter segments",
                sorted(seg_df["Segment"].unique()),
                default=sorted(seg_df["Segment"].unique()),
            )
            view_df = seg_df[seg_df["Segment"].isin(segment_filter)]
            fig = px.scatter(
                view_df,
                x="Engagement_Ratio",
                y="Avg_Ticket",
                size="Total_Amount",
                color="Segment",
                hover_name="State",
                size_max=48,
                color_discrete_map={
                    "High value": "#f6c94e",
                    "High engagement": "#10b981",
                    "High spend, low engagement": "#ec4899",
                    "Needs activation": "#ef4444",
                },
            )
            customize_plotly_fig(fig)
            fig.update_layout(height=500)
            st.plotly_chart(fig, use_container_width=True)

            counts = view_df["Segment"].value_counts().reset_index()
            counts.columns = ["Segment", "States"]
            cols = st.columns(min(4, len(counts)) or 1)
            for i, row in counts.iterrows():
                with cols[i % len(cols)]:
                    st.markdown(
                        f"<span class='segment-pill'>{row['Segment']}: "
                        f"{int(row['States'])} states</span>",
                        unsafe_allow_html=True,
                    )
            st.dataframe(view_df, use_container_width=True, hide_index=True)
            st.download_button(
                "Download segment data",
                view_df.to_csv(index=False).encode("utf-8"),
                "state_lab_segments.csv",
                "text/csv",
            )

    st.markdown("</div>", unsafe_allow_html=True)


st.markdown(
    "<hr style='border-color:rgba(167,139,250,0.2);margin-top:32px;'>"
    "<p style='text-align:center;color:#8b5cf6;font-size:12px;'>"
    "Data sourced from PhonePe Pulse · github.com/PhonePe/pulse · "
    "Dashboard built with Streamlit & Plotly · © 2026"
    "</p>",
    unsafe_allow_html=True,
)
