import streamlit as st
import pandas as pd
import sqlite3
import json
import html
import uuid
import math
from datetime import datetime, date, timedelta
from pathlib import Path
import streamlit.components.v1 as components

# ============================================================
# FLOWOS | Operational Intelligence
# Local prototype: SQLite persistence + deterministic analytics
# ============================================================

st.set_page_config(
    page_title="FlowOS | Operational Intelligence",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Each Streamlit browser session receives its own isolated demo database.
# This is session isolation for a prototype, NOT authenticated multi-user security.
if "flowos_workspace_id" not in st.session_state:
    st.session_state["flowos_workspace_id"] = uuid.uuid4().hex
DB_PATH = Path(__file__).with_name(f"flowos_demo_{st.session_state['flowos_workspace_id']}.db")

# ---------- Persistence ----------
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                material TEXT PRIMARY KEY,
                current_stock REAL NOT NULL,
                daily_usage REAL NOT NULL,
                lead_time REAL NOT NULL,
                unit TEXT DEFAULT 'units',
                supplier TEXT DEFAULT 'Primary supplier',
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                material TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                quantity REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                note TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                category TEXT NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suppliers (
                name TEXT PRIMARY KEY,
                material TEXT DEFAULT '',
                lead_time REAL DEFAULT 0,
                reliability REAL DEFAULT 95,
                contact TEXT DEFAULT '',
                status TEXT DEFAULT 'Active'
            )
        """)
        count = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
        if count == 0:
            seed = [
                ("Paper Grade A", 5000, 2000, 5, "kg", "Northstar Paper Co."),
                ("Paper Grade B", 12000, 1000, 4, "kg", "Evergreen Materials"),
                ("Binding Glue", 600, 100, 3, "kg", "Adhesive Works"),
                ("Packaging Material", 900, 150, 2, "units", "PackRight Supply"),
            ]
            conn.executemany(
                "INSERT INTO inventory(material,current_stock,daily_usage,lead_time,unit,supplier,updated_at) VALUES(?,?,?,?,?,?,?)",
                [(a,b,c,d,e,f,datetime.now().isoformat(timespec="seconds")) for a,b,c,d,e,f in seed]
            )
            suppliers = [
                ("Northstar Paper Co.", "Paper Grade A", 5, 92, "demo@northstar.example", "Active"),
                ("Evergreen Materials", "Paper Grade B", 4, 97, "demo@evergreen.example", "Active"),
                ("Adhesive Works", "Binding Glue", 3, 95, "demo@adhesive.example", "Active"),
                ("PackRight Supply", "Packaging Material", 2, 98, "demo@packright.example", "Active"),
            ]
            conn.executemany("INSERT OR IGNORE INTO suppliers VALUES(?,?,?,?,?,?)", suppliers)
            conn.execute(
                "INSERT INTO activity(created_at,category,message) VALUES(?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), "SYSTEM", "FlowOS demonstration environment initialised.")
            )

def log_event(category, message):
    with db() as conn:
        conn.execute(
            "INSERT INTO activity(created_at,category,message) VALUES(?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), category, message)
        )

def load_inventory():
    with db() as conn:
        return pd.read_sql_query("SELECT material,current_stock,daily_usage,lead_time,unit,supplier FROM inventory ORDER BY material", conn)

def save_inventory(frame, replace_all=False):
    """Persist inventory. replace_all=True synchronises the editable register, including deletions."""
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        if replace_all:
            conn.execute("DELETE FROM inventory")
        for _, r in frame.iterrows():
            material = str(r["material"]).strip()
            if not material:
                continue
            conn.execute("""
                INSERT INTO inventory(material,current_stock,daily_usage,lead_time,unit,supplier,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(material) DO UPDATE SET
                    current_stock=excluded.current_stock,
                    daily_usage=excluded.daily_usage,
                    lead_time=excluded.lead_time,
                    unit=excluded.unit,
                    supplier=excluded.supplier,
                    updated_at=excluded.updated_at
            """, (
                material,
                max(0, float(r["current_stock"])),
                max(0.0, float(r["daily_usage"])),
                max(0, float(r["lead_time"])),
                str(r.get("unit", "units")),
                str(r.get("supplier", "Primary supplier")),
                now,
            ))
    log_event("INVENTORY", "Material register saved.")

def validate_inventory_frame(frame):
    """Return a cleaned inventory frame and human-readable validation errors."""
    required = ["material", "current_stock", "daily_usage", "lead_time"]
    missing = [c for c in required if c not in frame.columns]
    if missing:
        return pd.DataFrame(), ["Missing required columns: " + ", ".join(missing)]

    cleaned = frame.copy()
    cleaned.columns = [str(c).strip().lower().replace(" ", "_") for c in cleaned.columns]
    errors = []
    cleaned["material"] = cleaned["material"].fillna("").astype(str).str.strip()
    if (cleaned["material"] == "").any():
        errors.append("Every row needs a material name.")
    duplicate_names = cleaned.loc[cleaned["material"].duplicated(keep=False) & (cleaned["material"] != ""), "material"].unique().tolist()
    if duplicate_names:
        errors.append("Duplicate material names: " + ", ".join(duplicate_names[:8]) + ". Keep one row per material.")

    for col in ["current_stock", "daily_usage", "lead_time"]:
        numeric = pd.to_numeric(cleaned[col], errors="coerce")
        invalid = numeric.isna() | ~numeric.map(lambda x: math.isfinite(float(x)) if pd.notna(x) else False)
        if invalid.any():
            errors.append(f"{col.replace('_', ' ').title()} must contain valid numbers in every row.")
        elif (numeric < 0).any():
            errors.append(f"{col.replace('_', ' ').title()} cannot be negative.")
        cleaned[col] = numeric

    for col, default in [("unit", "units"), ("supplier", "Unassigned")]:
        if col not in cleaned.columns:
            cleaned[col] = default
        cleaned[col] = cleaned[col].fillna(default).astype(str).str.strip().replace("", default)

    if errors:
        return cleaned, errors
    return cleaned[["material", "current_stock", "daily_usage", "lead_time", "unit", "supplier"]], []

def load_activity(limit=12):
    with db() as conn:
        return pd.read_sql_query(
            "SELECT created_at,category,message FROM activity ORDER BY id DESC LIMIT ?",
            conn, params=(limit,)
        )

def load_approvals():
    with db() as conn:
        return pd.read_sql_query("SELECT * FROM approvals ORDER BY id DESC", conn)

def create_approval(material, quantity, recommendation):
    quantity = float(quantity)
    if quantity <= 0:
        return False
    with db() as conn:
        conn.execute(
            "INSERT INTO approvals(created_at,material,recommendation,quantity,status) VALUES(?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), material, recommendation, float(quantity), "Pending")
        )
    log_event("APPROVAL", f"Human approval requested for {material}: {quantity:,.0f} units.")
    return True

def update_approval(approval_id, status, note=""):
    with db() as conn:
        conn.execute("UPDATE approvals SET status=?, note=? WHERE id=?", (status, note, int(approval_id)))
    log_event("APPROVAL", f"Approval #{approval_id} marked {status.lower()}.")

def load_suppliers():
    with db() as conn:
        return pd.read_sql_query("SELECT * FROM suppliers ORDER BY name", conn)

def save_suppliers(frame):
    """Synchronise the supplier register and linked inventory lead-time fields."""
    records = []
    seen_materials = set()
    for _, r in frame.iterrows():
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        material = str(r.get("material", "")).strip()
        if material:
            if material in seen_materials:
                raise ValueError(f"More than one supplier is linked to {material}. Keep one primary supplier in this demo register.")
            seen_materials.add(material)
        lead_time = max(0.0, float(r.get("lead_time", 0) or 0))
        reliability = min(100.0, max(0.0, float(r.get("reliability", 95) or 0)))
        records.append((name, material, lead_time, reliability, str(r.get("contact", "")), str(r.get("status", "Active"))))
    with db() as conn:
        old_links = {
            row[0] for row in conn.execute("SELECT material FROM suppliers WHERE material <> ''").fetchall()
        }
        new_links = {record[1] for record in records if record[1]}
        # If a supplier link is removed, make that visible instead of leaving a ghost supplier name.
        for orphaned_material in old_links - new_links:
            conn.execute(
                "UPDATE inventory SET supplier='Unassigned', updated_at=? WHERE material=?",
                (datetime.now().isoformat(timespec="seconds"), orphaned_material),
            )
        conn.execute("DELETE FROM suppliers")
        if records:
            conn.executemany(
                "INSERT INTO suppliers(name,material,lead_time,reliability,contact,status) VALUES(?,?,?,?,?,?)",
                records,
            )
        # The supplier register is authoritative for the linked material's primary supplier and lead time.
        for name, material, lead_time, _reliability, _contact, _status in records:
            if material:
                conn.execute(
                    "UPDATE inventory SET supplier=?, lead_time=?, updated_at=? WHERE material=?",
                    (name, lead_time, datetime.now().isoformat(timespec="seconds"), material),
                )
    log_event("SUPPLIERS", "Supplier register saved and linked lead times synchronised.")

# ---------- Analytics ----------
def enrich_inventory(raw):
    """Auditable stock-cover, coverage-gap and replenishment calculations."""
    cols = ["days_cover", "days_remaining", "coverage_gap", "target_stock", "suggested_reorder", "risk"]
    if raw.empty:
        result = raw.copy()
        for col in cols:
            result[col] = pd.Series(dtype="object" if col == "risk" else "float64")
        return result
    d = raw.copy()
    for col in ["current_stock", "daily_usage", "lead_time"]:
        if col not in d.columns:
            d[col] = 0
        d[col] = pd.to_numeric(d[col], errors="coerce").fillna(0).clip(lower=0)
    d["days_remaining"] = d.apply(
        lambda r: r["current_stock"] / r["daily_usage"] if r["daily_usage"] > 0 else float("inf"), axis=1
    )
    d["days_cover"] = d["days_remaining"]
    d["coverage_gap"] = d.apply(
        lambda r: max(0.0, r["lead_time"] - r["days_remaining"])
        if r["days_remaining"] != float("inf") else 0.0, axis=1
    )
    d["target_stock"] = d["daily_usage"] * (d["lead_time"] + 3)
    d["suggested_reorder"] = (d["target_stock"] - d["current_stock"]).clip(lower=0)
    def classify(r):
        if r["daily_usage"] <= 0:
            return "HEALTHY"
        if r["days_remaining"] < r["lead_time"]:
            return "CRITICAL"
        if r["days_remaining"] < r["lead_time"] + 3:
            return "WATCH"
        return "HEALTHY"
    d["risk"] = d.apply(classify, axis=1)
    return d

def pct(n, total):
    return 0 if total == 0 else round(100*n/total)

def ring_chart(healthy, watch, critical):
    total = healthy + watch + critical
    if total == 0:
        a,b,c = 0,0,100
    else:
        a,b,c = healthy/total*100, watch/total*100, critical/total*100
    p1 = a
    p2 = a+b
    st.markdown(f"""
    <div class="ring-wrap">
      <div class="ring" style="background:conic-gradient(#39c6a2 0 {p1:.2f}%, #e7b65c {p1:.2f}% {p2:.2f}%, #f06d78 {p2:.2f}% 100%);">
        <div class="ring-hole"><div class="ring-total">{total}</div><div class="ring-label">MATERIALS</div></div>
      </div>
      <div class="ring-legend">
        <div><i class="dot healthy"></i><span>Healthy</span><b>{healthy} · {pct(healthy,total)}%</b></div>
        <div><i class="dot watch"></i><span>Watchlist</span><b>{watch} · {pct(watch,total)}%</b></div>
        <div><i class="dot critical"></i><span>Critical</span><b>{critical} · {pct(critical,total)}%</b></div>
      </div>
    </div>
    """, unsafe_allow_html=True)

def animated_metrics(items):
    components.html(f"""
    <div id="flow-metrics" style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;font-family:Inter,Arial,sans-serif;">
      {''.join([f'<div class="metric"><div class="label">{html.escape(x["label"])}</div><div class="value" data-target="{x["value"]}">0</div><div class="sub">{html.escape(x["sub"])}</div></div>' for x in items])}
    </div>
    <style>
      .metric{{background:linear-gradient(145deg,#142b49,#0d1d34);border:1px solid #294566;border-radius:15px;padding:17px 18px;min-height:108px;box-shadow:0 8px 24px rgba(0,0,0,.14)}}
      .label{{font-size:10px;letter-spacing:1.5px;color:#91a9c4;font-weight:700;text-transform:uppercase}}
      .value{{font-size:34px;font-weight:750;letter-spacing:-1.5px;color:#edf5ff;margin:8px 0 3px}}
      .sub{{font-size:11px;color:#8da6c2}}
      @media(max-width:700px){{#flow-metrics{{grid-template-columns:repeat(2,minmax(0,1fr))!important}}}}
    </style>
    <script>
      const root = document.getElementById('flow-metrics');
      root.querySelectorAll('.value').forEach(el => {{
        const target = Number(el.dataset.target || 0);
        const duration = 850;
        const start = performance.now();
        function tick(now) {{
          const t = Math.min(1, (now-start)/duration);
          const eased = 1-Math.pow(1-t,3);
          el.textContent = Math.round(target*eased).toLocaleString();
          if(t<1) requestAnimationFrame(tick);
        }}
        requestAnimationFrame(tick);
      }});
    </script>
    """, height=135, scrolling=False)

# ---------- Design system ----------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600;700;800&display=swap');
:root{--bg:#071321;--panel:#0d1e32;--panel2:#112742;--line:#213c59;--text:#edf4fc;--muted:#8ea5bf;--blue:#4d91ff;--cyan:#50d4e8;--green:#39c6a2;--amber:#e7b65c;--red:#f06d78}
html,body,[class*="css"]{font-family:'Manrope',sans-serif}
.stApp{background:radial-gradient(ellipse at 75% -10%,rgba(36,103,174,.22),transparent 34%),linear-gradient(145deg,#071321 0%,#081728 55%,#071321 100%);color:var(--text)}
[data-testid="stHeader"]{background:rgba(7,19,33,.78)}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#09182a,#071321);border-right:1px solid #1d3550}
[data-testid="stSidebar"] *{color:#dce8f5}
.block-container{padding-top:1.35rem;padding-bottom:3rem;max-width:1500px}
h1,h2,h3{color:#f0f6ff!important;letter-spacing:-.7px}
p,li{color:#b7c8dc}
.eyebrow{font:500 10px 'DM Mono',monospace;letter-spacing:2px;color:#6fa9e8;text-transform:uppercase;margin-bottom:8px}
.hero{padding:25px 28px;border:1px solid #294b70;border-radius:20px;background:radial-gradient(circle at 90% 20%,rgba(69,142,255,.25),transparent 28%),linear-gradient(120deg,#102b4a,#0c1e34 65%,#102a47);box-shadow:0 16px 42px rgba(0,0,0,.18);margin-bottom:18px}
.hero h1{font-size:31px!important;margin:0 0 8px 0!important}
.hero p{font-size:13px;max-width:760px;margin:0;color:#b7cbe2}
.panel{background:linear-gradient(145deg,rgba(17,39,65,.96),rgba(11,28,48,.96));border:1px solid #203e5d;border-radius:17px;padding:18px 19px;box-shadow:0 10px 30px rgba(0,0,0,.12);margin-bottom:14px}
.panel-title{font-size:11px;font-weight:800;letter-spacing:1.5px;color:#9db6d2;text-transform:uppercase;margin-bottom:12px}
.small-muted{font-size:11px;color:#829bb6}
.status-pill{display:inline-block;padding:5px 9px;border-radius:99px;border:1px solid #285b65;background:#0d353b;color:#75e3d2;font:500 10px 'DM Mono',monospace;letter-spacing:.6px}
hr{border-color:#1e3854!important}
div[data-testid="stMetric"]{background:linear-gradient(145deg,#142b49,#0d1d34);border:1px solid #294566;border-radius:15px;padding:14px 16px}
div[data-testid="stMetricLabel"]{color:#91a9c4}
div[data-testid="stMetricValue"]{color:#edf5ff}
.stButton>button{border-radius:10px;border:1px solid #31577e;background:linear-gradient(110deg,#2563bd,#327ed8);color:white;font-weight:700;min-height:40px}
.stButton>button:hover{border-color:#6ab7ff;color:white;background:linear-gradient(110deg,#3179d8,#3b98e8)}
.stDownloadButton>button{border-radius:10px}
.stTextInput input,.stNumberInput input,.stSelectbox div[data-baseweb="select"],.stTextArea textarea{background:#0a1b2e!important;color:#edf4fc!important;border:1px solid #294563!important;border-radius:9px!important}
[data-testid="stDataFrame"],[data-testid="stTable"]{border:1px solid #274361;border-radius:12px;overflow:hidden}
.stTabs [data-baseweb="tab-list"]{gap:8px}
.stTabs [data-baseweb="tab"]{background:#10243b;border-radius:9px 9px 0 0;padding:9px 15px;color:#a9bfd7}
.stTabs [aria-selected="true"]{background:#1b3e65!important;color:#fff!important}
.ring-wrap{display:flex;align-items:center;gap:22px;padding:10px 0 4px}
.ring{width:170px;height:170px;border-radius:50%;display:grid;place-items:center;flex:0 0 170px;box-shadow:0 0 35px rgba(42,116,180,.12)}
.ring-hole{width:116px;height:116px;border-radius:50%;background:#0d2036;display:flex;flex-direction:column;align-items:center;justify-content:center;border:1px solid #294663}
.ring-total{font-size:31px;font-weight:800;color:#f2f7ff}
.ring-label{font:10px 'DM Mono',monospace;letter-spacing:1.2px;color:#8da8c4}
.ring-legend{display:flex;flex-direction:column;gap:13px;flex:1;min-width:150px}
.ring-legend div{display:grid;grid-template-columns:10px 1fr auto;align-items:center;gap:9px;font-size:12px;color:#b9cbe0}
.ring-legend b{color:#eaf2fc;font-size:11px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.healthy{background:#39c6a2}.watch{background:#e7b65c}.critical{background:#f06d78}
.signal{padding:12px 14px;border:1px solid #3e3940;border-left:3px solid #f06d78;background:#211f2a;border-radius:10px;margin:7px 0}
.signal.watch{border-left-color:#e7b65c;background:#24251f}
.signal.good{border-left-color:#39c6a2;background:#172a2c}
.signal b{color:#f2f6fb;font-size:12px}
.signal p{font-size:11px;margin:4px 0 0;color:#aabbd0}
[data-testid="stAlert"]{border-radius:12px}
footer{visibility:hidden}
#MainMenu{visibility:hidden}
</style>
""", unsafe_allow_html=True)

init_db()

# ---------- Premium visual refinement ----------
# High-contrast typography, clearer navigation, richer surfaces and restrained motion.
st.markdown(r'''
<style>
/* Contrast and legibility */
:root {
  --text: #F7FAFF;
  --muted: #B5C7DD;
  --line: #345575;
}
.stApp, [data-testid="stAppViewContainer"] { color: #F7FAFF; }
p, li, label, [data-testid="stMarkdownContainer"] p { color: #D1DDED !important; }
h1, h2, h3, h4 { color: #FFFFFF !important; font-weight: 800 !important; }
.eyebrow { color: #83C7FF !important; font-weight: 700 !important; }
.small-muted, .stCaption, [data-testid="stCaptionContainer"] {
  color: #AFC3DA !important;
}
/* Sidebar navigation: brighter, heavier and easier to scan */
section[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0B1D33 0%, #081729 60%, #071321 100%);
  border-right: 1px solid #315273;
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
  color: #DCEBFA !important;
}
section[data-testid="stSidebar"] [role="radiogroup"] label {
  color: #E5F0FC !important;
  font-weight: 700 !important;
  font-size: 13px !important;
  padding: 7px 9px !important;
  border-radius: 9px !important;
  transition: background .18s ease, color .18s ease;
}
section[data-testid="stSidebar"] [role="radiogroup"] label:hover {
  background: #173553 !important;
  color: #FFFFFF !important;
}
section[data-testid="stSidebar"] [role="radiogroup"] label p {
  color: inherit !important;
  font-weight: 700 !important;
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] .eyebrow {
  color: #8ACBFF !important;
}
/* Stronger hero and premium surface treatment */
.hero {
  position: relative;
  overflow: hidden;
  border: 1px solid #3B6792 !important;
  background:
    radial-gradient(ellipse at 86% 10%, rgba(68,157,255,.32), transparent 34%),
    radial-gradient(ellipse at 10% 110%, rgba(43,201,218,.12), transparent 42%),
    linear-gradient(118deg, #15395F 0%, #102B4A 48%, #0C2039 100%) !important;
  box-shadow: 0 20px 55px rgba(0,0,0,.28), inset 0 1px 0 rgba(255,255,255,.06) !important;
  animation: heroEnter .65s ease both;
}
.hero:after {
  content: "";
  position: absolute;
  width: 240px; height: 240px;
  right: -90px; top: -130px;
  border: 1px solid rgba(128,194,255,.22);
  border-radius: 50%;
  box-shadow: 0 0 0 28px rgba(128,194,255,.035), 0 0 0 58px rgba(128,194,255,.025);
  pointer-events: none;
}
.hero h1 { font-size: clamp(30px, 3.2vw, 43px) !important; font-weight: 850 !important; }
.hero p { color: #D7E7F8 !important; font-size: 14px !important; line-height: 1.7 !important; }
.panel {
  border: 1px solid #2B4C6D !important;
  background: linear-gradient(145deg, rgba(19,45,73,.98), rgba(10,27,47,.98)) !important;
  box-shadow: 0 14px 38px rgba(0,0,0,.18), inset 0 1px 0 rgba(255,255,255,.035) !important;
  transition: border-color .2s ease, transform .2s ease, box-shadow .2s ease;
}
.panel:hover {
  border-color: #477BAA !important;
  box-shadow: 0 18px 42px rgba(0,0,0,.23), 0 0 24px rgba(63,145,220,.07) !important;
}
.panel-title {
  color: #B8D9F8 !important;
  font-weight: 800 !important;
  letter-spacing: 1.6px !important;
}
.status-pill {
  color: #A2F3E4 !important;
  background: rgba(13,74,69,.55) !important;
  border-color: #2B8278 !important;
  font-weight: 700 !important;
}
/* Metric cards and labels */
div[data-testid="stMetric"] {
  background: linear-gradient(145deg, #17385D, #102640) !important;
  border: 1px solid #3B638A !important;
  box-shadow: 0 12px 30px rgba(0,0,0,.2) !important;
}
div[data-testid="stMetricLabel"], div[data-testid="stMetricLabel"] * {
  color: #C4D9EF !important; font-weight: 700 !important;
}
div[data-testid="stMetricValue"], div[data-testid="stMetricValue"] * {
  color: #FFFFFF !important; font-weight: 850 !important;
}
/* Buttons, tabs and inputs */
.stButton > button, .stDownloadButton > button {
  font-weight: 800 !important;
  color: #FFFFFF !important;
  border: 1px solid #5799D6 !important;
  box-shadow: 0 7px 18px rgba(24,91,161,.2);
}
.stButton > button:hover, .stDownloadButton > button:hover {
  border-color: #8BCBFF !important;
  box-shadow: 0 0 0 3px rgba(78,160,235,.13), 0 9px 22px rgba(24,91,161,.25);
  transform: translateY(-1px);
}
.stTabs [data-baseweb="tab"] {
  color: #C5D8EC !important; font-weight: 750 !important;
  border: 1px solid transparent;
}
.stTabs [aria-selected="true"] {
  color: #FFFFFF !important;
  border-color: #3B6B98 !important;
  background: linear-gradient(180deg,#1B4168,#153452) !important;
}
.stTextInput label, .stNumberInput label, .stSelectbox label,
.stTextArea label, .stDateInput label, .stSlider label,
.stFileUploader label {
  color: #D9E7F5 !important; font-weight: 750 !important;
}
/* Table readability */
[data-testid="stDataFrame"] {
  border: 1px solid #355879 !important;
  box-shadow: 0 10px 25px rgba(0,0,0,.13);
}
[data-testid="stDataFrame"] * { color: #E8F1FB; }
/* Responsive 4-up metric strip */
@media (max-width: 760px) {
  .ring-wrap { flex-wrap: wrap; }
  .ring { width: 145px; height: 145px; flex-basis: 145px; }
  .ring-hole { width: 99px; height: 99px; }
}
@keyframes heroEnter {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
@media (prefers-reduced-motion: reduce) {
  *, *:before, *:after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
}
</style>
''', unsafe_allow_html=True)

# ---------- First-run welcome gate ----------
# Show the welcome note once per Streamlit browser session, then open Command Centre.
if "flowos_welcome_completed" not in st.session_state:
    st.session_state["flowos_welcome_completed"] = False

# ---------- Sidebar ----------
if "pending_navigation" in st.session_state:
    st.session_state["flowos_navigation"] = st.session_state.pop("pending_navigation")

if not st.session_state["flowos_welcome_completed"]:
    # The first-run welcome is intentionally distraction-free: no sidebar until entry.
    st.markdown("<style>section[data-testid='stSidebar']{display:none!important}</style>", unsafe_allow_html=True)
    page = "Welcome"
else:
    with st.sidebar:
        st.markdown("""
        <div style="padding:8px 2px 20px">
          <svg viewBox="0 0 330 92" role="img" aria-label="FlowOS logo" style="width:100%;height:auto;display:block">
            <defs><linearGradient id="flowosBrand" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#63DDF2"/><stop offset="1" stop-color="#438BFF"/></linearGradient></defs>
            <g transform="translate(5 8)" fill="none" stroke="url(#flowosBrand)" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M18 38 L42 14 L66 38 L42 62 Z"/><path d="M42 14 L42 62 M18 38 L66 38" opacity=".5"/><path d="M66 38 L84 38 L96 26"/>
              <circle cx="18" cy="38" r="4.5" fill="#63DDF2" stroke="none"/><circle cx="42" cy="14" r="4.5" fill="#63DDF2" stroke="none"/><circle cx="66" cy="38" r="4.5" fill="#438BFF" stroke="none"/><circle cx="42" cy="62" r="4.5" fill="#438BFF" stroke="none"/><circle cx="96" cy="26" r="4.5" fill="#63DDF2" stroke="none"/>
            </g>
            <text x="116" y="48" font-family="Arial,sans-serif" font-size="34" font-weight="800" letter-spacing="-1.8" fill="#F4F8FF">FLOW<tspan fill="#59D2E9">OS</tspan></text>
            <text x="118" y="68" font-family="Arial,sans-serif" font-size="7.5" font-weight="600" letter-spacing="1.65" fill="#9DB8D4">CONNECT. PREDICT. PROGRESS.</text>
          </svg>
        </div>
        <div class="eyebrow">WORKSPACE</div>
        """, unsafe_allow_html=True)
        pages = [
            "Command Centre", "Inventory Intelligence", "Production Intelligence",
            "Department Coordination", "Supplier Intelligence", "AI Assistant",
            "Chain Reaction Engine", "Approvals & Audit", "Help & Support",
            "Plans & Subscription", "FAQ"
        ]
        if st.session_state.get("flowos_navigation") not in pages:
            st.session_state["flowos_navigation"] = "Command Centre"
        page = st.radio("Navigate", pages, label_visibility="collapsed", key="flowos_navigation")
        st.markdown("---")
        st.markdown('<div class="eyebrow">SYSTEM STATUS</div><span class="status-pill">● DEMO ENVIRONMENT ONLINE</span>', unsafe_allow_html=True)
        st.caption("Prototype workspace · Sample data")
        st.caption("Human approval required for consequential actions.")
        st.caption("Designed for clarity, not ERP replacement.")
        st.markdown("---")
        st.markdown('<div class="eyebrow">DEMO WORKSPACE</div>', unsafe_allow_html=True)
        st.caption("Your browser session has an isolated sample workspace.")
        if st.button("Reset my demo workspace", use_container_width=True):
            st.session_state["confirm_demo_reset"] = True
        if st.session_state.get("confirm_demo_reset", False):
            st.warning("This clears only your current demo workspace: inventory edits, approvals and activity.")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Confirm reset", type="primary", use_container_width=True):
                    try:
                        if DB_PATH.exists():
                            DB_PATH.unlink()
                        for key in ["chain_result", "inventory_editor", "supplier_editor", "confirm_demo_reset"]:
                            st.session_state.pop(key, None)
                        init_db()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Reset could not complete: {exc}")
            with c2:
                if st.button("Cancel", use_container_width=True):
                    st.session_state.pop("confirm_demo_reset", None)
                    st.rerun()
        st.markdown("---")
        st.markdown('<div class="small-muted">FLOWOS / BUILD 0.5<br>Session-isolated demonstration prototype</div>', unsafe_allow_html=True)

raw = load_inventory()
data = enrich_inventory(raw)
total_materials = len(data)
critical_n = int((data["risk"] == "CRITICAL").sum()) if total_materials else 0
watch_n = int((data["risk"] == "WATCH").sum()) if total_materials else 0
healthy_n = int((data["risk"] == "HEALTHY").sum()) if total_materials else 0
pending_n = int((load_approvals()["status"] == "Pending").sum()) if not load_approvals().empty else 0

# ---------- Reusable header ----------
def page_header(kicker, title, subtitle):
    st.markdown(f"""
    <div class="hero">
      <div class="eyebrow">{html.escape(kicker)}</div>
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(subtitle)}</p>
    </div>
    """, unsafe_allow_html=True)

def show_risk_signal(r):
    css = "signal" if r["risk"] == "CRITICAL" else ("signal watch" if r["risk"] == "WATCH" else "signal good")
    st.markdown(
        f'<div class="{css}"><b>{html.escape(str(r["material"]))} · {r["risk"]}</b>'
        f'<p>{r["days_cover"]:.1f} days of stock against {r["lead_time"]:.1f} days supplier lead time.</p></div>',
        unsafe_allow_html=True
    )


def render_runway_cards(frame):
    """Display days remaining prominently for every material."""
    if frame.empty:
        st.info("No materials to display. Add or import inventory records.")
        return
    risk_order = {"CRITICAL": 0, "WATCH": 1, "HEALTHY": 2}
    ordered = frame.assign(_order=frame["risk"].map(risk_order)).sort_values(["_order", "days_remaining"])
    for _, r in ordered.iterrows():
        days = r["days_remaining"]
        days_text = "No usage forecast" if days == float("inf") else f"{days:.1f} days"
        gap_text = f"{r['coverage_gap']:.1f} days"
        risk_color = "#F06D78" if r["risk"] == "CRITICAL" else ("#E7B65C" if r["risk"] == "WATCH" else "#39C6A2")
        material = html.escape(str(r["material"]))
        supplier = html.escape(str(r.get("supplier", "")))
        st.markdown(f"""
        <div style="display:grid;grid-template-columns:minmax(140px,1.5fr) minmax(105px,1fr) minmax(105px,1fr) minmax(100px,.8fr);gap:12px;align-items:center;padding:13px 15px;margin:7px 0;border:1px solid #315171;border-left:4px solid {risk_color};border-radius:12px;background:linear-gradient(110deg,#102943,#0b1d31)">
          <div><div style="font-size:13px;font-weight:800;color:#F5F9FF">{material}</div><div style="font-size:10px;color:#AFC4DA;margin-top:3px">{supplier}</div></div>
          <div><div style="font-size:9px;letter-spacing:1px;color:#AFC4DA;text-transform:uppercase;font-weight:800">Stock remaining</div><div style="font-size:20px;font-weight:850;color:#FFFFFF;margin-top:2px">{days_text}</div></div>
          <div><div style="font-size:9px;letter-spacing:1px;color:#AFC4DA;text-transform:uppercase;font-weight:800">Coverage gap</div><div style="font-size:17px;font-weight:800;color:#FFFFFF;margin-top:4px">{gap_text}</div></div>
          <div><div style="font-size:9px;letter-spacing:1px;color:#AFC4DA;text-transform:uppercase;font-weight:800">Risk status</div><div style="font-size:12px;font-weight:900;color:{risk_color};margin-top:5px">{r['risk']}</div></div>
        </div>
        """, unsafe_allow_html=True)

# ---------- Pages ----------
if page == "Welcome":
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Caveat:wght@500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
    .stApp { background: radial-gradient(ellipse at 50% 5%, rgba(28,119,210,.34), transparent 38%), linear-gradient(145deg,#06101f 0%,#0a1e36 52%,#071321 100%) !important; }
    .block-container { max-width: 1120px !important; padding-top: 3.2rem !important; }
    .welcome-stage { perspective: 1200px; padding: 1.2rem 0 2rem; }
    .welcome-card { position:relative; overflow:hidden; text-align:center; padding: clamp(28px,5vw,58px); border-radius:30px; border:1px solid rgba(116,190,255,.42); background:linear-gradient(145deg,rgba(23,65,108,.82),rgba(8,25,46,.94)); box-shadow:0 35px 90px rgba(0,0,0,.42), inset 0 1px 0 rgba(255,255,255,.13); transform:rotateX(2deg); animation:welcomeRise .9s cubic-bezier(.2,.8,.2,1) both; backdrop-filter:blur(18px); }
    .welcome-card:before { content:''; position:absolute; inset:-40% auto auto -15%; width:70%; height:90%; background:radial-gradient(ellipse,rgba(75,190,255,.20),transparent 68%); pointer-events:none; }
    .welcome-orb { position:absolute; border-radius:50%; filter:blur(1px); pointer-events:none; animation:orbFloat 8s ease-in-out infinite; }
    .welcome-orb.one { width:180px;height:180px;right:5%;top:8%;background:radial-gradient(circle at 35% 35%,rgba(96,220,255,.28),rgba(56,126,255,.025) 68%); }
    .welcome-orb.two { width:120px;height:120px;left:7%;bottom:8%;background:radial-gradient(circle at 35% 35%,rgba(78,137,255,.22),rgba(56,126,255,.01) 70%);animation-delay:-3s; }
    .welcome-brand { width:min(100%,440px); margin:0 auto 1.5rem; filter:drop-shadow(0 10px 28px rgba(50,165,255,.28)); animation:brandFloat 5s ease-in-out infinite; }
    .welcome-eyebrow { color:#8eddf7; font:700 10px 'DM Mono',monospace; letter-spacing:3px; margin:1rem 0; }
    .welcome-title { color:#f7fbff; font:800 clamp(30px,5vw,48px) Manrope,sans-serif; letter-spacing:-1.8px; margin:.2rem 0 .7rem; }
    .welcome-note { color:#f6f2ed; font:600 clamp(27px,4vw,42px) 'Caveat',cursive; line-height:1.22; max-width:760px; margin:1.2rem auto 1rem; text-shadow:0 3px 24px rgba(93,189,255,.12); }
    .welcome-copy { color:#c4d7eb; font:400 14px/1.8 Manrope,sans-serif; max-width:680px; margin:0 auto; }
    .welcome-foot { color:#79b9e8; font:600 10px 'DM Mono',monospace; letter-spacing:2px; margin-top:2rem; }
    @keyframes welcomeRise { from{opacity:0;transform:translateY(28px) rotateX(8deg) scale(.975)} to{opacity:1;transform:translateY(0) rotateX(2deg) scale(1)} }
    @keyframes brandFloat { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-7px)} }
    @keyframes orbFloat { 0%,100%{transform:translate3d(0,0,0)} 50%{transform:translate3d(10px,-16px,0)} }
    @media (prefers-reduced-motion:reduce) { .welcome-card,.welcome-brand,.welcome-orb{animation:none!important;transform:none!important} }
    @media(max-width:640px) { .block-container{padding-top:1.2rem!important}.welcome-card{border-radius:20px}.welcome-orb{opacity:.45} }
    </style>
    <div class="welcome-stage">
      <div class="welcome-card">
        <div class="welcome-orb one"></div><div class="welcome-orb two"></div>
        <svg class="welcome-brand" viewBox="0 0 720 180" role="img" aria-label="FlowOS logo">
          <defs><linearGradient id="flowosWelcome" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#63DDF2"/><stop offset="1" stop-color="#438BFF"/></linearGradient></defs>
          <g transform="translate(18 18)" fill="none" stroke="url(#flowosWelcome)" stroke-width="8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M24 72 L70 26 L116 72 L70 118 Z"/><path d="M70 26 L70 118 M24 72 L116 72" opacity=".5"/><path d="M116 72 L151 72 L174 49"/>
            <circle cx="24" cy="72" r="8" fill="#63DDF2" stroke="none"/><circle cx="70" cy="26" r="8" fill="#63DDF2" stroke="none"/><circle cx="116" cy="72" r="8" fill="#438BFF" stroke="none"/><circle cx="70" cy="118" r="8" fill="#438BFF" stroke="none"/><circle cx="174" cy="49" r="8" fill="#63DDF2" stroke="none"/>
          </g>
          <text x="220" y="100" font-family="Arial,sans-serif" font-size="66" font-weight="800" letter-spacing="-3" fill="#F4F8FF">FLOW<tspan fill="#59D2E9">OS</tspan></text>
          <text x="224" y="135" font-family="Arial,sans-serif" font-size="15" font-weight="600" letter-spacing="4" fill="#9DB8D4">CONNECT. PREDICT. PROGRESS.</text>
        </svg>
        <div class="welcome-eyebrow">OPERATIONAL INTELLIGENCE · BUILD 0.5</div>
        <div class="welcome-title">Welcome to FlowOS</div>
        <div class="welcome-note">Dear User,<br/>Welcome aboard.</div>
        <div class="welcome-copy">Your operations, connected. FlowOS helps bring inventory signals, supplier context, production exposure and team decisions into one clear workspace. Explore the picture, understand what may need attention, and keep people in control of every consequential action.</div>
        <div class="welcome-foot">CONNECT. PREDICT. PROGRESS.</div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("<div style='text-align:center;color:#a9c3df;font-size:12px;margin:0 0 8px'>Press Enter in the field below, or select the button to open your workspace.</div>", unsafe_allow_html=True)
    with st.form("flowos_enter_form", clear_on_submit=False):
        st.text_input("Keyboard entry", placeholder="Press Enter to continue", label_visibility="collapsed", key="flowos_enter_key")
        entered = st.form_submit_button("ENTER FLOWOS  →", type="primary", use_container_width=True)
    if entered:
        st.session_state["flowos_welcome_completed"] = True
        st.session_state["flowos_navigation"] = "Command Centre"
        st.session_state.pop("flowos_enter_key", None)
        st.rerun()

elif page == "Command Centre":
    page_header("FLOWOS / OPERATIONAL INTELLIGENCE", "Command Centre", "One connected view of your operations. Detect emerging risk, coordinate the response and keep people in control.")
    st.markdown('<span class="status-pill">● DEMO ENVIRONMENT ONLINE</span> &nbsp; <span class="small-muted">Last refreshed: '+datetime.now().strftime("%d %b %Y · %H:%M")+'</span>', unsafe_allow_html=True)
    st.info("Start with Inventory Intelligence, then review supplier lead times and run a Chain Reaction scenario. All figures in this prototype are demonstration data.")
    animated_metrics([
        {"label":"Materials monitored","value":total_materials,"sub":"Active inventory records"},
        {"label":"Critical signals","value":critical_n,"sub":"Require immediate review"},
        {"label":"Watchlist","value":watch_n,"sub":"Early attention"},
        {"label":"Pending approvals","value":pending_n,"sub":"Human decisions required"},
    ])
    left, right = st.columns([1.05, 1.25], gap="large")
    with left:
        st.markdown('<div class="panel"><div class="panel-title">360° inventory health</div>', unsafe_allow_html=True)
        ring_chart(healthy_n, watch_n, critical_n)
        st.markdown('<div class="small-muted">Distribution is calculated from current stock cover compared with supplier lead time and the illustrative safety buffer.</div></div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="panel"><div class="panel-title">Priority signals</div>', unsafe_allow_html=True)
        if data.empty:
            st.info("Add inventory records to generate operational signals.")
        else:
            risk_rank = {"CRITICAL": 0, "WATCH": 1, "HEALTHY": 2}
            priority_data = data.assign(_risk_rank=data["risk"].map(risk_rank)).sort_values(["_risk_rank", "days_cover"])
            for _, r in priority_data.iterrows():
                if r["risk"] != "HEALTHY":
                    show_risk_signal(r)
            if critical_n == 0 and watch_n == 0:
                st.success("No critical or watchlist materials detected.")
        st.markdown('</div>', unsafe_allow_html=True)
    st.markdown("### Stock runway · days remaining")
    st.caption("How long will each material last at the entered average daily usage?")
    render_runway_cards(data)

    c1,c2 = st.columns([1.2,1], gap="large")
    with c1:
        st.markdown('<div class="panel"><div class="panel-title">Stock cover by material · days</div>', unsafe_allow_html=True)
        if not data.empty:
            chart = data.set_index("material")[["days_remaining","lead_time"]].copy()
            chart["days_remaining"] = chart["days_remaining"].replace(float("inf"), float("nan"))
            chart = chart.rename(columns={"days_remaining":"Stock remaining (days)","lead_time":"Supplier lead time (days)"})
            st.bar_chart(chart, color=["#4d91ff","#e7b65c"], height=250)
        st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="panel"><div class="panel-title">Operational activity</div>', unsafe_allow_html=True)
        activity = load_activity(7)
        if activity.empty:
            st.caption("No activity yet.")
        else:
            for _, a in activity.iterrows():
                stamp = str(a["created_at"])[11:16]
                st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid #203750"><span class="eyebrow">{html.escape(str(a["category"]))} · {stamp}</span><div style="font-size:12px;color:#c4d3e4">{html.escape(str(a["message"]))}</div></div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    st.markdown("### How can FlowOS help your operations today?")
    q1,q2,q3 = st.columns(3)
    with q1:
        if st.button("Review inventory risk", use_container_width=True): st.session_state["pending_navigation"]="Inventory Intelligence"; st.rerun()
    with q2:
        if st.button("Run a disruption scenario", use_container_width=True): st.session_state["pending_navigation"]="Chain Reaction Engine"; st.rerun()
    with q3:
        if st.button("Review approvals", use_container_width=True): st.session_state["pending_navigation"]="Approvals & Audit"; st.rerun()

elif page == "Inventory Intelligence":
    page_header("INTELLIGENCE / MATERIALS", "Inventory Intelligence", "Monitor stock cover, understand exposure and prepare controlled replenishment decisions.")
    with st.expander("Import inventory from CSV or Excel", expanded=False):
        uploaded = st.file_uploader("Upload a .csv or .xlsx file", type=["csv","xlsx"], key="inventory_upload")
        st.caption("Required columns: material, current_stock, daily_usage, lead_time. Optional: unit, supplier.")
        template = pd.DataFrame([
            {"material":"Example Material","current_stock":1000,"daily_usage":100,"lead_time":4,"unit":"kg","supplier":"Example Supplier"}
        ])
        st.download_button(
            "Download CSV template",
            template.to_csv(index=False).encode("utf-8"),
            file_name="flowos_inventory_template.csv",
            mime="text/csv",
            help="Use this column structure to prepare an inventory import."
        )
        if uploaded is not None:
            try:
                incoming = pd.read_csv(uploaded) if uploaded.name.lower().endswith(".csv") else pd.read_excel(uploaded)
                incoming.columns = [str(c).strip().lower().replace(" ","_") for c in incoming.columns]
                cleaned, validation_errors = validate_inventory_frame(incoming)
                if validation_errors:
                    st.error("Please correct these import issues before saving:")
                    for issue in validation_errors:
                        st.write("• " + issue)
                else:
                    st.success(f"Validation passed · {len(cleaned)} material rows ready to import.")
                    st.dataframe(cleaned.head(20), use_container_width=True)
                    if st.button("Import and save records", type="primary"):
                        save_inventory(cleaned)
                        st.success("Inventory records validated and saved to this local demo workspace.")
                        st.rerun()
            except Exception as e:
                st.error(f"Could not read that file: {e}")
    st.markdown('<div class="panel"><div class="panel-title">Live material register</div>', unsafe_allow_html=True)
    editable = raw.copy()
    edited = st.data_editor(
        editable, use_container_width=True, hide_index=True, num_rows="dynamic",
        column_config={
            "material": st.column_config.TextColumn("Material", required=True),
            "current_stock": st.column_config.NumberColumn("Current stock", min_value=0, step=100),
            "daily_usage": st.column_config.NumberColumn("Daily usage", min_value=0, step=10),
            "lead_time": st.column_config.NumberColumn("Supplier lead time (days)", min_value=0, step=1),
            "unit": st.column_config.TextColumn("Unit"),
            "supplier": st.column_config.TextColumn("Supplier"),
        },
        key="inventory_editor", disabled=["lead_time", "supplier"]
    )
    if st.button("Save register changes", type="primary"):
        try:
            cleaned, validation_errors = validate_inventory_frame(edited)
            if validation_errors:
                st.error("Changes were not saved. Please correct the following:")
                for issue in validation_errors:
                    st.write("• " + issue)
            else:
                save_inventory(cleaned, replace_all=True)
                st.success("Validated changes saved, including removed rows, in this local demo workspace.")
                st.rerun()
        except Exception as e:
            st.error(f"Could not save register: {e}")
    st.markdown('</div>', unsafe_allow_html=True)
    data = enrich_inventory(load_inventory())
    st.markdown("### Stock runway and risk analysis")
    st.caption("Stock remaining is calculated for every material and displayed prominently below.")
    if not data.empty:
        render_runway_cards(data)
        show = data[["material","current_stock","daily_usage","lead_time","days_remaining","coverage_gap","risk","suggested_reorder"]].copy()
        show["days_remaining"] = show["days_remaining"].replace(float("inf"), float("nan"))
        show.columns = ["Material","Stock on hand","Daily usage","Supplier lead (days)","Stock remaining (days)","Coverage gap (days)","Risk","Illustrative reorder qty"]
        st.dataframe(
            show.round(1), use_container_width=True, hide_index=True,
            column_config={
                "Stock remaining (days)": st.column_config.NumberColumn("Stock remaining (days)", format="%.1f"),
                "Coverage gap (days)": st.column_config.NumberColumn("Coverage gap (days)", format="%.1f"),
                "Illustrative reorder qty": st.column_config.NumberColumn("Illustrative reorder qty", format="%,.0f"),
            }
        )
        st.markdown("#### Calculation method")
        st.markdown(
            "**Stock remaining (days)** = stock on hand ÷ average daily usage.\n\n"
            "**Coverage gap (days)** = supplier lead time − stock remaining, minimum zero.\n\n"
            "**Risk** = Critical if stock runs out before supplier delivery; Watch if cover is less than lead time + 3 days; otherwise Healthy."
        )
        st.caption("The 3-day buffer is a demonstration assumption. The reorder estimate excludes incoming purchase orders, MOQ, demand variability and supplier reliability.")
        st.markdown("### Replenishment decision")
        risk_rank = {"CRITICAL": 0, "WATCH": 1, "HEALTHY": 2}
        preferred = data.assign(_risk_rank=data["risk"].map(risk_rank)).sort_values(
            ["_risk_rank", "suggested_reorder"], ascending=[True, False]
        )["material"].iloc[0]
        choice = st.selectbox("Select material", data["material"].tolist(), index=data["material"].tolist().index(preferred))
        r = data[data["material"] == choice].iloc[0]
        a, b, c = st.columns(3)
        days_remaining = r["days_remaining"]
        days_label = "No usage forecast" if days_remaining == float("inf") else f"{days_remaining:.1f} days"
        a.metric("Stock remaining", days_label)
        b.metric("Supplier lead time", f'{r["lead_time"]:.1f} days')
        c.metric("Coverage gap", f'{r["coverage_gap"]:.1f} days')
        st.metric("Illustrative reorder quantity", f'{r["suggested_reorder"]:,.0f} {r["unit"]}')
        st.caption("Example: 5,000 kg ÷ 2,000 kg/day = 2.5 days of stock. With a 5-day lead time, the coverage gap is 2.5 days.")
        if r["suggested_reorder"] > 0:
            if st.button("Request human approval", type="primary"):
                if create_approval(choice, r["suggested_reorder"], "Illustrative replenishment recommendation"):
                    st.success("Approval request created. No purchase order has been sent.")
                    st.rerun()
        else:
            st.info("No replenishment approval is needed: the illustrative reorder quantity is zero.")
    else:
        st.info("No inventory records. Import a file or add a material above.")

elif page == "Production Intelligence":
    page_header("INTELLIGENCE / PRODUCTION", "Production Intelligence", "Translate material constraints into a clearer view of production exposure.")
    st.info("This workspace is a scenario model for the demonstration. It is not connected to a live production ERP.")
    materials = data["material"].tolist() if not data.empty else []
    if materials:
        selected = st.selectbox("Material constraint", materials)
        r = data[data["material"] == selected].iloc[0]
        planned_daily = st.number_input("Planned daily production consumption", min_value=0.0, value=float(r["daily_usage"]), step=50.0)
        open_orders = st.number_input("Open customer orders potentially affected", min_value=0, value=3, step=1)
        days_horizon = st.slider("Planning horizon (days)", 1, 30, 7)
        available_days = float(r["current_stock"]) / planned_daily if planned_daily > 0 else float("inf")
        exposure = max(0, days_horizon - available_days) if planned_daily > 0 else 0
        a,b,c = st.columns(3)
        a.metric("Estimated stock cover", "No usage forecast" if available_days == float("inf") else f"{available_days:.1f} days")
        b.metric("Potential exposure window", f"{exposure:.1f} days")
        c.metric("Orders to review", f"{open_orders if exposure > 0 else 0}")
        if exposure > 0:
            st.warning(f"Scenario signal: {selected} may constrain production for approximately {exposure:.1f} days within the selected planning horizon.")
        else:
            st.success("No stock-cover constraint detected within this illustrative horizon.")
        if st.button("Create production review task"):
            log_event("PRODUCTION", f"Production review requested for {selected}; {open_orders} orders flagged for review.")
            st.success("Review task recorded in the activity log.")
    else:
        st.info("Add inventory data to run a production scenario.")

elif page == "Department Coordination":
    page_header("COORDINATION / TEAMS", "Department Coordination", "Turn an operational signal into clear, accountable work across teams.")
    departments = ["Procurement","Production","Sales","Warehouse","Finance","Quality"]
    with st.form("task_form"):
        title = st.text_input("Task title", placeholder="Review Paper Grade A replenishment")
        department = st.selectbox("Department", departments)
        owner = st.text_input("Assigned to", placeholder="Role or team member")
        due = st.date_input("Due date", value=date.today()+timedelta(days=1))
        priority = st.selectbox("Priority", ["Critical","High","Normal"])
        submitted = st.form_submit_button("Create coordination task", type="primary")
    if submitted:
        if title.strip():
            log_event("COORDINATION", f"{department} task created: {title} · Owner: {owner or 'Unassigned'} · Due: {due} · {priority}")
            st.success("Task added to the operational activity log.")
        else:
            st.error("Please enter a task title.")
    st.markdown("### Recent coordination activity")
    activity = load_activity(20)
    if not activity.empty:
        st.dataframe(activity[activity["category"].isin(["COORDINATION","PRODUCTION","APPROVAL"])], use_container_width=True, hide_index=True)
    else:
        st.caption("No coordination activity yet.")

elif page == "Supplier Intelligence":
    page_header("NETWORK / SUPPLIERS", "Supplier Intelligence", "Maintain supplier context and make lead-time exposure visible.")
    suppliers = load_suppliers()
    st.markdown('<div class="panel"><div class="panel-title">Supplier register</div>', unsafe_allow_html=True)
    edited_suppliers = st.data_editor(
        suppliers, use_container_width=True, hide_index=True, num_rows="dynamic",
        column_config={
            "name": st.column_config.TextColumn("Supplier", required=True),
            "material": st.column_config.TextColumn("Material"),
            "lead_time": st.column_config.NumberColumn("Lead time (days)", min_value=0),
            "reliability": st.column_config.NumberColumn("Reliability (%)", min_value=0, max_value=100),
            "contact": st.column_config.TextColumn("Contact"),
            "status": st.column_config.SelectboxColumn("Status", options=["Active","Watch","Inactive"]),
        }, key="supplier_editor"
    )
    if st.button("Save supplier register", type="primary"):
        save_suppliers(edited_suppliers)
        st.success("Supplier register saved.")
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    if not suppliers.empty:
        avg = float(suppliers["reliability"].mean())
        st.metric("Average recorded supplier reliability", f"{avg:.1f}%")
        st.caption("Reliability values are demonstration inputs, not independently verified supplier performance.")

elif page == "AI Assistant":
    page_header("FLOWOS / ASSISTANT", "Operational AI Assistant", "Ask about the operational picture. Responses below are generated from local rules and sample data, not a live language model.")
    prompt = st.text_area("What would you like to understand?", placeholder="Which materials need attention first?")
    if st.button("Analyse operations", type="primary"):
        if not prompt.strip():
            st.warning("Enter a question first.")
        else:
            current = enrich_inventory(load_inventory())
            if current.empty:
                st.info("Add inventory data before asking for an operational analysis.")
            else:
                risks = current[current["risk"] != "HEALTHY"].sort_values("days_cover")
                if any(x in prompt.lower() for x in ["supplier","vendor","lead"]):
                    answer = "Supplier lead time is compared with days of stock cover. Review suppliers linked to critical or watchlist materials before changing a purchase plan."
                elif any(x in prompt.lower() for x in ["reorder","buy","purchase"]):
                    answer = "Reorder suggestions are illustrative calculations based on lead time plus a 3-day buffer. Check open POs, MOQ, demand variability and supplier reliability before approval."
                elif risks.empty:
                    answer = "Current sample inventory shows no critical or watchlist materials. Continue monitoring usage and supplier lead times."
                else:
                    names = ", ".join([f'{r["material"]} ({r["days_cover"]:.1f} days cover)' for _,r in risks.iterrows()])
                    answer = f"Priority review: {names}. These materials have less than the lead-time-plus-buffer target. Check incoming purchase orders and production commitments, then request human approval for any replenishment."
                st.markdown(f'<div class="panel"><div class="panel-title">FLOWOS ANALYSIS</div><div style="font-size:14px;line-height:1.8;color:#e0ebf7">{html.escape(answer)}</div><div class="small-muted" style="margin-top:12px">Method: deterministic rules over locally stored demonstration data.</div></div>', unsafe_allow_html=True)
                log_event("ASSISTANT", "Operational analysis generated.")

    st.markdown("### Supplier communication draft")
    material_names = data["material"].tolist() if not data.empty else []
    if material_names:
        m = st.selectbox("Material for supplier follow-up", material_names)
        rr = data[data["material"] == m].iloc[0]
        if st.button("Draft supplier follow-up"):
            supplier = str(rr["supplier"])
            draft = f"""Subject: Request for delivery confirmation — {m}

Hello {supplier} team,

We are reviewing our material coverage for {m}. Our current planning data indicates approximately {rr['days_cover']:.1f} days of stock cover against a listed lead time of {rr['lead_time']:.1f} days.

Could you please confirm the expected dispatch and delivery dates, and let us know whether an earlier delivery or partial shipment is possible?

Please share any constraints or changes to the schedule so our operations team can plan accordingly.

Regards,
Procurement Team
"""
            st.text_area("Editable draft — not sent", value=draft, height=220)
            st.caption("This is a draft only. FlowOS does not send messages.")

elif page == "Chain Reaction Engine":
    page_header("SIGNATURE WORKFLOW / WHAT-IF", "Operational Chain Reaction Engine", "Inject a disruption and trace how one event can affect inventory, production and teams.")
    st.warning("Demonstration simulation. No supplier, ERP, purchase order or customer system is contacted.")
    if not data.empty:
        selected = st.selectbox("Simulate a supplier delay for", data["material"].tolist())
        delay = st.slider("Additional supplier delay (days)", 1, 14, 3)
        # Never display a result for a previous material or delay setting.
        previous_result = st.session_state.get("chain_result")
        if previous_result and (previous_result.get("material") != selected or previous_result.get("delay") != delay):
            st.session_state.pop("chain_result", None)
        r = data[data["material"] == selected].iloc[0]
        if st.button("Run chain reaction", type="primary"):
            cover = float(r["days_cover"])
            new_lead = float(r["lead_time"]) + delay
            gap = max(0, new_lead - cover)
            st.session_state["chain_result"] = {
                "material": selected, "cover": cover, "lead": new_lead, "gap": gap,
                "supplier": str(r["supplier"]), "delay": delay,
                "daily_usage": float(r["daily_usage"]), "unit": str(r["unit"])
            }
            log_event("SIMULATION", f"Simulated {delay}-day supplier delay for {selected}.")
        result = st.session_state.get("chain_result")
        if result:
            steps = [
                ("01","SUPPLIER EVENT",f'{result["supplier"]} reports a simulated {result["delay"]}-day delay.'),
                ("02","INVENTORY SIGNAL",f'{result["material"]} has {result["cover"]:.1f} days of stock cover versus {result["lead"]:.1f} days revised lead time.'),
                ("03","PRODUCTION EXPOSURE",f'Illustrative supply gap: {result["gap"]:.1f} days. Review production commitments and open orders.'),
                ("04","TEAM COORDINATION","Recommended review tasks for procurement, production and sales. No tasks are sent automatically."),
                ("05","HUMAN DECISION","FlowOS prepares a recommendation. A person must review and approve any consequential action.")
            ]
            for n,title,desc in steps:
                st.markdown(f'<div class="panel" style="display:flex;gap:16px;align-items:flex-start"><div style="font:500 18px DM Mono;color:#5bbbe8">{n}</div><div><div class="panel-title" style="margin:0 0 5px">{title}</div><div style="font-size:13px;color:#c4d4e6">{html.escape(desc)}</div></div></div>', unsafe_allow_html=True)
            proposed_qty = max(0, result["gap"] * float(result["daily_usage"]))
            if proposed_qty > 0:
                if st.button("Create human review approval"):
                    create_approval(result["material"], proposed_qty, "Chain Reaction Engine scenario review")
                    st.success("Human review approval created. No external action was taken.")
                    st.rerun()
            else:
                st.info("No supply gap is predicted in this scenario, so no approval request is required.")
    else:
        st.info("Add inventory records to run the scenario.")

elif page == "Approvals & Audit":
    page_header("GOVERNANCE / CONTROL", "Approvals & Audit", "Keep consequential operational decisions visible, reviewable and under human control.")
    approvals = load_approvals()
    if approvals.empty:
        st.info("No approval requests yet. Create one from Inventory Intelligence or the Chain Reaction Engine.")
    else:
        st.dataframe(approvals, use_container_width=True, hide_index=True)
        pending = approvals[approvals["status"] == "Pending"]
        if not pending.empty:
            st.markdown("### Review pending decision")
            selected_id = st.selectbox("Approval request", pending["id"].tolist(), format_func=lambda x: f"#{x} · {pending[pending['id']==x].iloc[0]['material']}")
            note = st.text_input("Decision note (optional)")
            a,b = st.columns(2)
            with a:
                if st.button("Approve recommendation", type="primary", use_container_width=True):
                    update_approval(selected_id, "Approved", note)
                    st.success("Marked approved in the local demo audit log. No purchase order was sent.")
                    st.rerun()
            with b:
                if st.button("Reject recommendation", use_container_width=True):
                    update_approval(selected_id, "Rejected", note)
                    st.warning("Recommendation rejected and recorded.")
                    st.rerun()
    st.markdown("### Audit trail")
    activity = load_activity(50)
    if not activity.empty:
        st.dataframe(activity, use_container_width=True, hide_index=True)

elif page == "Help & Support":
    page_header("SUPPORT / KNOWLEDGE", "How can we help?", "A clear path to understand the prototype, troubleshoot data and get operational support.")
    a,b = st.columns(2, gap="large")
    with a:
        st.markdown('<div class="panel"><div class="panel-title">Getting started</div><h3 style="font-size:18px">Set up your operational picture</h3><p>1. Import an inventory CSV or Excel file.<br>2. Review days of stock cover and risk.<br>3. Check supplier lead times.<br>4. Create an approval request.<br>5. Run a disruption scenario.<br>6. Visit FAQ for product and data limitations.</p></div>', unsafe_allow_html=True)
        st.markdown('<div class="panel"><div class="panel-title">Data and privacy</div><p>This prototype stores its demonstration records in a local SQLite database beside app.py. It does not connect to an external ERP or send supplier emails.</p></div>', unsafe_allow_html=True)
    with b:
        st.markdown('<div class="panel"><div class="panel-title">Support request</div>', unsafe_allow_html=True)
        with st.form("support_form"):
            name = st.text_input("Your name or team")
            email = st.text_input("Work email (optional)")
            topic = st.selectbox("Topic", ["Inventory data","Supplier information","Approvals","Production workflow","Technical issue","Other"])
            message = st.text_area("How can we help?")
            send = st.form_submit_button("Record support request", type="primary")
        if send:
            if message.strip():
                log_event("SUPPORT", f"Support request from {name or 'User'} · {topic}: {message[:240]}")
                st.success("Support request recorded locally in the activity log. No email was sent.")
            else:
                st.error("Please describe the issue.")
        st.markdown('</div>', unsafe_allow_html=True)

elif page == "Plans & Subscription":
    page_header("FLOWOS / PLANS", "Plans built around operational maturity", "A proposed pricing structure for growing manufacturers. Choose a starting point, then scale as workflows mature.")
    st.info("Pricing below is illustrative product planning, not an active subscription checkout. No payment is collected and no plan is activated from this prototype.")
    plans = [
        {"name":"Starter", "price":"₹2,499", "period":"per workspace / month", "audience":"For small teams organising operational data", "features":["Inventory register and CSV imports", "Stock-cover and risk dashboard", "Supplier register and lead-time tracking", "Basic disruption scenarios", "Human-reviewed approval log", "Email support (planned)"], "tag":"START SMALL"},
        {"name":"Growth", "price":"₹7,999", "period":"per workspace / month", "audience":"For growing manufacturers coordinating multiple teams", "features":["Everything in Starter", "Expanded department coordination", "Advanced scenario history and comparisons (planned)", "Custom operational reports (planned)", "Multiple team workspaces (planned)", "Priority support (planned)"], "tag":"GROW WITH FLOWOS"},
        {"name":"Enterprise", "price":"Custom", "period":"based on scope and deployment", "audience":"For organisations with integration and governance needs", "features":["Tailored workflows and role permissions (planned)", "ERP and business-system integrations (planned)", "Advanced audit and governance (planned)", "Deployment and onboarding support (planned)", "Service-level options (planned)", "Dedicated account support (planned)"], "tag":"TAILORED"},
    ]
    cols = st.columns(3, gap="large")
    for col, plan in zip(cols, plans):
        with col:
            st.markdown('<div class="panel" style="height:100%;min-height:390px">', unsafe_allow_html=True)
            st.markdown(f'<div class="eyebrow">{html.escape(plan["tag"])}</div><h2 style="font-size:24px;margin:0">{html.escape(plan["name"])}</h2><div style="font-size:29px;font-weight:800;color:#edf5ff;margin:14px 0 2px">{html.escape(plan["price"])}</div><div class="small-muted">{html.escape(plan["period"])}</div><p style="font-size:12px;margin-top:12px">{html.escape(plan["audience"])}</p><hr>', unsafe_allow_html=True)
            for feature in plan["features"]:
                st.markdown(f'<div style="font-size:12px;color:#c6d7e9;padding:5px 0">✓ {html.escape(feature)}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
    st.markdown("### What is included in the current prototype?")
    st.write("The current demonstration build includes the core navigation, local sample workspace, inventory calculations, supplier register, rule-based assistant, disruption simulation and human approval log. Subscription billing, account management, cloud persistence, production-grade security and the features marked planned are not implemented.")
    st.markdown("### Request a product discussion")
    with st.form("plans_interest_form"):
        contact_name = st.text_input("Name or organisation")
        contact_email = st.text_input("Contact email (optional)")
        desired_plan = st.selectbox("Plan of interest", ["Starter", "Growth", "Enterprise", "Not sure yet"])
        use_case = st.text_area("What would you want FlowOS to help with?")
        interest_submit = st.form_submit_button("Record interest in this demo", type="primary")
    if interest_submit:
        if not contact_name.strip() or not use_case.strip():
            st.error("Please enter a name or organisation and a short description of your use case.")
        else:
            log_event("PLAN INTEREST", f"{contact_name.strip()[:80]} · {desired_plan} · {use_case.strip()[:220]}")
            st.success("Interest recorded locally in this demo workspace. No email was sent and no subscription was created.")

elif page == "FAQ":
    page_header("FLOWOS / KNOWLEDGE BASE", "Frequently asked questions", "Straight answers about what FlowOS is designed to do, how the prototype works and what is still on the roadmap.")
    faq_groups = {
        "About FlowOS": [
            ("What is FlowOS?", "FlowOS is an operational-intelligence concept designed to connect business information, surface potential risks, simulate disruptions and support better-informed decisions."),
            ("Who is FlowOS designed for?", "The initial target segment is small and medium-sized manufacturers that need a clearer view of inventory, suppliers and production-related risks."),
            ("Does FlowOS replace an ERP?", "No. The intended direction is to complement existing ERP and business systems with an operational risk and decision-support layer."),
            ("What makes FlowOS different?", "The product direction prioritises a focused interface, understandable risk signals, practical scenario walkthroughs and human-reviewed decisions. These advantages still need validation with real customers."),
        ],
        "Data, integrations and security": [
            ("Does the current prototype connect to SAP, Oracle or Microsoft Dynamics?", "No. The current prototype does not connect to live ERP systems. Imports are performed using a defined CSV or Excel inventory structure."),
            ("Does FlowOS use real supplier data?", "No. The included supplier and inventory records are fictional demonstration data. Users can enter their own data in the current demo workspace, but should not upload confidential business information."),
            ("Is the prototype secure for confidential company data?", "It is not production-grade or enterprise-certified. The demo uses a local SQLite database associated with a browser session and does not provide authenticated multi-user access or enterprise security controls."),
            ("Will my data always be saved?", "The current deployment uses prototype storage and hosting that may be temporary. Do not rely on it for permanent business records or backups."),
        ],
        "Intelligence and decisions": [
            ("Is the AI Assistant a live generative AI model?", "No. In this prototype, the assistant uses deterministic rules and local demonstration data. A live AI model is a possible future development, not a current feature."),
            ("Are the disruption simulations predictions?", "No. They are illustrative what-if calculations based on entered stock cover and supplier lead time. They do not forecast actual delivery outcomes."),
            ("Does FlowOS automatically place purchase orders?", "No. The prototype can create a local approval record. It does not send purchase orders, supplier messages or external instructions."),
            ("How is stock risk calculated?", "Stock cover is current stock divided by average daily usage. It is compared with supplier lead time and an illustrative three-day buffer. The estimate does not account for every real-world planning factor."),
        ],
        "Plans and support": [
            ("Are the prices on the Plans page live prices?", "No. They are illustrative proposed prices for product planning. There is no active payment, billing or subscription service in this prototype."),
            ("Can I upgrade or cancel a subscription?", "Subscription management is not implemented yet. No subscription can be purchased or cancelled through this demo."),
            ("How do I request support?", "Use Help & Support to record a local support request in the demo activity log. It does not send an email."),
            ("How can I suggest a feature?", "Use the support form and select the relevant topic. Feedback is recorded locally in the current demonstration workspace."),
        ],
    }
    for group_name, questions in faq_groups.items():
        st.markdown(f"### {group_name}")
        for question, answer in questions:
            with st.expander(question):
                st.write(answer)
    st.markdown('<div class="panel"><div class="panel-title">Prototype transparency</div><p>FlowOS is currently a demonstration prototype. It uses sample data, deterministic calculations and local records. Live ERP integrations, generative AI, predictive models, payment processing, permanent cloud storage and external messaging are not active features.</p></div>', unsafe_allow_html=True)

# ---------- Footer ----------
st.markdown("""
<hr>
<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;color:#7892ad;font:10px 'DM Mono',monospace;letter-spacing:.5px">
<span>FLOWOS · CONNECT. PREDICT. PROGRESS.</span>
<span>LOCAL DEMONSTRATION BUILD · HUMAN-IN-THE-LOOP</span>
</div>
""", unsafe_allow_html=True)
