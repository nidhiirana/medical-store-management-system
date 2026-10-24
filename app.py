"""
Medical Store Management System  -  MediCare Pharmacy
Flask + SQLite + Bootstrap 5

Run:  python app.py        (then open http://127.0.0.1:5000)
"""
import csv
import io
import json
import os
import secrets
import sqlite3
import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from seed_data import seed

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.environ.get("MSMS_DB", os.path.join(INSTANCE_DIR, "medical_store.db"))

EXPIRY_ALERT_DAYS = 30          # "expiring soon" window shown on dashboard / alerts
PAYMENT_MODES = ["Cash", "UPI", "Card"]
CATEGORIES = ["Analgesic", "Antibiotic", "Antihistamine", "Antacid", "Antidiabetic", "Cardiac",
              "Vitamins", "Cough & Cold", "Respiratory", "First Aid", "Surgical", "General"]

STORE = {
    "name": "MediCare Pharmacy",
    "tagline": "Better Health • Brighter Tomorrow",
    "address": "G-14, Mahaveer Heights, beside Bhagvan Mahaveer college, Vesu, Surat, Gujarat 395007",
    "phone": "+91 98765 43210",
    "email": "medicarepharmacy@gmail.com",
}

app = Flask(__name__, instance_path=INSTANCE_DIR)


def _load_secret_key():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    path = os.path.join(INSTANCE_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(path) as fh:
        return fh.read().strip()


app.config.update(
    SECRET_KEY=_load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=1024 * 1024,
)


# --------------------------------------------------------------------------- #
# Database helpers
# --------------------------------------------------------------------------- #
def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, isolation_level=None, timeout=15)  # manual transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


class transaction:
    """with transaction(db): ...  -> BEGIN IMMEDIATE / COMMIT, ROLLBACK on error."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def init_db(path=None):
    """Create tables and, on a brand-new database, load the sample data."""
    os.makedirs(os.path.dirname(path or DB_PATH), exist_ok=True)
    db = connect(path)
    try:
        with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as fh:
            db.executescript(fh.read())
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            with transaction(db):
                seed(db)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def today():
    return date.today()


def q2(value):
    """Decimal -> 2dp, half-up (money rounding)."""
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_decimal(value, default=None):
    try:
        d = Decimal(str(value).strip())
        return d if d.is_finite() else default
    except (InvalidOperation, AttributeError):
        return default


def to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def medicine_state(row, ref=None):
    """Return dict(status, days_left) for a medicine row.
    status: expired | expiring | out | low | ok   (expiry has priority)."""
    ref = ref or today()
    exp = parse_date(row["expiry_date"])
    days_left = (exp - ref).days if exp else None
    if days_left is not None and days_left <= 0:
        status = "expired"
    elif days_left is not None and days_left <= EXPIRY_ALERT_DAYS:
        status = "expiring"
    elif row["quantity"] <= 0:
        status = "out"
    elif row["quantity"] <= row["reorder_level"]:
        status = "low"
    else:
        status = "ok"
    return {"status": status, "days_left": days_left}


def decorate(rows):
    out = []
    for r in rows:
        d = dict(r)
        d.update(medicine_state(r))
        out.append(d)
    return out


def is_expired(expiry_date, ref=None):
    exp = parse_date(expiry_date)
    return exp is None or exp <= (ref or today())


@app.template_filter("inr")
def inr_filter(v):
    try:
        return "₹{:,.2f}".format(float(v or 0))
    except (TypeError, ValueError):
        return "₹0.00"


@app.template_filter("dmy")
def dmy_filter(v):
    d = parse_date(str(v)[:10]) if v else None
    return d.strftime("%d %b %Y") if d else (v or "")


@app.template_filter("dtime")
def dtime_filter(v):
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").strftime("%d %b %Y, %I:%M %p")
    except (ValueError, TypeError):
        return v or ""


# --------------------------------------------------------------------------- #
# Security: auth, roles, CSRF, login throttling
# --------------------------------------------------------------------------- #
def current_user():
    if "user" not in g:
        g.user = None
        uid = session.get("user_id")
        if uid:
            g.user = get_db().execute("SELECT id,name,username,role FROM users WHERE id=?", (uid,)).fetchone()
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if current_user() is None:
            session.pop("user_id", None)
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.full_path.rstrip("?") if request.method == "GET" else None))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*a, **kw):
        if current_user()["role"] != "admin":
            flash("Only the store admin can perform that action.", "danger")
            return redirect(request.referrer or url_for("dashboard"))
        return view(*a, **kw)
    return wrapped


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Security token missing or expired. Please go back, refresh the page and try again.")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if current_user_cached():
        resp.headers["Cache-Control"] = "no-store"
    return resp


def current_user_cached():
    return g.get("user")


_failed = {}   # ip -> [count, first_ts]
MAX_FAILS, LOCK_SECONDS = 5, 300


def _locked(ip):
    rec = _failed.get(ip)
    if not rec:
        return 0
    if time.time() - rec[1] > LOCK_SECONDS:
        _failed.pop(ip, None)
        return 0
    return max(0, int(LOCK_SECONDS - (time.time() - rec[1]))) if rec[0] >= MAX_FAILS else 0


def safe_next(target):
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


# --------------------------------------------------------------------------- #
# Stock alerts (StockAlert class in the class diagram)
# --------------------------------------------------------------------------- #
def sync_alerts(db):
    """Keep stock_alerts in step with the medicines table.
    Alerts disappear automatically when the condition is fixed; read-state is kept."""
    ref = today()
    now = ref.isoformat()
    desired = {}
    for m in db.execute("SELECT id,name,batch_no,quantity,reorder_level,expiry_date FROM medicines"):
        st = medicine_state(m, ref)
        label = f"{m['name']} (batch {m['batch_no']})"
        if st["status"] == "expired":
            desired[(m["id"], "expired")] = f"{label} expired on {dmy_filter(m['expiry_date'])} - remove from shelf."
        elif st["status"] == "expiring":
            desired[(m["id"], "expiring_soon")] = f"{label} expires in {st['days_left']} day(s) on {dmy_filter(m['expiry_date'])}."
        if m["quantity"] <= 0:
            desired[(m["id"], "out_of_stock")] = f"{label} is out of stock."
        elif m["quantity"] <= m["reorder_level"]:
            desired[(m["id"], "low_stock")] = f"{label} is low: {m['quantity']} left (reorder level {m['reorder_level']})."
    existing = {(r["medicine_id"], r["type"]): r for r in db.execute("SELECT * FROM stock_alerts")}
    if set(existing) == set(desired) and all(existing[k]["message"] == v for k, v in desired.items()):
        return
    with transaction(db):
        for k, r in existing.items():
            if k not in desired:
                db.execute("DELETE FROM stock_alerts WHERE id=?", (r["id"],))
        for (mid, typ), msg in desired.items():
            if (mid, typ) in existing:
                if existing[(mid, typ)]["message"] != msg:
                    db.execute("UPDATE stock_alerts SET message=? WHERE medicine_id=? AND type=?", (msg, mid, typ))
            else:
                db.execute("INSERT INTO stock_alerts (medicine_id,type,message,alert_date) VALUES (?,?,?,?)",
                           (mid, typ, msg, now))


@app.context_processor
def inject_globals():
    ctx = {"store": STORE, "csrf_token": csrf_token, "today": today(), "current_user": current_user(),
           "payment_modes": PAYMENT_MODES, "unread_alerts": 0}
    if ctx["current_user"] is not None and request.endpoint != "static":
        db = get_db()
        sync_alerts(db)
        ctx["unread_alerts"] = db.execute("SELECT COUNT(*) FROM stock_alerts WHERE is_read=0").fetchone()[0]
    return ctx


# --------------------------------------------------------------------------- #
# Login / logout
# --------------------------------------------------------------------------- #
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        ip = request.remote_addr or "?"
        wait = _locked(ip)
        if wait:
            flash(f"Too many failed attempts. Try again in {wait // 60 + 1} minute(s).", "danger")
            return render_template("login.html"), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            _failed.pop(ip, None)
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            csrf_token()
            flash(f"Welcome back, {user['name']}!", "success")
            return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
        rec = _failed.setdefault(ip, [0, time.time()])
        rec[0] += 1
        flash("Invalid username or password.", "danger")
        return render_template("login.html", username=username), 401
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def sales_total(db, where, args=()):
    r = db.execute(f"SELECT COUNT(*) c, COALESCE(SUM(total_amount),0) t FROM sales WHERE {where}", args).fetchone()
    return r["c"], r["t"]


@app.route("/")
@login_required
def dashboard():
    db = get_db()
    t = today()
    meds = decorate(db.execute("SELECT * FROM medicines").fetchall())
    stats = {
        "total_medicines": len(meds),
        "total_units": sum(m["quantity"] for m in meds),
        "stock_value": sum(m["quantity"] * m["price"] for m in meds),
        "low": sum(1 for m in meds if m["quantity"] > 0 and m["quantity"] <= m["reorder_level"]),
        "out": sum(1 for m in meds if m["quantity"] <= 0),
        "expired": sum(1 for m in meds if m["status"] == "expired"),
        "expiring": sum(1 for m in meds if m["status"] == "expiring"),
        "suppliers": db.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0],
        "customers": db.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
    }
    td_count, td_total = sales_total(db, "date(sale_date)=?", (t.isoformat(),))
    mo_count, mo_total = sales_total(db, "strftime('%Y-%m',sale_date)=?", (t.strftime("%Y-%m"),))
    all_count, all_total = sales_total(db, "1=1")

    # last 7 days
    series = []
    for i in range(6, -1, -1):
        d = t - timedelta(days=i)
        c, tot = sales_total(db, "date(sale_date)=?", (d.isoformat(),))
        series.append({"label": d.strftime("%a"), "date": d.strftime("%d %b"), "total": tot, "count": c})
    peak = max([s["total"] for s in series] + [1])
    for s in series:
        s["pct"] = round(s["total"] / peak * 100)

    recent = db.execute("SELECT * FROM sales ORDER BY id DESC LIMIT 6").fetchall()
    top = db.execute(
        "SELECT si.medicine_name, SUM(si.quantity) units, SUM(si.subtotal) revenue FROM sale_items si "
        "JOIN sales s ON s.id=si.sale_id WHERE date(s.sale_date) >= ? GROUP BY si.medicine_name "
        "ORDER BY units DESC LIMIT 5", ((t - timedelta(days=29)).isoformat(),)).fetchall()
    low_list = sorted([m for m in meds if m["quantity"] <= m["reorder_level"] and m["status"] != "expired"],
                      key=lambda m: m["quantity"])[:6]
    exp_list = sorted([m for m in meds if m["status"] in ("expired", "expiring")], key=lambda m: m["days_left"])[:6]
    return render_template("dashboard.html", stats=stats, td_count=td_count, td_total=td_total,
                           mo_count=mo_count, mo_total=mo_total, all_count=all_count, all_total=all_total,
                           series=series, recent=recent, top=list(top), low_list=low_list, exp_list=exp_list)


# --------------------------------------------------------------------------- #
# Public: medicine availability search (Customer actor in the use-case diagram)
# --------------------------------------------------------------------------- #
@app.route("/availability")
def availability():
    q = request.args.get("q", "").strip()
    results = None
    if q:
        rows = get_db().execute(
            "SELECT id,name,category,price,quantity,reorder_level,expiry_date FROM medicines "
            "WHERE name LIKE ? OR category LIKE ? ORDER BY name", (f"%{q}%", f"%{q}%")).fetchall()
        results = []
        for r in decorate(rows):
            r["available"] = r["status"] not in ("expired",) and r["quantity"] > 0
            results.append(r)
    return render_template("availability.html", q=q, results=results)


# --------------------------------------------------------------------------- #
# Medicines (FR-1)
# --------------------------------------------------------------------------- #
def suppliers_list(db):
    return db.execute("SELECT id,name FROM suppliers ORDER BY name").fetchall()


def validate_medicine(form, db):
    errors, data = [], {}
    data["name"] = form.get("name", "").strip()
    data["batch_no"] = form.get("batch_no", "").strip()
    data["category"] = form.get("category", "").strip() or "General"
    if not data["name"]:
        errors.append("Medicine name is required.")
    if not data["batch_no"]:
        errors.append("Batch number is required.")
    price = to_decimal(form.get("price"))
    if price is None or price < 0 or price > 1000000:
        errors.append("Enter a valid price (0 - 10,00,000).")
    else:
        data["price"] = float(q2(price))
    qty = to_int(form.get("quantity"))
    if qty is None or qty < 0 or qty > 10000000:
        errors.append("Quantity must be a whole number, 0 or more.")
    else:
        data["quantity"] = qty
    rl = to_int(form.get("reorder_level"), 10) if form.get("reorder_level", "").strip() else 10
    if rl is None or rl < 0:
        errors.append("Reorder level must be a whole number, 0 or more.")
    else:
        data["reorder_level"] = rl
    exp = parse_date(form.get("expiry_date"))
    if not exp:
        errors.append("Enter a valid expiry date.")
    else:
        data["expiry_date"] = exp.isoformat()
    sid = form.get("supplier_id", "").strip()
    if sid:
        sid = to_int(sid)
        if sid is None or not db.execute("SELECT 1 FROM suppliers WHERE id=?", (sid,)).fetchone():
            errors.append("Selected supplier does not exist.")
        else:
            data["supplier_id"] = sid
    else:
        data["supplier_id"] = None
    return data, errors


@app.route("/medicines")
@login_required
def medicines():
    db = get_db()
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    cat = request.args.get("category", "")
    sql, args = ("SELECT m.*, s.name AS supplier_name FROM medicines m "
                 "LEFT JOIN suppliers s ON s.id=m.supplier_id WHERE 1=1"), []
    if q:
        sql += " AND (m.name LIKE ? OR m.batch_no LIKE ? OR m.category LIKE ?)"
        args += [f"%{q}%"] * 3
    if cat:
        sql += " AND m.category=?"
        args.append(cat)
    rows = decorate(db.execute(sql + " ORDER BY m.name, m.expiry_date", args).fetchall())
    if status in ("expired", "expiring", "out", "low", "ok"):
        rows = [r for r in rows if r["status"] == status]
    cats = [r[0] for r in db.execute("SELECT DISTINCT category FROM medicines ORDER BY category")]
    return render_template("medicines.html", meds=rows, q=q, status=status, cat=cat, cats=cats)


@app.route("/medicines/add", methods=["GET", "POST"])
@login_required
def medicine_add():
    db = get_db()
    form = request.form if request.method == "POST" else {"reorder_level": 10}
    if request.method == "POST":
        data, errors = validate_medicine(request.form, db)
        if not errors:
            try:
                db.execute(
                    "INSERT INTO medicines (name,batch_no,category,price,quantity,reorder_level,expiry_date,supplier_id)"
                    " VALUES (:name,:batch_no,:category,:price,:quantity,:reorder_level,:expiry_date,:supplier_id)", data)
                flash(f"Medicine '{data['name']}' added.", "success")
                if is_expired(data["expiry_date"]):
                    flash("Note: this batch is already expired and cannot be sold.", "warning")
                return redirect(url_for("medicines"))
            except sqlite3.IntegrityError:
                errors.append("A medicine with the same name and batch number already exists.")
        for e in errors:
            flash(e, "danger")
    return render_template("medicine_form.html", med=None, form=form, suppliers=suppliers_list(db),
                           categories=CATEGORIES)


@app.route("/medicines/<int:mid>/edit", methods=["GET", "POST"])
@login_required
def medicine_edit(mid):
    db = get_db()
    med = db.execute("SELECT * FROM medicines WHERE id=?", (mid,)).fetchone()
    if not med:
        abort(404)
    form = request.form if request.method == "POST" else med
    if request.method == "POST":
        data, errors = validate_medicine(request.form, db)
        if not errors:
            try:
                data["id"] = mid
                db.execute(
                    "UPDATE medicines SET name=:name,batch_no=:batch_no,category=:category,price=:price,"
                    "quantity=:quantity,reorder_level=:reorder_level,expiry_date=:expiry_date,"
                    "supplier_id=:supplier_id WHERE id=:id", data)
                flash(f"Medicine '{data['name']}' updated.", "success")
                return redirect(url_for("medicines"))
            except sqlite3.IntegrityError:
                errors.append("A medicine with the same name and batch number already exists.")
        for e in errors:
            flash(e, "danger")
    return render_template("medicine_form.html", med=med, form=form, suppliers=suppliers_list(db),
                           categories=CATEGORIES)


@app.route("/medicines/<int:mid>/delete", methods=["POST"])
@admin_required
def medicine_delete(mid):
    db = get_db()
    med = db.execute("SELECT name FROM medicines WHERE id=?", (mid,)).fetchone()
    if not med:
        abort(404)
    db.execute("DELETE FROM medicines WHERE id=?", (mid,))
    flash(f"Medicine '{med['name']}' deleted. Past invoices are kept.", "success")
    return redirect(url_for("medicines"))


# --------------------------------------------------------------------------- #
# Inventory & low-stock alerts (FR-1 stock part)
# --------------------------------------------------------------------------- #
@app.route("/inventory")
@login_required
def inventory():
    db = get_db()
    flt = request.args.get("filter", "all")
    q = request.args.get("q", "").strip()
    sql, args = "SELECT m.*, s.name supplier_name FROM medicines m LEFT JOIN suppliers s ON s.id=m.supplier_id WHERE 1=1", []
    if q:
        sql += " AND (m.name LIKE ? OR m.batch_no LIKE ?)"
        args += [f"%{q}%"] * 2
    rows = decorate(db.execute(sql + " ORDER BY m.quantity ASC, m.name", args).fetchall())
    counts = {
        "all": len(rows),
        "low": sum(1 for r in rows if 0 < r["quantity"] <= r["reorder_level"]),
        "out": sum(1 for r in rows if r["quantity"] <= 0),
    }
    if flt == "low":
        rows = [r for r in rows if 0 < r["quantity"] <= r["reorder_level"]]
    elif flt == "out":
        rows = [r for r in rows if r["quantity"] <= 0]
    return render_template("inventory.html", meds=rows, flt=flt, counts=counts, q=q,
                           total_units=sum(r["quantity"] for r in rows))


@app.route("/inventory/<int:mid>/restock", methods=["POST"])
@login_required
def restock(mid):
    db = get_db()
    med = db.execute("SELECT name FROM medicines WHERE id=?", (mid,)).fetchone()
    if not med:
        abort(404)
    qty = to_int(request.form.get("qty"))
    if qty is None or qty <= 0 or qty > 1000000:
        flash("Enter a whole number greater than 0 to add to stock.", "danger")
    else:
        db.execute("UPDATE medicines SET quantity = quantity + ? WHERE id=?", (qty, mid))
        flash(f"Added {qty} unit(s) to {med['name']}.", "success")
    return redirect(safe_next(request.form.get("next")) or url_for("inventory"))


# --------------------------------------------------------------------------- #
# Expiry management
# --------------------------------------------------------------------------- #
@app.route("/expiry")
@login_required
def expiry():
    db = get_db()
    window = to_int(request.args.get("days"), 30)
    window = window if window in (30, 60, 90) else 30
    rows = decorate(db.execute(
        "SELECT m.*, s.name supplier_name FROM medicines m LEFT JOIN suppliers s ON s.id=m.supplier_id "
        "ORDER BY m.expiry_date").fetchall())
    expired = [r for r in rows if r["days_left"] <= 0]
    soon = [r for r in rows if 0 < r["days_left"] <= window]
    loss = sum(r["quantity"] * r["price"] for r in expired)
    return render_template("expiry.html", expired=expired, soon=soon, window=window, loss=loss)


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
@app.route("/alerts")
@login_required
def alerts():
    rows = get_db().execute(
        "SELECT a.*, m.name medicine_name FROM stock_alerts a JOIN medicines m ON m.id=a.medicine_id "
        "ORDER BY a.is_read, CASE a.type WHEN 'expired' THEN 0 WHEN 'out_of_stock' THEN 1 "
        "WHEN 'expiring_soon' THEN 2 ELSE 3 END, m.name").fetchall()
    return render_template("alerts.html", alerts=rows)


@app.route("/alerts/<int:aid>/read", methods=["POST"])
@login_required
def alert_read(aid):
    get_db().execute("UPDATE stock_alerts SET is_read=1 WHERE id=?", (aid,))
    return redirect(url_for("alerts"))


@app.route("/alerts/read-all", methods=["POST"])
@login_required
def alerts_read_all():
    get_db().execute("UPDATE stock_alerts SET is_read=1")
    flash("All alerts marked as read.", "success")
    return redirect(url_for("alerts"))


# --------------------------------------------------------------------------- #
# Suppliers (FR-2)
# --------------------------------------------------------------------------- #
def validate_supplier(form):
    d = {k: form.get(k, "").strip() for k in ("name", "contact", "email", "address")}
    errors = []
    if not d["name"]:
        errors.append("Supplier name is required.")
    if not d["contact"]:
        errors.append("Contact number is required.")
    elif sum(c.isdigit() for c in d["contact"]) < 7:
        errors.append("Enter a valid contact number.")
    if d["email"] and ("@" not in d["email"] or "." not in d["email"].split("@")[-1]):
        errors.append("Enter a valid e-mail address.")
    return d, errors


@app.route("/suppliers")
@login_required
def suppliers():
    q = request.args.get("q", "").strip()
    sql = ("SELECT s.*, COUNT(m.id) med_count FROM suppliers s LEFT JOIN medicines m ON m.supplier_id=s.id "
           "WHERE (s.name LIKE ? OR s.contact LIKE ? OR s.address LIKE ?) GROUP BY s.id ORDER BY s.name")
    rows = get_db().execute(sql, (f"%{q}%",) * 3).fetchall()
    return render_template("suppliers.html", suppliers=rows, q=q)


@app.route("/suppliers/add", methods=["GET", "POST"])
@login_required
def supplier_add():
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        d, errors = validate_supplier(request.form)
        if not errors:
            get_db().execute("INSERT INTO suppliers (name,contact,email,address) VALUES (:name,:contact,:email,:address)", d)
            flash(f"Supplier '{d['name']}' added.", "success")
            return redirect(url_for("suppliers"))
        for e in errors:
            flash(e, "danger")
    return render_template("supplier_form.html", sup=None, form=form)


@app.route("/suppliers/<int:sid>")
@login_required
def supplier_view(sid):
    db = get_db()
    sup = db.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
    if not sup:
        abort(404)
    meds = decorate(db.execute("SELECT * FROM medicines WHERE supplier_id=? ORDER BY name", (sid,)).fetchall())
    return render_template("supplier_view.html", sup=sup, meds=meds)


@app.route("/suppliers/<int:sid>/edit", methods=["GET", "POST"])
@login_required
def supplier_edit(sid):
    db = get_db()
    sup = db.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
    if not sup:
        abort(404)
    form = request.form if request.method == "POST" else sup
    if request.method == "POST":
        d, errors = validate_supplier(request.form)
        if not errors:
            d["id"] = sid
            db.execute("UPDATE suppliers SET name=:name,contact=:contact,email=:email,address=:address WHERE id=:id", d)
            flash(f"Supplier '{d['name']}' updated.", "success")
            return redirect(url_for("suppliers"))
        for e in errors:
            flash(e, "danger")
    return render_template("supplier_form.html", sup=sup, form=form)


@app.route("/suppliers/<int:sid>/delete", methods=["POST"])
@admin_required
def supplier_delete(sid):
    db = get_db()
    sup = db.execute("SELECT name FROM suppliers WHERE id=?", (sid,)).fetchone()
    if not sup:
        abort(404)
    db.execute("DELETE FROM suppliers WHERE id=?", (sid,))
    flash(f"Supplier '{sup['name']}' deleted. Their medicines were kept without a supplier.", "success")
    return redirect(url_for("suppliers"))


# --------------------------------------------------------------------------- #
# Customers & prescriptions (FR-4)
# --------------------------------------------------------------------------- #
def validate_customer(form):
    d = {k: form.get(k, "").strip() for k in ("name", "contact", "address")}
    errors = []
    if not d["name"]:
        errors.append("Customer name is required.")
    if d["contact"] and sum(c.isdigit() for c in d["contact"]) < 7:
        errors.append("Enter a valid contact number.")
    return d, errors


@app.route("/customers")
@login_required
def customers():
    q = request.args.get("q", "").strip()
    rows = get_db().execute(
        "SELECT c.*, (SELECT COUNT(*) FROM sales s WHERE s.customer_id=c.id) purchases, "
        "(SELECT COALESCE(SUM(total_amount),0) FROM sales s WHERE s.customer_id=c.id) spent "
        "FROM customers c WHERE c.name LIKE ? OR c.contact LIKE ? ORDER BY c.name", (f"%{q}%",) * 2).fetchall()
    return render_template("customers.html", customers=rows, q=q)


@app.route("/customers/add", methods=["GET", "POST"])
@login_required
def customer_add():
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        d, errors = validate_customer(request.form)
        if not errors:
            get_db().execute("INSERT INTO customers (name,contact,address) VALUES (:name,:contact,:address)", d)
            flash(f"Customer '{d['name']}' added.", "success")
            return redirect(url_for("customers"))
        for e in errors:
            flash(e, "danger")
    return render_template("customer_form.html", cust=None, form=form)


@app.route("/customers/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def customer_edit(cid):
    db = get_db()
    cust = db.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        abort(404)
    form = request.form if request.method == "POST" else cust
    if request.method == "POST":
        d, errors = validate_customer(request.form)
        if not errors:
            d["id"] = cid
            db.execute("UPDATE customers SET name=:name,contact=:contact,address=:address WHERE id=:id", d)
            flash(f"Customer '{d['name']}' updated.", "success")
            return redirect(url_for("customer_view", cid=cid))
        for e in errors:
            flash(e, "danger")
    return render_template("customer_form.html", cust=cust, form=form)


@app.route("/customers/<int:cid>")
@login_required
def customer_view(cid):
    db = get_db()
    cust = db.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        abort(404)
    sales = db.execute("SELECT * FROM sales WHERE customer_id=? ORDER BY id DESC", (cid,)).fetchall()
    rx = db.execute("SELECT * FROM prescriptions WHERE customer_id=? ORDER BY issued_on DESC, id DESC", (cid,)).fetchall()
    return render_template("customer_view.html", cust=cust, sales=sales, rx=rx,
                           spent=sum(s["total_amount"] for s in sales))


@app.route("/customers/<int:cid>/delete", methods=["POST"])
@admin_required
def customer_delete(cid):
    db = get_db()
    cust = db.execute("SELECT name FROM customers WHERE id=?", (cid,)).fetchone()
    if not cust:
        abort(404)
    db.execute("DELETE FROM customers WHERE id=?", (cid,))
    flash(f"Customer '{cust['name']}' deleted. Their past invoices are kept.", "success")
    return redirect(url_for("customers"))


@app.route("/prescriptions", methods=["GET", "POST"])
@login_required
def prescriptions():
    db = get_db()
    if request.method == "POST":
        cid = to_int(request.form.get("customer_id"))
        doctor = request.form.get("doctor_name", "").strip()
        details = request.form.get("details", "").strip()
        issued = parse_date(request.form.get("issued_on")) or today()
        errors = []
        if cid is None or not db.execute("SELECT 1 FROM customers WHERE id=?", (cid,)).fetchone():
            errors.append("Select a customer.")
        if not doctor:
            errors.append("Doctor name is required.")
        if not details:
            errors.append("Prescription details are required.")
        if issued > today():
            errors.append("Prescription date cannot be in the future.")
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            db.execute("INSERT INTO prescriptions (customer_id,doctor_name,details,issued_on) VALUES (?,?,?,?)",
                       (cid, doctor, details, issued.isoformat()))
            flash("Prescription saved.", "success")
            return redirect(safe_next(request.form.get("next")) or url_for("prescriptions"))
    q = request.args.get("q", "").strip()
    rows = db.execute(
        "SELECT p.*, c.name customer_name FROM prescriptions p JOIN customers c ON c.id=p.customer_id "
        "WHERE c.name LIKE ? OR p.doctor_name LIKE ? OR p.details LIKE ? ORDER BY p.issued_on DESC, p.id DESC",
        (f"%{q}%",) * 3).fetchall()
    custs = db.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
    return render_template("prescriptions.html", rx=rows, customers=custs, q=q,
                           preselect=to_int(request.args.get("customer_id")))


@app.route("/prescriptions/<int:pid>/delete", methods=["POST"])
@login_required
def prescription_delete(pid):
    get_db().execute("DELETE FROM prescriptions WHERE id=?", (pid,))
    flash("Prescription deleted.", "success")
    return redirect(safe_next(request.form.get("next")) or url_for("prescriptions"))


# --------------------------------------------------------------------------- #
# Sales & billing (FR-3)
# --------------------------------------------------------------------------- #
class BillingError(Exception):
    pass


def create_sale(db, user_id, items, customer_id, new_customer, discount_pct, payment_mode, rx):
    """Validate and persist a sale atomically. Prices always come from the database.
    Raises BillingError with a user-friendly message; nothing is saved in that case."""
    if payment_mode not in PAYMENT_MODES:
        raise BillingError("Choose a valid payment mode.")
    if discount_pct is None or discount_pct < 0 or discount_pct > 100:
        raise BillingError("Discount must be between 0 and 100 %.")
    if not items:
        raise BillingError("Add at least one medicine to the bill.")

    merged = {}
    for it in items:
        mid, qty = to_int(it.get("id")), to_int(it.get("qty"))
        if mid is None or qty is None or qty <= 0 or qty > 100000:
            raise BillingError("Every bill line needs a valid medicine and a quantity of 1 or more.")
        merged[mid] = merged.get(mid, 0) + qty

    ref = today()
    with transaction(db):
        lines, subtotal = [], Decimal("0.00")
        for mid, qty in merged.items():
            med = db.execute("SELECT * FROM medicines WHERE id=?", (mid,)).fetchone()
            if not med:
                raise BillingError("A selected medicine no longer exists.")
            if is_expired(med["expiry_date"], ref):
                raise BillingError(f"{med['name']} (batch {med['batch_no']}) is expired and cannot be sold.")
            if med["quantity"] < qty:
                raise BillingError(f"Not enough stock for {med['name']}: requested {qty}, available {med['quantity']}.")
            line = q2(Decimal(str(med["price"])) * qty)
            subtotal += line
            lines.append((med, qty, line))

        subtotal = q2(subtotal)
        disc_amt = q2(subtotal * discount_pct / Decimal(100))
        total = q2(subtotal - disc_amt)

        customer_name = "Walk-in Customer"
        if customer_id:
            c = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
            if not c:
                raise BillingError("Selected customer does not exist.")
            customer_name = c["name"]
        elif new_customer and new_customer.get("name"):
            nc, errs = validate_customer(new_customer)
            if errs:
                raise BillingError(errs[0])
            customer_id = db.execute("INSERT INTO customers (name,contact,address) VALUES (?,?,?)",
                                     (nc["name"], nc["contact"], nc["address"])).lastrowid
            customer_name = nc["name"]
        if rx and (rx["doctor"] or rx["details"]):
            if not customer_id:
                raise BillingError("Select or add a customer to save prescription details.")
            if not (rx["doctor"] and rx["details"]):
                raise BillingError("Prescription needs both the doctor name and details.")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sid = db.execute(
            "INSERT INTO sales (invoice_no,sale_date,customer_id,customer_name,subtotal,discount_pct,discount_amt,"
            "total_amount,payment_mode,user_id) VALUES ('PENDING',?,?,?,?,?,?,?,?,?)",
            (now, customer_id, customer_name, float(subtotal), float(discount_pct), float(disc_amt),
             float(total), payment_mode, user_id)).lastrowid
        db.execute("UPDATE sales SET invoice_no=? WHERE id=?", (f"INV-{sid:05d}", sid))
        for med, qty, line in lines:
            cur = db.execute(
                "UPDATE medicines SET quantity = quantity - ? WHERE id=? AND quantity >= ? AND expiry_date > ?",
                (qty, med["id"], qty, ref.isoformat()))
            if cur.rowcount != 1:
                raise BillingError(f"{med['name']} could not be sold (stock changed). Please retry.")
            db.execute(
                "INSERT INTO sale_items (sale_id,medicine_id,medicine_name,batch_no,quantity,price,subtotal) "
                "VALUES (?,?,?,?,?,?,?)", (sid, med["id"], med["name"], med["batch_no"], qty, med["price"], float(line)))
        if rx and rx["doctor"] and rx["details"]:
            db.execute("INSERT INTO prescriptions (customer_id,sale_id,doctor_name,details,issued_on) VALUES (?,?,?,?,?)",
                       (customer_id, sid, rx["doctor"], rx["details"], ref.isoformat()))
    return sid


@app.route("/billing", methods=["GET", "POST"])
@login_required
def billing():
    db = get_db()
    if request.method == "POST":
        try:
            try:
                items = json.loads(request.form.get("items", "[]"))
                if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                    raise ValueError
            except ValueError:
                raise BillingError("The bill could not be read. Please add the medicines again.")
            cust = request.form.get("customer_id", "").strip()
            cust_id = to_int(cust) if cust and cust != "new" else None
            new_c = None
            if cust == "new":
                new_c = {"name": request.form.get("new_name", "").strip(),
                         "contact": request.form.get("new_contact", "").strip(), "address": ""}
                if not new_c["name"]:
                    raise BillingError("Enter the new customer's name or choose Walk-in.")
            disc = to_decimal(request.form.get("discount_pct") or "0")
            rx = {"doctor": request.form.get("rx_doctor", "").strip(), "details": request.form.get("rx_details", "").strip()}
            sid = create_sale(db, session["user_id"], items, cust_id, new_c, disc,
                              request.form.get("payment_mode", ""), rx)
            flash("Bill generated and stock updated.", "success")
            return redirect(url_for("invoice", sid=sid))
        except BillingError as e:
            flash(str(e), "danger")
    meds = decorate(db.execute("SELECT * FROM medicines ORDER BY name, expiry_date").fetchall())
    catalog = [{"id": m["id"], "name": m["name"], "batch": m["batch_no"], "category": m["category"],
                "price": m["price"], "stock": m["quantity"], "expiry": m["expiry_date"],
                "sellable": m["status"] != "expired" and m["quantity"] > 0,
                "status": m["status"]} for m in meds]
    custs = db.execute("SELECT id,name,contact FROM customers ORDER BY name").fetchall()
    return render_template("billing.html", catalog=catalog, customers=custs,
                           preselect=to_int(request.args.get("customer_id")))


@app.route("/sales")
@login_required
def sales():
    db = get_db()
    q = request.args.get("q", "").strip()
    d_from, d_to = parse_date(request.args.get("from")), parse_date(request.args.get("to"))
    sql, args = "SELECT s.*, u.name cashier FROM sales s LEFT JOIN users u ON u.id=s.user_id WHERE 1=1", []
    if q:
        sql += " AND (s.invoice_no LIKE ? OR s.customer_name LIKE ?)"
        args += [f"%{q}%"] * 2
    if d_from:
        sql += " AND date(s.sale_date) >= ?"
        args.append(d_from.isoformat())
    if d_to:
        sql += " AND date(s.sale_date) <= ?"
        args.append(d_to.isoformat())
    rows = db.execute(sql + " ORDER BY s.id DESC LIMIT 300", args).fetchall()
    return render_template("sales.html", sales=rows, q=q, d_from=d_from, d_to=d_to,
                           total=sum(r["total_amount"] for r in rows))


@app.route("/sales/<int:sid>")
@login_required
def invoice(sid):
    db = get_db()
    sale = db.execute("SELECT s.*, u.name cashier FROM sales s LEFT JOIN users u ON u.id=s.user_id WHERE s.id=?",
                      (sid,)).fetchone()
    if not sale:
        abort(404)
    items = db.execute("SELECT * FROM sale_items WHERE sale_id=? ORDER BY id", (sid,)).fetchall()
    cust = db.execute("SELECT * FROM customers WHERE id=?", (sale["customer_id"],)).fetchone() if sale["customer_id"] else None
    rx = db.execute("SELECT * FROM prescriptions WHERE sale_id=?", (sid,)).fetchone()
    return render_template("invoice.html", sale=sale, items=items, cust=cust, rx=rx,
                           units=sum(i["quantity"] for i in items))


# --------------------------------------------------------------------------- #
# Reports (daily / monthly sales + stock)
# --------------------------------------------------------------------------- #
def csv_response(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/reports")
@login_required
def reports():
    return redirect(url_for("report_daily"))


@app.route("/reports/daily")
@login_required
def report_daily():
    db = get_db()
    day = parse_date(request.args.get("date")) or today()
    rows = db.execute("SELECT s.*, u.name cashier FROM sales s LEFT JOIN users u ON u.id=s.user_id "
                      "WHERE date(s.sale_date)=? ORDER BY s.sale_date", (day.isoformat(),)).fetchall()
    if request.args.get("format") == "csv":
        return csv_response(f"daily_sales_{day}.csv",
                            ["Invoice", "Time", "Customer", "Payment", "Subtotal", "Discount", "Total"],
                            [[r["invoice_no"], r["sale_date"], r["customer_name"], r["payment_mode"], r["subtotal"],
                              r["discount_amt"], r["total_amount"]] for r in rows])
    summary = db.execute(
        "SELECT COUNT(*) bills, COALESCE(SUM(subtotal),0) gross, COALESCE(SUM(discount_amt),0) disc, "
        "COALESCE(SUM(total_amount),0) net FROM sales WHERE date(sale_date)=?", (day.isoformat(),)).fetchone()
    units = db.execute("SELECT COALESCE(SUM(si.quantity),0) FROM sale_items si JOIN sales s ON s.id=si.sale_id "
                       "WHERE date(s.sale_date)=?", (day.isoformat(),)).fetchone()[0]
    modes = db.execute("SELECT payment_mode, COUNT(*) c, SUM(total_amount) t FROM sales WHERE date(sale_date)=? "
                       "GROUP BY payment_mode ORDER BY t DESC", (day.isoformat(),)).fetchall()
    items = db.execute("SELECT si.medicine_name, SUM(si.quantity) units, SUM(si.subtotal) revenue FROM sale_items si "
                       "JOIN sales s ON s.id=si.sale_id WHERE date(s.sale_date)=? GROUP BY si.medicine_name "
                       "ORDER BY units DESC", (day.isoformat(),)).fetchall()
    return render_template("report_daily.html", day=day, rows=rows, summary=summary, units=units, modes=modes,
                           items=items, prev_day=day - timedelta(days=1),
                           next_day=day + timedelta(days=1) if day < today() else None)


@app.route("/reports/monthly")
@login_required
def report_monthly():
    db = get_db()
    raw = request.args.get("month", "")
    try:
        first = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        first = today().replace(day=1)
    last_day = monthrange(first.year, first.month)[1]
    key = first.strftime("%Y-%m")
    by_day = {r["d"]: r for r in db.execute(
        "SELECT date(sale_date) d, COUNT(*) bills, SUM(subtotal) gross, SUM(discount_amt) disc, SUM(total_amount) net "
        "FROM sales WHERE strftime('%Y-%m',sale_date)=? GROUP BY date(sale_date)", (key,))}
    days = []
    for n in range(1, last_day + 1):
        d = first.replace(day=n)
        r = by_day.get(d.isoformat())
        days.append({"date": d, "bills": r["bills"] if r else 0, "gross": r["gross"] if r else 0,
                     "disc": r["disc"] if r else 0, "net": r["net"] if r else 0})
    if request.args.get("format") == "csv":
        return csv_response(f"monthly_sales_{key}.csv", ["Date", "Bills", "Gross", "Discount", "Net sales"],
                            [[x["date"].isoformat(), x["bills"], x["gross"], x["disc"], x["net"]] for x in days])
    peak = max([x["net"] for x in days] + [1])
    for x in days:
        x["pct"] = round(x["net"] / peak * 100)
    totals = {"bills": sum(x["bills"] for x in days), "gross": sum(x["gross"] for x in days),
              "disc": sum(x["disc"] for x in days), "net": sum(x["net"] for x in days)}
    active = sum(1 for x in days if x["bills"])
    totals["avg_bill"] = totals["net"] / totals["bills"] if totals["bills"] else 0
    totals["avg_day"] = totals["net"] / active if active else 0
    top = db.execute("SELECT si.medicine_name, SUM(si.quantity) units, SUM(si.subtotal) revenue FROM sale_items si "
                     "JOIN sales s ON s.id=si.sale_id WHERE strftime('%Y-%m',s.sale_date)=? GROUP BY si.medicine_name "
                     "ORDER BY revenue DESC LIMIT 10", (key,)).fetchall()
    modes = db.execute("SELECT payment_mode, COUNT(*) c, SUM(total_amount) t FROM sales WHERE strftime('%Y-%m',sale_date)=? "
                       "GROUP BY payment_mode ORDER BY t DESC", (key,)).fetchall()
    prev_m = (first - timedelta(days=1)).replace(day=1)
    next_m = (first + timedelta(days=32)).replace(day=1)
    return render_template("report_monthly.html", first=first, days=days, totals=totals, top=top, modes=modes,
                           prev_m=prev_m.strftime("%Y-%m"),
                           next_m=next_m.strftime("%Y-%m") if next_m <= today() else None, key=key)


@app.route("/reports/stock")
@login_required
def report_stock():
    db = get_db()
    rows = decorate(db.execute("SELECT m.*, s.name supplier_name FROM medicines m "
                               "LEFT JOIN suppliers s ON s.id=m.supplier_id ORDER BY m.name").fetchall())
    if request.args.get("format") == "csv":
        return csv_response("stock_report.csv",
                            ["Medicine", "Batch", "Category", "Price", "Quantity", "Reorder level", "Expiry", "Status", "Supplier"],
                            [[r["name"], r["batch_no"], r["category"], r["price"], r["quantity"], r["reorder_level"],
                              r["expiry_date"], r["status"], r["supplier_name"] or ""] for r in rows])
    summary = {"items": len(rows), "units": sum(r["quantity"] for r in rows),
               "value": sum(r["quantity"] * r["price"] for r in rows),
               "low": sum(1 for r in rows if 0 < r["quantity"] <= r["reorder_level"]),
               "out": sum(1 for r in rows if r["quantity"] <= 0),
               "expired": sum(1 for r in rows if r["status"] == "expired")}
    return render_template("report_stock.html", rows=rows, summary=summary)


# --------------------------------------------------------------------------- #
# Profile & user management (User class: login / updateProfile)
# --------------------------------------------------------------------------- #
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        cur, new, conf = (request.form.get(k, "") for k in ("current_password", "new_password", "confirm_password"))
        errors = []
        if not name:
            errors.append("Name is required.")
        if new:
            row = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
            if not check_password_hash(row["password_hash"], cur):
                errors.append("Current password is incorrect.")
            if len(new) < 8:
                errors.append("New password must be at least 8 characters.")
            if new != conf:
                errors.append("New password and confirmation do not match.")
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            db.execute("UPDATE users SET name=? WHERE id=?", (name, user["id"]))
            if new:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), user["id"]))
            flash("Profile updated.", "success")
            return redirect(url_for("profile"))
    return render_template("profile.html")


@app.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip().lower()
        pw = request.form.get("password", "")
        role = request.form.get("role", "pharmacist")
        errors = []
        if not name or not username:
            errors.append("Name and username are required.")
        if role not in ("admin", "pharmacist"):
            errors.append("Invalid role.")
        if len(pw) < 8:
            errors.append("Password must be at least 8 characters.")
        if not errors:
            try:
                db.execute("INSERT INTO users (name,username,password_hash,role) VALUES (?,?,?,?)",
                           (name, username, generate_password_hash(pw), role))
                flash(f"User '{username}' created.", "success")
                return redirect(url_for("users"))
            except sqlite3.IntegrityError:
                errors.append("That username is already taken.")
        for e in errors:
            flash(e, "danger")
    return render_template("users.html", users=db.execute("SELECT * FROM users ORDER BY id").fetchall())


@app.route("/users/<int:uid>/delete", methods=["POST"])
@admin_required
def user_delete(uid):
    db = get_db()
    if uid == session["user_id"]:
        flash("You cannot delete your own account.", "danger")
    elif db.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone() is None:
        abort(404)
    else:
        db.execute("DELETE FROM users WHERE id=?", (uid,))
        flash("User deleted.", "success")
    return redirect(url_for("users"))


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400, title="Request not accepted", message=getattr(e, "description", "")), 400


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, title="Page not found",
                           message="The page or record you asked for does not exist."), 404


@app.errorhandler(413)
def too_large(e):
    return render_template("error.html", code=413, title="Request too large", message="The submitted data is too large."), 413


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, title="Something went wrong",
                           message="An unexpected error occurred. Please try again."), 500


init_db()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
