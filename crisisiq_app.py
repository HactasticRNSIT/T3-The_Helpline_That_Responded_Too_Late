# ============================================================
#  CrisisIQ — AI Emergency Intelligence System
#  SINGLE FILE VERSION — no folders, no imports needed
#  Run: python -m streamlit run crisisiq_app.py
# ============================================================

import re
import sqlite3
import os
import time
import streamlit as st
import pandas as pd
from datetime import datetime

# ─────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crisisiq.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS incidents (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        transcript TEXT NOT NULL,
        severity  TEXT NOT NULL,
        priority  TEXT NOT NULL,
        dispatch  TEXT NOT NULL,
        panic     REAL DEFAULT 0.0,
        context   REAL DEFAULT 0.0,
        timestamp TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS resources (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        type     TEXT NOT NULL,
        status   TEXT DEFAULT 'available',
        location TEXT DEFAULT 'Central Station'
    )''')
    c.execute("SELECT COUNT(*) FROM resources")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO resources (type,status,location) VALUES (?,?,?)", [
            ('Ambulance','available','City Hospital'),
            ('Ambulance','available','East Wing Station'),
            ('Police',   'available','Central Police HQ'),
            ('Police',   'available','North Precinct'),
            ('Fire Truck','available','Fire Station 1'),
            ('Fire Truck','available','Fire Station 2'),
        ])
    conn.commit()
    conn.close()

def insert_incident(transcript, severity, priority, dispatch, panic=0.0, ctx=0.0):
    conn = get_conn()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO incidents (transcript,severity,priority,dispatch,panic,context,timestamp) VALUES (?,?,?,?,?,?,?)",
        (transcript, severity, priority, dispatch, panic, ctx, ts)
    )
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return iid

def fetch_all():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM incidents ORDER BY timestamp DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────
#  NLP AGENT
# ─────────────────────────────────────────────
SEVERITY_KW = {
    "Critical": ["not breathing","no pulse","unconscious","collapsed","cardiac arrest",
                 "heart attack","stroke","choking","drowning","not responding",
                 "bleeding heavily","severe bleeding","overdose","poisoned","dying",
                 "stopped breathing","unresponsive","murder","suicide"],
    "High":     ["fire","burning","explosion","stabbed","shot","gunshot","shooting",
                 "attacked","severe pain","chest pain","difficulty breathing",
                 "head injury","broken bone","fracture","electrocuted","trapped","crash"],
    "Medium":   ["robbery","theft","stolen","burglar","assault","fight","violence",
                 "threatening","harassing","missing person","minor injury","fever"],
    "Low":      ["noise complaint","minor accident","parking","suspicious","argument",
                 "stray animal","minor fall","headache","minor cut","smoke smell"]
}
INCIDENT_KW = {
    "medical":  ["breathing","pulse","unconscious","collapsed","heart","stroke","choking",
                 "overdose","poison","bleeding","injury","pain","sick","fainted","seizure"],
    "fire":     ["fire","burning","smoke","flames","explosion","gas leak","burnt"],
    "crime":    ["robbery","theft","stolen","burglar","assault","attack","shot","stabbed",
                 "gun","knife","threat","murder","kidnap"],
    "accident": ["accident","crash","collision","car","vehicle","road","trapped","hit"]
}
PANIC_HIGH = ["help","please","hurry","quick","fast","dying","dead","emergency",
              "now","immediately","screaming","crying","scared","terrified","oh god","oh no"]
PANIC_MED  = ["worried","afraid","concerned","urgent","bad","serious","need help"]
CTX_WORDS  = ["night","alone","isolated","dark","no one","nobody","street",
              "alley","forest","remote","locked","trapped","weapon","armed"]

def analyze(transcript):
    t = transcript.lower().strip()
    severity = "Low"
    for lvl in ["Critical","High","Medium","Low"]:
        if any(kw in t for kw in SEVERITY_KW[lvl]):
            severity = lvl
            break
    scores = {k: sum(1 for kw in v if kw in t) for k,v in INCIDENT_KW.items()}
    best = max(scores, key=scores.get)
    incident_type = best if scores[best] > 0 else "general"
    panic = 0.0
    panic += min(t.count("!") * 0.1, 0.3)
    panic += min(sum(1 for c in transcript if c.isupper()) / max(len(transcript),1) * 0.5, 0.2)
    for w in PANIC_HIGH:
        if w in t: panic += 0.15
    for w in PANIC_MED:
        if w in t: panic += 0.08
    repeated = re.findall(r'(\b\w+\b)(?:\s+\1){1,}', t)
    panic += len(repeated) * 0.05
    panic = round(min(panic, 1.0), 2)
    ctx = round(min(sum(0.12 for w in CTX_WORDS if w in t), 1.0), 2)
    return severity, incident_type, panic, ctx

# ─────────────────────────────────────────────
#  PRIORITY AGENT
# ─────────────────────────────────────────────
SEV_W = {"Critical":1.0,"High":0.75,"Medium":0.5,"Low":0.25}

def calc_priority(severity, panic, ctx):
    score = 0.4*SEV_W.get(severity,0.25) + 0.3*panic + 0.2*ctx
    if score >= 0.65: return "P1"
    if score >= 0.40: return "P2"
    return "P3"

# ─────────────────────────────────────────────
#  DISPATCH AGENT
# ─────────────────────────────────────────────
DISPATCH = {
    "medical":"Ambulance","fire":"Fire Truck",
    "crime":"Police","accident":"Ambulance + Police","general":"Police"
}
OVERRIDE = {
    "Critical":{"medical":"Ambulance + Paramedic Unit","accident":"Ambulance + Fire Truck + Police"},
    "High":    {"fire":"Fire Truck + Ambulance","crime":"Police + Armed Response"}
}
ETA = {"P1":"3-5 min","P2":"8-12 min","P3":"15-20 min"}

def get_dispatch(severity, incident_type):
    return OVERRIDE.get(severity,{}).get(incident_type, DISPATCH.get(incident_type,"Police"))

# ─────────────────────────────────────────────
#  PROCESS INCIDENT — full pipeline
# ─────────────────────────────────────────────
def process_incident(transcript):
    severity, itype, panic, ctx = analyze(transcript)
    priority = calc_priority(severity, panic, ctx)
    dispatch = get_dispatch(severity, itype)
    iid = insert_incident(transcript, severity, priority, dispatch, panic, ctx)
    return {
        "id": iid, "transcript": transcript,
        "severity": severity, "priority": priority,
        "dispatch": dispatch, "panic": panic,
        "context": ctx, "eta": ETA.get(priority,"—")
    }

# ─────────────────────────────────────────────
#  COLORS / STYLE HELPERS
# ─────────────────────────────────────────────
PC = {"P1":"#FF2D2D","P2":"#FF9900","P3":"#2DCC70"}
SC = {"Critical":"#FF2D2D","High":"#FF9900","Medium":"#F1C40F","Low":"#2DCC70"}
PBG= {"P1":"#2d0a0a","P2":"#2d1a00","P3":"#0a2d1a"}

# ─────────────────────────────────────────────
#  STREAMLIT APP
# ─────────────────────────────────────────────
init_db()

st.set_page_config(
    page_title="CrisisIQ",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Exo+2:wght@400;700;900&family=Share+Tech+Mono&display=swap');
html, body, [class*="css"] { font-family:'Exo 2',sans-serif; background:#0a0e1a; color:#e0e6f0; }
.stApp { background:#0a0e1a; }
div[data-testid="stSidebar"] { background:#0d1220 !important; border-right:1px solid #1e2d45; }
.stTextArea textarea, .stTextInput input {
    background:#111827 !important; color:#e0e6f0 !important;
    border:1px solid #1e2d45 !important; border-radius:8px !important;
}
.stButton>button {
    background:linear-gradient(135deg,#FF2D2D,#cc0000) !important;
    color:white !important; border:none !important; border-radius:8px !important;
    font-weight:700 !important; letter-spacing:1px !important;
    padding:0.6rem 2rem !important;
}
.stButton>button:hover { opacity:0.85 !important; }
h1,h2,h3 { font-family:'Exo 2',sans-serif !important; }
.stTabs [data-baseweb="tab"] { font-family:'Exo 2',sans-serif; color:#7a8aaa; }
.stTabs [aria-selected="true"] { color:#FF2D2D !important; }
div[data-testid="stMetricValue"] { color:#4a9eff; font-size:2rem !important; font-weight:900 !important; }
</style>
""", unsafe_allow_html=True)

# ── SIDEBAR ──────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="text-align:center;padding:1rem 0;">
      <div style="font-size:2.5rem;">🚨</div>
      <div style="font-family:'Exo 2',sans-serif;font-weight:900;font-size:1.4rem;
                  color:#FF2D2D;letter-spacing:3px;">CrisisIQ</div>
      <div style="color:#7a8aaa;font-size:0.7rem;letter-spacing:2px;
                  font-family:'Share Tech Mono',monospace;">EMERGENCY INTELLIGENCE</div>
    </div>
    <hr style="border-color:#1e2d45;">
    """, unsafe_allow_html=True)

    page = st.radio("", ["🆘 Report Incident", "📊 Live Dashboard"], label_visibility="collapsed")

    st.markdown("<hr style='border-color:#1e2d45;'>", unsafe_allow_html=True)
    st.markdown("""
    <div style="background:#111827;border-radius:8px;padding:0.7rem 1rem;border:1px solid #1e4d2b;">
      <div style="color:#7a8aaa;font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;">SYSTEM</div>
      <div style="color:#2DCC70;font-weight:700;">● ONLINE — No API needed</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""
    <div style="background:#111827;border-radius:8px;padding:0.8rem 1rem;border:1px solid #1e2d45;">
      <div style="color:#7a8aaa;font-size:0.7rem;text-transform:uppercase;
                  letter-spacing:1px;margin-bottom:0.5rem;">PRIORITY GUIDE</div>
      <div style="color:#FF2D2D;font-weight:700;font-size:0.85rem;">P1 — CRITICAL (&ge;0.65)</div>
      <div style="color:#FF9900;font-weight:700;font-size:0.85rem;margin-top:3px;">P2 — HIGH (&ge;0.40)</div>
      <div style="color:#2DCC70;font-weight:700;font-size:0.85rem;margin-top:3px;">P3 — STANDARD</div>
    </div>
    """, unsafe_allow_html=True)

# ── PAGE: REPORT ─────────────────────────────
if "Report" in page:
    st.markdown("""
    <div style="text-align:center;padding:1.5rem 0 0.5rem;">
      <h1 style="color:#FF2D2D;letter-spacing:4px;font-weight:900;
                 text-shadow:0 0 20px rgba(255,45,45,0.4);margin:0;">🚨 CrisisIQ</h1>
      <p style="color:#7a8aaa;font-family:'Share Tech Mono',monospace;
                font-size:0.8rem;letter-spacing:2px;">AI EMERGENCY INTELLIGENCE SYSTEM</p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([1.2, 0.8], gap="large")

    with col1:
        st.markdown("#### 📝 Describe the Emergency")
        user_input = st.text_area(
            "", height=140,
            placeholder='e.g. "My father collapsed and is not breathing, please hurry!"',
            label_visibility="collapsed"
        )
        submitted = st.button("🚨 SUBMIT EMERGENCY", use_container_width=True)

        if submitted:
            if not user_input.strip():
                st.error("Please describe the emergency.")
            else:
                with st.spinner("Analyzing emergency..."):
                    result = process_incident(user_input.strip())

                p  = result["priority"]
                s  = result["severity"]
                pc = PC.get(p,"#aaa")
                sc = SC.get(s,"#aaa")
                pb = PBG.get(p,"#111")

                st.markdown(f"""
                <div style="background:linear-gradient(135deg,#111827,#1a2235);border-radius:12px;
                            padding:1.4rem 1.6rem;border-left:5px solid {pc};
                            box-shadow:0 4px 20px rgba(0,0,0,0.4);margin-top:1rem;">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
                    <span style="font-size:1.1rem;font-weight:700;color:{pc};">
                      🚨 INCIDENT #{result['id']} REGISTERED
                    </span>
                    <span style="background:{pb};color:{pc};padding:3px 14px;border-radius:20px;
                                 font-weight:700;font-size:0.85rem;border:1px solid {pc}44;
                                 font-family:'Share Tech Mono',monospace;">{p}</span>
                  </div>
                  <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:1rem;margin-bottom:1rem;">
                    <div>
                      <div style="color:#7a8aaa;font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;">SEVERITY</div>
                      <div style="color:{sc};font-weight:700;font-size:1rem;">{s}</div>
                    </div>
                    <div>
                      <div style="color:#7a8aaa;font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;">DISPATCH</div>
                      <div style="color:#e0e6f0;font-weight:700;font-size:0.95rem;">🚒 {result['dispatch']}</div>
                    </div>
                    <div>
                      <div style="color:#7a8aaa;font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;">ETA</div>
                      <div style="color:#4a9eff;font-weight:700;font-size:1rem;">⏱ {result['eta']}</div>
                    </div>
                    <div>
                      <div style="color:#7a8aaa;font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;">PANIC</div>
                      <div style="color:#e0e6f0;font-weight:700;font-size:1rem;">{result['panic']:.2f}</div>
                    </div>
                  </div>
                  <div style="background:#0a0e1a;border-radius:6px;padding:0.6rem 0.8rem;">
                    <span style="color:#7a8aaa;font-size:0.7rem;text-transform:uppercase;letter-spacing:1px;">TRANSCRIPT</span>
                    <div style="color:#c0cce0;font-style:italic;margin-top:2px;">"{result['transcript']}"</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)

    with col2:
        st.markdown("#### ⚡ Quick Test Scenarios")
        scenarios = [
            ("🔴 Critical — Not Breathing",   "My father collapsed and is not breathing please hurry help"),
            ("🟠 High — Building Fire",        "There is a massive fire in the building people are trapped and screaming"),
            ("🟡 Medium — Armed Robbery",      "Someone just robbed the store on Main Street with a knife threatening people"),
            ("🟢 Low — Minor Accident",        "Minor fender bender in the parking lot no injuries just some damage"),
            ("🔴 Critical — Overdose",         "My friend is unconscious and not responding I think she overdosed please come now"),
            ("🟠 High — Gunshot Wound",        "Man has been shot and is bleeding heavily on the street"),
        ]
        for label, text in scenarios:
            if st.button(label, use_container_width=True, key=label):
                with st.spinner("Analyzing..."):
                    r = process_incident(text)
                p_col = PC.get(r['priority'],'#aaa')
                st.markdown(f"""
                <div style="background:#111827;border-radius:8px;padding:0.7rem 1rem;
                            border-left:3px solid {p_col};margin-bottom:0.3rem;">
                  <span style="color:{p_col};font-weight:700;">{r['priority']}</span>
                  <span style="color:#7a8aaa;font-size:0.85rem;"> · {r['severity']} · </span>
                  <span style="color:#e0e6f0;font-size:0.85rem;">{r['dispatch']}</span>
                  <span style="color:#4a9eff;font-size:0.8rem;float:right;">⏱ {r['eta']}</span>
                </div>
                """, unsafe_allow_html=True)

# ── PAGE: DASHBOARD ──────────────────────────
elif "Dashboard" in page:
    st.markdown("""
    <h2 style="color:#FF2D2D;letter-spacing:3px;font-weight:900;margin:0.5rem 0 0 0;">
      📊 LIVE INCIDENT DASHBOARD
    </h2>
    <p style="color:#7a8aaa;font-family:'Share Tech Mono',monospace;
              font-size:0.78rem;letter-spacing:2px;margin:0 0 1rem 0;">
      REAL-TIME EMERGENCY MONITORING
    </p>
    """, unsafe_allow_html=True)

    ctrl1, ctrl2, ctrl3 = st.columns([1,1,3])
    with ctrl1:
        auto_ref = st.toggle("Auto Refresh", value=False)
    with ctrl2:
        interval = st.selectbox("", [10,30,60], label_visibility="collapsed")
    with ctrl3:
        if st.button("🔄 Refresh Now"):
            st.rerun()

    st.markdown("<hr style='border-color:#1e2d45;margin:0.5rem 0;'>", unsafe_allow_html=True)

    incidents = fetch_all()
    total  = len(incidents)
    p1_cnt = sum(1 for i in incidents if i["priority"]=="P1")
    p2_cnt = sum(1 for i in incidents if i["priority"]=="P2")
    p3_cnt = sum(1 for i in incidents if i["priority"]=="P3")
    crit   = sum(1 for i in incidents if i["severity"]=="Critical")

    m1,m2,m3,m4,m5 = st.columns(5)
    for col, val, lbl, color in [
        (m1,total, "TOTAL",       "#4a9eff"),
        (m2,p1_cnt,"P1 CRITICAL", "#FF2D2D"),
        (m3,p2_cnt,"P2 HIGH",     "#FF9900"),
        (m4,p3_cnt,"P3 STANDARD", "#2DCC70"),
        (m5,crit,  "CRITICAL SEV","#FF2D2D"),
    ]:
        with col:
            st.markdown(f"""
            <div style="background:#111827;border-radius:10px;padding:1rem 0.5rem;
                        text-align:center;border:1px solid #1e2d45;">
              <div style="font-size:2rem;font-weight:900;color:{color};
                          font-family:'Exo 2',sans-serif;">{val}</div>
              <div style="color:#7a8aaa;font-size:0.68rem;letter-spacing:1px;
                          text-transform:uppercase;margin-top:2px;">{lbl}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🃏 Incident Cards", "📋 Data Table"])

    with tab1:
        if not incidents:
            st.markdown("""
            <div style="text-align:center;padding:3rem;color:#7a8aaa;">
              <div style="font-size:3rem;">📭</div>
              <div style="margin-top:0.5rem;">No incidents yet. Submit one from the Report page.</div>
            </div>
            """, unsafe_allow_html=True)
        for inc in incidents[:20]:
            p  = inc["priority"]; s = inc["severity"]
            pc = PC.get(p,"#aaa"); sc2 = SC.get(s,"#aaa"); pb = PBG.get(p,"#111")
            txt = inc["transcript"][:110] + ("…" if len(inc["transcript"])>110 else "")
            dispatch_icon = "🚑" if "Ambulance" in inc["dispatch"] else "🚔" if "Police" in inc["dispatch"] else "🚒"
            st.markdown(f"""
            <div style="background:linear-gradient(135deg,#111827,#1a2235);border-radius:12px;
                        padding:1rem 1.2rem;margin-bottom:0.7rem;border-left:4px solid {pc};
                        box-shadow:0 2px 12px rgba(0,0,0,0.3);">
              <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.5rem;">
                <span style="background:{pb};color:{pc};padding:2px 10px;border-radius:20px;
                             font-size:0.78rem;font-weight:700;border:1px solid {pc}44;
                             font-family:'Share Tech Mono',monospace;">{p}</span>
                <span style="color:{sc2};font-weight:700;font-size:0.9rem;">{s}</span>
                <span style="color:#7a8aaa;font-size:0.78rem;margin-left:auto;">#{inc['id']} · {inc['timestamp']}</span>
              </div>
              <div style="color:#c0cce0;font-size:0.88rem;font-style:italic;margin-bottom:0.5rem;">"{txt}"</div>
              <div style="display:flex;gap:1.5rem;">
                <span style="color:#7a8aaa;font-size:0.78rem;">{dispatch_icon}
                  <span style="color:#e0e6f0;">{inc['dispatch']}</span></span>
                <span style="color:#7a8aaa;font-size:0.78rem;">😰
                  <span style="color:#e0e6f0;">{float(inc['panic']):.2f}</span></span>
              </div>
            </div>
            """, unsafe_allow_html=True)

    with tab2:
        if incidents:
            df = pd.DataFrame([{
                "ID":       i["id"],
                "Priority": i["priority"],
                "Severity": i["severity"],
                "Dispatch": i["dispatch"],
                "Panic":    round(float(i["panic"]),2),
                "Time":     i["timestamp"],
                "Report":   i["transcript"][:60]+"…"
            } for i in incidents])

            def color_priority(val):
                m = {"P1":"background-color:#2d0a0a;color:#FF2D2D",
                     "P2":"background-color:#2d1a00;color:#FF9900",
                     "P3":"background-color:#0a2d1a;color:#2DCC70"}
                return m.get(val,"")

            def color_severity(val):
                m = {"Critical":"color:#FF2D2D","High":"color:#FF9900",
                     "Medium":"color:#F1C40F","Low":"color:#2DCC70"}
                return m.get(val,"")

            styled = df.style.map(color_priority, subset=["Priority"])\
                             .map(color_severity, subset=["Severity"])
            st.dataframe(styled, use_container_width=True, height=400)
        else:
            st.info("No incidents yet.")

    if auto_ref:
        time.sleep(interval)
        st.rerun()
