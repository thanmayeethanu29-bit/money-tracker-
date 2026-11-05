import csv, io, os
from datetime import date
from functools import wraps
import mysql.connector
from dotenv import load_dotenv
from flask import Flask, Response, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-key")
MODES = ["Cash", "Card", "UPI", "Net Banking"]
DEFAULT_CATS = [("Food", "expense"), ("Travel", "expense"), ("Bills", "expense"), ("Shopping", "expense"),
                ("Fun", "expense"), ("Health", "expense"), ("Salary", "income"), ("Pocket Money", "income")]


def query(sql, args=(), one=False, commit=False):
    conn = mysql.connector.connect(host=os.getenv("DB_HOST", "localhost"), user=os.getenv("DB_USER", "root"),
                                   password=os.getenv("DB_PASSWORD", ""), database=os.getenv("DB_NAME", "money_tracker"))
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, args)  # always parameterized -> prevents SQL injection
    if commit:
        conn.commit(); out = cur.lastrowid
    else:
        out = cur.fetchone() if one else cur.fetchall()
    cur.close(); conn.close()
    return out


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "uid" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))
        return f(*a, **k)
    return w


def own_category(cid):
    return query("SELECT category_id FROM categories WHERE category_id=%s AND user_id=%s", (cid, session["uid"]), one=True)


def parse_txn(form):
    amount = round(float(form["amount"]), 2)
    if amount <= 0 or not own_category(form["category_id"]):
        raise ValueError
    date.fromisoformat(form["txn_date"])
    mode = form["payment_mode"] if form["payment_mode"] in MODES else "Cash"
    return (form["category_id"], amount, form["txn_date"], form["description"].strip()[:150], mode)


@app.route("/")
def home():
    return redirect(url_for("dashboard" if "uid" in session else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        n, e, p = request.form["name"].strip(), request.form["email"].strip().lower(), request.form["password"]
        if not n or "@" not in e or len(p) < 6:
            flash("Enter your name, a valid email and a password of 6+ characters.")
        elif query("SELECT user_id FROM users WHERE email=%s", (e,), one=True):
            flash("That email is already registered.")
        else:
            uid = query("INSERT INTO users (name,email,password_hash) VALUES (%s,%s,%s)",
                        (n, e, generate_password_hash(p)), commit=True)
            for name, typ in DEFAULT_CATS:
                query("INSERT INTO categories (user_id,name,type) VALUES (%s,%s,%s)", (uid, name, typ), commit=True)
            flash("Account created. Log in to start tracking.")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = query("SELECT * FROM users WHERE email=%s", (request.form["email"].strip().lower(),), one=True)
        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session["uid"], session["name"] = u["user_id"], u["name"]
            return redirect(url_for("dashboard"))
        flash("Wrong email or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["uid"]  # every query is scoped to the logged-in user
    ym = request.args.get("month") or date.today().strftime("%Y-%m")
    y, m = int(ym[:4]), int(ym[5:7])
    q = request.args.get("q", "").strip()
    period = "t.user_id=%s AND YEAR(t.txn_date)=%s AND MONTH(t.txn_date)=%s"
    totals = {r["type"]: float(r["s"]) for r in query(
        f"SELECT c.type, SUM(t.amount) s FROM transactions t JOIN categories c ON c.category_id=t.category_id "
        f"WHERE {period} GROUP BY c.type", (uid, y, m))}
    income, expense = totals.get("income", 0), totals.get("expense", 0)
    by_cat = query(f"SELECT c.name, SUM(t.amount) s FROM transactions t JOIN categories c ON c.category_id=t.category_id "
                   f"WHERE {period} AND c.type='expense' GROUP BY c.name ORDER BY s DESC", (uid, y, m))
    budgets = query("SELECT b.budget_id, c.name, b.limit_amount, COALESCE(SUM(t.amount),0) spent FROM budgets b "
                    "JOIN categories c ON c.category_id=b.category_id LEFT JOIN transactions t "
                    "ON t.category_id=b.category_id AND t.user_id=b.user_id AND YEAR(t.txn_date)=b.year AND MONTH(t.txn_date)=b.month "
                    "WHERE b.user_id=%s AND b.month=%s AND b.year=%s GROUP BY b.budget_id, c.name, b.limit_amount", (uid, m, y))
    for b in budgets:
        b["pct"] = round(float(b["spent"]) / float(b["limit_amount"]) * 100)
    txns = query(f"SELECT t.*, c.name cat, c.type FROM transactions t JOIN categories c ON c.category_id=t.category_id "
                 f"WHERE {period} AND (t.description LIKE %s OR c.name LIKE %s) ORDER BY t.txn_date DESC, t.txn_id DESC",
                 (uid, y, m, f"%{q}%", f"%{q}%"))
    cats = query("SELECT * FROM categories WHERE user_id=%s ORDER BY type, name", (uid,))
    pct = min(100, round(expense / income * 100)) if income else (100 if expense else 0)
    return render_template("dashboard.html", ym=ym, q=q, income=income, expense=expense, balance=income - expense,
                           pct=pct, by_cat=by_cat, budgets=budgets, txns=txns, cats=cats, modes=MODES,
                           today=date.today().isoformat())


@app.route("/add", methods=["POST"])
@login_required
def add():
    try:
        query("INSERT INTO transactions (user_id,category_id,amount,txn_date,description,payment_mode) "
              "VALUES (%s,%s,%s,%s,%s,%s)", (session["uid"], *parse_txn(request.form)), commit=True)
    except (ValueError, KeyError):
        flash("Check the form: amount must be above 0 and the date must be valid.")
    return redirect(url_for("dashboard", month=request.form.get("txn_date", "")[:7]))


@app.route("/edit/<int:tid>", methods=["GET", "POST"])
@login_required
def edit(tid):
    t = query("SELECT * FROM transactions WHERE txn_id=%s AND user_id=%s", (tid, session["uid"]), one=True)
    if not t:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        try:
            query("UPDATE transactions SET category_id=%s, amount=%s, txn_date=%s, description=%s, payment_mode=%s "
                  "WHERE txn_id=%s AND user_id=%s", (*parse_txn(request.form), tid, session["uid"]), commit=True)
            return redirect(url_for("dashboard", month=request.form["txn_date"][:7]))
        except (ValueError, KeyError):
            flash("Check the form: amount must be above 0 and the date must be valid.")
    cats = query("SELECT * FROM categories WHERE user_id=%s ORDER BY type, name", (session["uid"],))
    return render_template("edit.html", t=t, cats=cats, modes=MODES)


@app.route("/delete/<int:tid>", methods=["POST"])
@login_required
def delete(tid):
    query("DELETE FROM transactions WHERE txn_id=%s AND user_id=%s", (tid, session["uid"]), commit=True)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/category", methods=["POST"])
@login_required
def category():
    name, typ = request.form["name"].strip()[:40], request.form["type"]
    if name and typ in ("income", "expense") and not query(
            "SELECT 1 x FROM categories WHERE user_id=%s AND name=%s AND type=%s", (session["uid"], name, typ), one=True):
        query("INSERT INTO categories (user_id,name,type) VALUES (%s,%s,%s)", (session["uid"], name, typ), commit=True)
    else:
        flash("Enter a new category name.")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/budget", methods=["POST"])
@login_required
def budget():
    try:
        lim, ym = float(request.form["limit"]), request.form["month"]
        assert lim > 0 and own_category(request.form["category_id"])
        query("INSERT INTO budgets (user_id,category_id,month,year,limit_amount) VALUES (%s,%s,%s,%s,%s) "
              "ON DUPLICATE KEY UPDATE limit_amount=VALUES(limit_amount)",
              (session["uid"], request.form["category_id"], int(ym[5:7]), int(ym[:4]), lim), commit=True)
    except (ValueError, AssertionError, KeyError):
        flash("Pick a category and enter a limit above 0.")
    return redirect(url_for("dashboard", month=request.form.get("month")))


@app.route("/export")
@login_required
def export():
    rows = query("SELECT t.txn_date, c.type, c.name, t.amount, t.payment_mode, t.description FROM transactions t "
                 "JOIN categories c ON c.category_id=t.category_id WHERE t.user_id=%s ORDER BY t.txn_date", (session["uid"],))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Type", "Category", "Amount", "Payment mode", "Description"])
    for r in rows:
        w.writerow([r["txn_date"], r["type"], r["name"], r["amount"], r["payment_mode"], r["description"]])
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=transactions.csv"})


if __name__ == "__main__":
    app.run(debug=True)
