"""
Smart Hospital Emergency Resource Manager (HT-03)
Flask + SQLite REST API. All hospital data is simulated / fictional.
Run:  python app.py   then open http://localhost:5000
"""
import json
import os
import random
import sqlite3
import time

from flask import Flask, g, jsonify, request

from allocator import ORDER, plan

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emergency.db")
SPECIALTIES = ["Emergency", "Cardiology", "Surgery", "Pediatrics", "General"]
WARDS = {"ICU": (6, 4), "ER": (10, 6), "General": (12, 8), "Isolation": (4, 1)}  # (beds, occupied)
AMBULANCE_RETURN_SECONDS = 8
CYCLE_DOCTOR = {"free": "busy", "busy": "off", "off": "free"}
CYCLE_AMBULANCE = {"free": "dispatched", "dispatched": "returning", "returning": "free"}

app = Flask(__name__, static_folder="static", static_url_path="")

SCHEMA = """
CREATE TABLE categories(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
    weight REAL NOT NULL, max_wait REAL NOT NULL, color TEXT NOT NULL);
CREATE TABLE beds(id TEXT PRIMARY KEY, ward TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'free');
CREATE TABLE doctors(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
    specialty TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'free');
CREATE TABLE ambulances(id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'free', returning_since REAL);
CREATE TABLE cases(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, age INTEGER NOT NULL,
    complaint TEXT NOT NULL, category_id INTEGER NOT NULL, need TEXT NOT NULL, specialty TEXT NOT NULL,
    transport INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
    bed_id TEXT, doctor_id INTEGER, ambulance_id TEXT, excluded TEXT NOT NULL DEFAULT '[]',
    allocated_at REAL, completed_at REAL);
CREATE TABLE log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, msg TEXT NOT NULL);
"""


# ----------------------------------------------------------------- database helpers
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def q(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def log(msg):
    db().execute("INSERT INTO log(ts,msg) VALUES(?,?)", (time.time(), msg))


def init_db(reset=False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    if conn.execute("SELECT name FROM sqlite_master WHERE name='cases'").fetchone():
        conn.close()
        return
    conn.executescript(SCHEMA)
    now = time.time()
    conn.executemany("INSERT INTO categories(name,weight,max_wait,color) VALUES(?,?,?,?)", [
        ("Critical", 100, 0, "#dc2626"), ("Urgent", 60, 15, "#f59e0b"),
        ("Semi-urgent", 30, 60, "#3b82f6"), ("Minor", 10, 120, "#10b981")])
    for ward, (total, occupied) in WARDS.items():
        conn.executemany("INSERT INTO beds VALUES(?,?,?)", [
            (f"{ward}-{i + 1}", ward, "occ" if i < occupied else "free") for i in range(total)])
    conn.executemany("INSERT INTO doctors(name,specialty,status) VALUES(?,?,?)", [
        ("Dr. Ayesha Khan", "Emergency", "free"), ("Dr. Bilal Ahmed", "Cardiology", "free"),
        ("Dr. Sana Malik", "Surgery", "free"), ("Dr. Usman Raza", "Pediatrics", "free"),
        ("Dr. Hina Shah", "General", "free"), ("Dr. Imran Ali", "Emergency", "busy"),
        ("Dr. Farah Noor", "Cardiology", "busy"), ("Dr. Zain Abbas", "Surgery", "off")])
    conn.executemany("INSERT INTO ambulances(id,status) VALUES(?,?)", [
        ("AMB-01", "free"), ("AMB-02", "free"), ("AMB-03", "dispatched"),
        ("AMB-04", "free"), ("AMB-05", "returning")])
    seeds = [  # name, age, complaint, category_id, need, specialty, transport, minutes waiting
        ("Ali Raza", 58, "Chest pain, sweating", 1, "ICU", "Cardiology", 1, 3),
        ("Baby Hira", 3, "High fever, seizure", 1, "ER", "Pediatrics", 0, 6),
        ("Kamran Shah", 34, "Road accident, suspected fracture", 2, "ER", "Surgery", 1, 12),
        ("Rukhsana Bibi", 71, "Severe breathlessness (possible infection)", 2, "Isolation", "General", 0, 20),
        ("Zeeshan Iqbal", 22, "Deep cut on hand", 3, "General", "Emergency", 0, 25)]
    conn.executemany(
        "INSERT INTO cases(name,age,complaint,category_id,need,specialty,transport,created_at) VALUES(?,?,?,?,?,?,?,?)",
        [(n, a, c, cat, nd, sp, tr, now - m * 60) for n, a, c, cat, nd, sp, tr, m in seeds])
    conn.execute("INSERT INTO log(ts,msg) VALUES(?,?)", (now, "System started with simulated hospital data"))
    conn.commit()
    conn.close()


# ----------------------------------------------------------------- state / planning
def load_all():
    now = time.time()
    db().execute("UPDATE ambulances SET status='free', returning_since=NULL "
                 "WHERE status='returning' AND returning_since IS NOT NULL AND returning_since<?",
                 (now - AMBULANCE_RETURN_SECONDS,))
    db().commit()
    return {
        "now": now,
        "cats": q("SELECT * FROM categories ORDER BY weight DESC"),
        "beds": q("SELECT * FROM beds ORDER BY ward, CAST(substr(id, instr(id,'-')+1) AS INTEGER)"),
        "doctors": q("SELECT * FROM doctors ORDER BY id"),
        "ambulances": q("SELECT * FROM ambulances ORDER BY id"),
        "cases": q("SELECT * FROM cases WHERE status!='done' ORDER BY id"),
    }


def make_plan(s):
    return plan(s["cases"], s["beds"], s["doctors"], s["ambulances"], s["cats"], s["now"])


def err(msg, code=400):
    return jsonify({"error": msg}), code


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/state")
def state():
    s = load_all()
    s["plans"] = make_plan(s)
    waiting = [c for c in s["cases"] if c["status"] == "waiting"]
    top = s["cats"][0]["id"] if s["cats"] else None
    alloc = q("SELECT created_at, allocated_at FROM cases WHERE allocated_at IS NOT NULL")
    s["stats"] = {
        "waiting": len(waiting),
        "top_priority_waiting": sum(1 for c in waiting if c["category_id"] == top),
        "beds_free": sum(1 for b in s["beds"] if b["status"] == "free"), "beds_total": len(s["beds"]),
        "doctors_free": sum(1 for d in s["doctors"] if d["status"] == "free"), "doctors_total": len(s["doctors"]),
        "ambulances_free": sum(1 for a in s["ambulances"] if a["status"] == "free"),
        "ambulances_total": len(s["ambulances"]),
        "avg_wait_min": round(sum((s["now"] - c["created_at"]) / 60 for c in waiting) / len(waiting), 1) if waiting else 0,
        "completed": q("SELECT COUNT(*) n FROM cases WHERE status='done'")[0]["n"],
        "avg_time_to_allocation_min": round(sum((a["allocated_at"] - a["created_at"]) / 60 for a in alloc) / len(alloc), 1) if alloc else 0,
    }
    s["log"] = [{"ts": r["ts"], "msg": r["msg"]} for r in q("SELECT ts,msg FROM log ORDER BY id DESC LIMIT 40")]
    return jsonify(s)


# ----------------------------------------------------------------- cases
def insert_case(d, name, age, complaint, category_id, need, specialty, transport):
    cur = d.execute(
        "INSERT INTO cases(name,age,complaint,category_id,need,specialty,transport,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (name, age, complaint, category_id, need, specialty, int(bool(transport)), time.time()))
    log(f"New case registered: {name}")
    return cur.lastrowid


@app.post("/api/cases")
def create_case():
    b = request.get_json(silent=True) or {}
    try:
        name, complaint = str(b["name"]).strip(), str(b["complaint"]).strip()
        age, cat = int(b["age"]), int(b["category_id"])
    except (KeyError, ValueError, TypeError):
        return err("name, age, complaint and category_id are required")
    if not name or not complaint or not 0 <= age <= 120:
        return err("Invalid name, complaint or age")
    if b.get("need") not in ORDER or b.get("specialty") not in SPECIALTIES:
        return err("Invalid ward or specialty")
    if not q("SELECT 1 FROM categories WHERE id=?", (cat,)):
        return err("Unknown category")
    cid = insert_case(db(), name, age, complaint, cat, b["need"], b["specialty"], b.get("transport"))
    db().commit()
    return jsonify({"id": cid}), 201


@app.post("/api/cases/<int:cid>/approve")
def approve(cid):
    s = load_all()
    case = next((c for c in s["cases"] if c["id"] == cid and c["status"] == "waiting"), None)
    if not case:
        return err("Case not found or not waiting", 404)
    rec = make_plan(s)[cid]
    if not rec["bed"]:
        return err("No suitable bed available for this case", 409)
    d = db()
    d.execute("UPDATE beds SET status='occ' WHERE id=?", (rec["bed"]["id"],))
    if rec["doctor"]:
        d.execute("UPDATE doctors SET status='busy' WHERE id=?", (rec["doctor"]["id"],))
    if rec["ambulance"]:
        d.execute("UPDATE ambulances SET status='dispatched' WHERE id=?", (rec["ambulance"],))
    d.execute("UPDATE cases SET status='allocated', bed_id=?, doctor_id=?, ambulance_id=?, allocated_at=? WHERE id=?",
              (rec["bed"]["id"], rec["doctor"] and rec["doctor"]["id"], rec["ambulance"], time.time(), cid))
    parts = [rec["bed"]["id"]] + ([rec["doctor"]["name"]] if rec["doctor"] else []) + ([rec["ambulance"]] if rec["ambulance"] else [])
    log(f"Approved: {case['name']} -> {', '.join(parts)}")
    d.commit()
    return jsonify({"ok": True})


@app.post("/api/cases/<int:cid>/reject")
def reject(cid):
    s = load_all()
    case = next((c for c in s["cases"] if c["id"] == cid and c["status"] == "waiting"), None)
    if not case:
        return err("Case not found or not waiting", 404)
    rec = make_plan(s)[cid]
    if rec["bed"]:
        excluded = json.loads(case["excluded"]) + [rec["bed"]["id"]]
        db().execute("UPDATE cases SET excluded=? WHERE id=?", (json.dumps(excluded), cid))
        log(f"Staff rejected {rec['bed']['id']} for {case['name']}; recalculated")
        db().commit()
    return jsonify({"ok": True})


def complete_case(case):
    d = db()
    d.execute("UPDATE cases SET status='done', completed_at=? WHERE id=?", (time.time(), case["id"]))
    if case["bed_id"]:
        d.execute("UPDATE beds SET status='free' WHERE id=?", (case["bed_id"],))
    if case["doctor_id"]:
        d.execute("UPDATE doctors SET status='free' WHERE id=?", (case["doctor_id"],))
    if case["ambulance_id"]:
        d.execute("UPDATE ambulances SET status='returning', returning_since=? WHERE id=?",
                  (time.time(), case["ambulance_id"]))
    log(f"{case['name']} completed; resources released")


@app.post("/api/cases/<int:cid>/complete")
def complete(cid):
    rows = q("SELECT * FROM cases WHERE id=? AND status='allocated'", (cid,))
    if not rows:
        return err("Case not found or not allocated", 404)
    complete_case(rows[0])
    db().commit()
    return jsonify({"ok": True})


# ----------------------------------------------------------------- resources
@app.post("/api/beds/<bid>/toggle")
def toggle_bed(bid):
    rows = q("SELECT * FROM beds WHERE id=?", (bid,))
    if not rows:
        return err("Bed not found", 404)
    if q("SELECT 1 FROM cases WHERE status='allocated' AND bed_id=?", (bid,)):
        return err("Bed is assigned to an active case. Complete the case first.", 409)
    new = "free" if rows[0]["status"] == "occ" else "occ"
    db().execute("UPDATE beds SET status=? WHERE id=?", (new, bid))
    log(f"Bed {bid} manually set {'available' if new == 'free' else 'occupied'}")
    db().commit()
    return jsonify({"status": new})


@app.post("/api/doctors/<int:did>/cycle")
def cycle_doctor(did):
    rows = q("SELECT * FROM doctors WHERE id=?", (did,))
    if not rows:
        return err("Doctor not found", 404)
    new = CYCLE_DOCTOR[rows[0]["status"]]
    db().execute("UPDATE doctors SET status=? WHERE id=?", (new, did))
    log(f"{rows[0]['name']} is now {new}")
    db().commit()
    return jsonify({"status": new})


@app.post("/api/ambulances/<aid>/cycle")
def cycle_ambulance(aid):
    rows = q("SELECT * FROM ambulances WHERE id=?", (aid,))
    if not rows:
        return err("Ambulance not found", 404)
    new = CYCLE_AMBULANCE[rows[0]["status"]]
    db().execute("UPDATE ambulances SET status=?, returning_since=? WHERE id=?",
                 (new, time.time() if new == "returning" else None, aid))
    log(f"{aid} is now {new}")
    db().commit()
    return jsonify({"status": new})


@app.put("/api/categories")
def save_categories():
    items = request.get_json(silent=True)
    if not isinstance(items, list) or not items:
        return err("Send a non-empty list of categories")
    d = db()
    for it in items:
        try:
            name, weight, max_wait, color = str(it["name"]).strip(), float(it["weight"]), float(it["max_wait"]), str(it["color"])
        except (KeyError, ValueError, TypeError):
            return err("Each category needs name, weight, max_wait, color")
        if not name:
            return err("Category name cannot be empty")
        if it.get("id"):
            d.execute("UPDATE categories SET name=?,weight=?,max_wait=?,color=? WHERE id=?",
                      (name, weight, max_wait, color, int(it["id"])))
        else:
            d.execute("INSERT INTO categories(name,weight,max_wait,color) VALUES(?,?,?,?)", (name, weight, max_wait, color))
    log("Priority categories updated")
    d.commit()
    return jsonify({"ok": True})


# ----------------------------------------------------------------- simulation
SIM_POOL = [  # name, age, complaint, category name, ward, specialty, transport
    ("Nadia", 31, "Severe bleeding", "Critical", "ER", "Surgery", 1),
    ("Tariq", 66, "Stroke symptoms", "Critical", "ICU", "Emergency", 1),
    ("Maryam", 9, "Asthma attack", "Urgent", "ER", "Emergency", 0),
    ("Hamza", 27, "Burn injury", "Urgent", "ER", "Surgery", 1),
    ("Sara", 19, "Vomiting, dehydration", "Semi-urgent", "General", "General", 0),
    ("Owais", 16, "Sprained ankle", "Minor", "General", "Emergency", 0),
    ("Little Ayan", 4, "Fever", "Semi-urgent", "General", "Pediatrics", 0),
]


@app.post("/api/simulate/tick")
def simulate_tick():
    cats = q("SELECT * FROM categories")
    if random.random() < 0.6 and cats:
        n, age, c, catname, ward, sp, tr = random.choice(SIM_POOL)
        cat = next((x for x in cats if x["name"] == catname), min(cats, key=lambda x: x["weight"]))
        insert_case(db(), n, age, c, cat["id"], ward, sp, tr)
    active = q("SELECT * FROM cases WHERE status='allocated'")
    if active and random.random() < 0.4:
        complete_case(random.choice(active))
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/reset")
def reset():
    conn = g.pop("db", None)
    if conn:
        conn.close()
    init_db(reset=True)
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
