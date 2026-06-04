from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
import mysql.connector
from mysql.connector.errors import IntegrityError
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-key")

UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "pdf", "docx", "txt"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ── Context processor — injects open_count into every template ───────────────

@app.context_processor
def inject_globals():
    """Injects open_count and pending_count into every template."""
    result = {"open_count": 0, "pending_count": 0}
    if "user_id" not in session:
        return result
    try:
        conn   = get_db()
        cursor = conn.cursor(dictionary=True)
        if session.get("role") == "employee":
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM tickets WHERE created_by = %s AND status = 'Open'",
                (session["user_id"],)
            )
            result["open_count"] = cursor.fetchone()["cnt"]
        elif session.get("role") == "admin":
            cursor.execute("SELECT COUNT(*) AS cnt FROM tickets WHERE status = 'Open'")
            result["open_count"] = cursor.fetchone()["cnt"]
            # pending_approval count (0 if column does not exist)
            try:
                cursor.execute("SELECT COUNT(*) AS cnt FROM users WHERE pending_approval = 1")
                result["pending_count"] = cursor.fetchone()["cnt"]
            except Exception:
                result["pending_count"] = 0
        conn.close()
    except Exception:
        pass
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

# SLA hours per priority level
SLA_HOURS = {"Urgent": 2, "High": 8, "Medium": 24, "Low": 72}

def sla_due_for(priority, created_at=None):
    """Return the SLA deadline datetime for a given priority."""
    from datetime import datetime, timedelta
    base = created_at or datetime.now()
    hours = SLA_HOURS.get(priority, 24)
    return base + timedelta(hours=hours)

def sla_status(sla_due_at, status):
    """Return ('ok'|'warning'|'breached', hours_remaining)."""
    from datetime import datetime
    if status == "Closed" or sla_due_at is None:
        return ("closed", None)
    now = datetime.now()
    diff = (sla_due_at - now).total_seconds() / 3600
    if diff < 0:
        return ("breached", diff)
    elif diff <= 2:
        return ("warning", diff)
    else:
        return ("ok", diff)

def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "it_ticket_system"),
        port=int(os.getenv("DB_PORT", 3306))
    )

def ensure_db_columns():
    """Auto-add missing columns and perform automated maintenance tasks."""
    try:
        conn   = get_db()
        cursor = conn.cursor()
        
        # --- Maintenance: Priority Auto-Escalation ---
        # Bump Medium tickets to High if untouched for 12 hours
        cursor.execute("""
            UPDATE tickets 
            SET priority = 'High' 
            WHERE priority = 'Medium' 
            AND status = 'Open' 
            AND created_at < DATE_SUB(NOW(), INTERVAL 12 HOUR)
        """)
        if cursor.rowcount > 0:
            print(f"[INFO] Auto-escalated {cursor.rowcount} tickets from Medium to High.")
            
        # pending_approval
        cursor.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users' AND COLUMN_NAME='pending_approval'""")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN pending_approval TINYINT(1) NOT NULL DEFAULT 0 AFTER is_active"
            )
        # sla_due_at
        cursor.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tickets' AND COLUMN_NAME='sla_due_at'""")
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE tickets ADD COLUMN sla_due_at DATETIME NULL AFTER resolved_at")

        # profile_pic
        cursor.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users' AND COLUMN_NAME='profile_pic'""")
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN profile_pic VARCHAR(255) NULL AFTER specialization")

        # Urgent priority — modify ENUM to include Urgent
        cursor.execute("""SELECT COLUMN_TYPE FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tickets' AND COLUMN_NAME='priority'""")
        col_type = cursor.fetchone()[0]
        if isinstance(col_type, bytes):
            col_type = col_type.decode()
        if "Urgent" not in col_type:
            cursor.execute(
                "ALTER TABLE tickets MODIFY COLUMN priority "
                "ENUM('Low','Medium','High','Urgent') NOT NULL DEFAULT 'Medium'"
            )
        # Backfill sla_due_at for existing open tickets
        cursor.execute(
            "UPDATE tickets SET sla_due_at=DATE_ADD(created_at,INTERVAL 2 HOUR)  WHERE priority='Urgent' AND sla_due_at IS NULL"
        )
        cursor.execute(
            "UPDATE tickets SET sla_due_at=DATE_ADD(created_at,INTERVAL 8 HOUR)  WHERE priority='High'   AND sla_due_at IS NULL"
        )
        cursor.execute(
            "UPDATE tickets SET sla_due_at=DATE_ADD(created_at,INTERVAL 24 HOUR) WHERE priority='Medium' AND sla_due_at IS NULL"
        )
        cursor.execute(
            "UPDATE tickets SET sla_due_at=DATE_ADD(created_at,INTERVAL 72 HOUR) WHERE priority='Low'    AND sla_due_at IS NULL"
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WARN] ensure_db_columns: {e}")

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("You do not have access to that page.", "danger")
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ── Root ──────────────────────────────────────────────────────────────────────

ensure_db_columns()

@app.route("/")
def home():
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    if session.get("role") == "employee":
        return redirect(url_for("my_tickets"))
    return redirect(url_for("login"))


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM users WHERE username = %s", (username,)
        )
        user = cursor.fetchone()
        conn.close()
        
        if user:
            if user["pending_approval"] == 1:
                flash("Your account is pending admin approval.", "warning")
                return render_template("login.html")
            if user["is_active"] == 0:
                flash("Your account has been deactivated.", "danger")
                return render_template("login.html")
            
            if check_password_hash(user["password"], password):
                session["user_id"]  = user["id"]
                session["username"] = user["username"]
                session["fullname"] = user["full_name"] or user["username"]
                session["role"]     = user["role"]
                session["dept"]     = user["department"] or ""
                return redirect(url_for("home"))
        flash("Incorrect username or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username       = request.form["username"].strip()
        fullname       = request.form["fullname"].strip()
        email          = request.form["email"].strip()
        # supports both name="role" and name="account_type" in form
        role           = request.form.get("role") or request.form.get("account_type", "employee")
        if role not in ("employee", "admin"):
            role = "employee"
        password       = request.form["password"]
        department     = request.form.get("department", "").strip()
        specialization = request.form.get("specialization", "").strip()

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")
        if role == "admin" and not specialization:
            flash("Please select your specialization.", "danger")
            return render_template("register.html")

        conn = get_db()
        cursor = conn.cursor()
        try:
            # If registering as admin, set is_active=0 and pending_approval=1
            is_active = 1 if role == "employee" else 0
            pending_approval = 0 if role == "employee" else 1

            cursor.execute(
                "INSERT INTO users (username, full_name, email, department, specialization, password, role, is_active, pending_approval) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (username, fullname, email,
                 department or None,
                 specialization or None,
                 generate_password_hash(password),
                 role, is_active, pending_approval)
            )
            conn.commit()
            if role == "admin":
                flash("Account request submitted. An existing admin must approve your account before you can log in.", "info")
            else:
                flash("Account created. You can now log in.", "success")
            return redirect(url_for("login"))
        except IntegrityError:
            flash("Username or email already exists.", "danger")
        finally:
            conn.close()
    return render_template("register.html")


# ── Employee ──────────────────────────────────────────────────────────────────

@app.route("/tickets")
@login_required(role="employee")
def my_tickets():
    status_f   = request.args.get("status", "")
    category_f = request.args.get("category", "")
    search_q   = request.args.get("q", "").strip()
    page       = max(1, int(request.args.get("page", 1)))
    per_page   = 15

    sql    = "SELECT * FROM tickets WHERE created_by = %s"
    params = [session["user_id"]]
    if status_f:
        sql += " AND status = %s"
        params.append(status_f)
    if category_f:
        sql += " AND category = %s"
        params.append(category_f)
    if search_q:
        sql += " AND title LIKE %s"
        params.append(f"%{search_q}%")

    conn   = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(sql.replace("SELECT *", "SELECT COUNT(*) AS cnt"), params)
    total       = cursor.fetchone()["cnt"]
    total_pages = max(1, -(-total // per_page))
    page        = min(page, total_pages)

    cursor.execute(
        sql + " ORDER BY created_at DESC LIMIT %s OFFSET %s",
        params + [per_page, (page - 1) * per_page]
    )
    tickets = cursor.fetchall()

    # Open ticket count for navbar badge
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM tickets WHERE created_by = %s AND status = 'Open'",
        (session["user_id"],)
    )
    open_count = cursor.fetchone()["cnt"]
    conn.close()

    return render_template("my_tickets.html", tickets=tickets,
                           status_f=status_f, category_f=category_f,
                           search_q=search_q,
                           open_count=open_count,
                           page=page, total_pages=total_pages, total=total)


@app.route("/tickets/new", methods=["GET", "POST"])
@login_required(role="employee")
def new_ticket():
    # Load specialists for the assign-to dropdown
    conn   = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, username, full_name, specialization FROM users "
        "WHERE role = 'admin' AND is_active = 1 ORDER BY specialization, full_name"
    )
    specialists = cursor.fetchall()
    conn.close()

    if request.method == "POST":
        title       = request.form["title"].strip()
        description = request.form["description"].strip()
        category    = request.form["category"]
        priority    = request.form["priority"]
        # supports both field names from different template versions
        assigned_to = request.form.get("preferred_assignee") or request.form.get("assigned_to") or None

        file     = request.files.get("attachment")
        filename = None
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        conn   = get_db()
        cursor = conn.cursor()
        # --- AUTOMATION: Smart Routing ---
        # If no preferred assignee was chosen, find a specialist for this category
        if not assigned_to:
            cursor.execute(
                "SELECT id FROM users WHERE role = 'admin' AND specialization = %s AND is_active = 1 LIMIT 1",
                (category,)
            )
            match = cursor.fetchone()
            if match:
                assigned_to = match[0]
                auto_note = f"Automated: Ticket routed to {category} specialist."
            else:
                auto_note = "Ticket created (unassigned)."
        else:
            auto_note = "Ticket created (manual assignment)."

        sla_due = sla_due_for(priority)
        cursor.execute(
            "INSERT INTO tickets (title, description, category, priority, status, created_by, assigned_to, sla_due_at) "
            "VALUES (%s, %s, %s, %s, 'Open', %s, %s, %s)",
            (title, description, category, priority, session["user_id"], assigned_to, sla_due)
        )
        ticket_id = cursor.lastrowid

        if filename:
            cursor.execute(
                "INSERT INTO comments (ticket_id, user_id, comment) VALUES (%s, %s, %s)",
                (ticket_id, session["user_id"], f"Attached file: {filename}")
            )

        cursor.execute(
            "INSERT INTO ticket_history (ticket_id, changed_by, old_status, new_status, note) "
            "VALUES (%s, %s, NULL, 'Open', %s)",
            (ticket_id, session["user_id"], auto_note)
        )
        conn.commit()
        conn.close()
        flash("Ticket submitted successfully.", "success")
        return redirect(url_for("my_tickets"))

    return render_template("new_ticket.html", specialists=specialists, staff_list=specialists)


@app.route("/tickets/<int:ticket_id>", methods=["GET", "POST"])
@login_required()
def view_ticket(ticket_id):
    conn   = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT t.*, u.username AS requester, u2.username AS assignee_name "
        "FROM tickets t "
        "JOIN users u ON t.created_by = u.id "
        "LEFT JOIN users u2 ON t.assigned_to = u2.id "
        "WHERE t.id = %s",
        (ticket_id,)
    )
    ticket = cursor.fetchone()

    if not ticket:
        flash("Ticket not found.", "danger")
        conn.close()
        return redirect(url_for("my_tickets"))

    if session["role"] == "employee" and ticket["created_by"] != session["user_id"]:
        flash("You can only view your own tickets.", "danger")
        conn.close()
        return redirect(url_for("my_tickets"))

    if request.method == "POST" and ticket["status"] != "Closed":
        comment = request.form.get("comment", "").strip()
        # Handle combined comment + file
        file = request.files.get("attachment")
        if file and file.filename and allowed_file(file.filename):
            filename = f"attach_{ticket_id}_{secure_filename(file.filename)}"
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            if comment:
                comment += f" (Attached file: {filename})"
            else:
                comment = f"Attached file: {filename}"

        if comment:
            cursor2 = conn.cursor()
            cursor2.execute(
                "INSERT INTO comments (ticket_id, user_id, comment) VALUES (%s, %s, %s)",
                (ticket_id, session["user_id"], comment)
            )
            conn.commit()
            flash("Message posted.", "success")
        else:
            flash("Please enter a comment or attach a file.", "warning")

    cursor.execute(
        "SELECT c.comment, u.username, c.created_at "
        "FROM comments c JOIN users u ON c.user_id = u.id "
        "WHERE c.ticket_id = %s ORDER BY c.created_at ASC",
        (ticket_id,)
    )
    comments = cursor.fetchall()

    cursor.execute(
        "SELECT h.*, u.username AS by_name "
        "FROM ticket_history h JOIN users u ON h.changed_by = u.id "
        "WHERE h.ticket_id = %s ORDER BY h.changed_at ASC",
        (ticket_id,)
    )
    history = cursor.fetchall()

    staff_list = []
    if session["role"] == "admin":
        cursor.execute(
            "SELECT id, username, full_name, specialization FROM users "
            "WHERE role = 'admin' AND is_active = 1 ORDER BY specialization, full_name"
        )
        staff_list = cursor.fetchall()

    # Open count for navbar badge (employee only)
    open_count = 0
    if session["role"] == "employee":
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM tickets WHERE created_by = %s AND status = 'Open'",
            (session["user_id"],)
        )
        open_count = cursor.fetchone()["cnt"]

    conn.close()
    return render_template("view_ticket.html", ticket=ticket,
                           comments=comments, history=history,
                           staff_list=staff_list, open_count=open_count)


@app.route("/tickets/<int:ticket_id>/edit", methods=["GET", "POST"])
@login_required(role="employee")
def edit_ticket(ticket_id):
    conn   = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM tickets WHERE id = %s AND created_by = %s",
        (ticket_id, session["user_id"])
    )
    ticket = cursor.fetchone()
    conn.close()

    if not ticket:
        flash("Ticket not found.", "danger")
        return redirect(url_for("my_tickets"))
    if ticket["status"] != "Open":
        flash("Only Open tickets can be edited.", "warning")
        return redirect(url_for("view_ticket", ticket_id=ticket_id))

    if request.method == "POST":
        title       = request.form["title"].strip()
        description = request.form["description"].strip()
        category    = request.form["category"]
        priority    = request.form["priority"]
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tickets SET title=%s, description=%s, category=%s, priority=%s WHERE id=%s",
            (title, description, category, priority, ticket_id)
        )
        conn.commit()
        conn.close()
        flash("Ticket updated.", "success")
        return redirect(url_for("view_ticket", ticket_id=ticket_id))

    return render_template("edit_ticket.html", ticket=ticket)


# ── File download ─────────────────────────────────────────────────────────────

@app.route("/uploads/<filename>")
def download_file(filename):
    if "user_id" not in session:
        return redirect(url_for("login"))
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.route("/admin")
@login_required(role="admin")
def admin_dashboard():
    conn   = get_db()
    cursor = conn.cursor(dictionary=True)

    # Stats
    cursor.execute("""
        SELECT
            COALESCE(SUM(status != 'Closed'), 0) AS unresolved,
            COALESCE(SUM(sla_due_at < NOW() AND status != 'Closed'), 0) AS overdue,
            COALESCE(SUM(DATE(sla_due_at) = CURDATE() AND status != 'Closed'), 0) AS due_today,
            COALESCE(SUM(status = 'Open'), 0) AS open,
            0 AS on_hold,
            COALESCE(SUM(assigned_to IS NULL AND status != 'Closed'), 0) AS unassigned,
            COALESCE(SUM(status = 'In Progress'), 0) AS in_progress,
            COALESCE(SUM(status = 'Closed'), 0) AS closed,
            COUNT(*) AS total,
            COALESCE(SUM(sla_due_at < NOW() AND status != 'Closed'), 0) AS sla_breached,
            COALESCE(SUM(sla_due_at BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 2 HOUR) AND status != 'Closed'), 0) AS sla_warning
        FROM tickets
    """)
    stats = cursor.fetchone()

    # Category breakdown
    cursor.execute(
        "SELECT category, COUNT(*) AS cnt FROM tickets GROUP BY category ORDER BY cnt DESC"
    )
    by_category = cursor.fetchall()

    # Priority breakdown (Active tickets only)
    cursor.execute("""
        SELECT priority, COUNT(*) AS cnt 
        FROM tickets 
        WHERE status != 'Closed' 
        GROUP BY priority 
        ORDER BY FIELD(priority, 'Urgent', 'High', 'Medium', 'Low')
    """)
    by_priority = cursor.fetchall()

    # 5 most recent tickets for quick view
    cursor.execute(
        "SELECT t.*, u.username AS requester "
        "FROM tickets t JOIN users u ON t.created_by = u.id "
        "ORDER BY t.created_at DESC LIMIT 5"
    )
    recent_tickets = cursor.fetchall()

    # IT Staff Workload
    cursor.execute("""
        SELECT u.username, COUNT(t.id) as active_count
        FROM users u
        LEFT JOIN tickets t ON u.id = t.assigned_to AND t.status != 'Closed'
        WHERE u.role = 'admin' AND u.is_active = 1
        GROUP BY u.id
        ORDER BY active_count DESC
    """)
    workload = cursor.fetchall()

    # Monthly Trend (Last 6 Months)
    cursor.execute("""
        SELECT DATE_FORMAT(created_at, '%b') as month_name, 
               COUNT(*) as count
        FROM tickets 
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
        GROUP BY YEAR(created_at), MONTH(created_at), month_name
        ORDER BY YEAR(created_at) ASC, MONTH(created_at) ASC
    """)
    monthly_trend = cursor.fetchall()

    conn.close()

    return render_template("admin_dashboard.html",
                           stats=stats,
                           by_category=by_category,
                           by_priority=by_priority,
                           recent_tickets=recent_tickets,
                           workload=workload,
                           monthly_trend=monthly_trend)


@app.route("/admin/tickets/<int:ticket_id>/status", methods=["POST"])
@login_required(role="admin")
def update_status(ticket_id):
    new_status = request.form.get("status")
    if new_status not in ("Open", "In Progress", "Closed"):
        flash("Invalid status.", "danger")
        return redirect(url_for("admin_dashboard"))

    conn   = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT status FROM tickets WHERE id = %s", (ticket_id,))
    row = cursor.fetchone()

    if row:
        old_status = row["status"]
        cursor2    = conn.cursor()
        if new_status == "Closed":
            cursor2.execute(
                "UPDATE tickets SET status = %s, resolved_at = NOW() WHERE id = %s",
                (new_status, ticket_id)
            )
        else:
            cursor2.execute(
                "UPDATE tickets SET status = %s WHERE id = %s",
                (new_status, ticket_id)
            )
        cursor2.execute(
            "INSERT INTO ticket_history (ticket_id, changed_by, old_status, new_status, note) "
            "VALUES (%s, %s, %s, %s, %s)",
            (ticket_id, session["user_id"], old_status, new_status, f"Status auto-updated to {new_status}")
        )
        
        # --- AUTOMATION: Resolution Notification ---
        if new_status == "Closed":
            cursor2.execute(
                "INSERT INTO comments (ticket_id, user_id, comment) "
                "VALUES (%s, %s, 'Automated: This ticket has been resolved and closed.')",
                (ticket_id, session["user_id"])
            )
            
        # --- AUTOMATION: Escalation Audit ---
        if old_status == "Open" and new_status == "Open":
            # This handles cases where only priority might have changed via automation
            pass
            
        conn.commit()
        flash(f"Status updated to {new_status}.", "success")

    conn.close()
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/admin/tickets/<int:ticket_id>/assign", methods=["POST"])
@login_required(role="admin")
def assign_ticket(ticket_id):
    assignee_id = request.form.get("assignee_id") or None
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE tickets SET assigned_to = %s WHERE id = %s", (assignee_id, ticket_id)
    )
    conn.commit()
    conn.close()
    flash("Ticket assigned.", "success")
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/admin/tickets/<int:ticket_id>/delete", methods=["POST"])
@login_required(role="admin")
def delete_ticket(ticket_id):
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ticket_history WHERE ticket_id = %s", (ticket_id,))
    cursor.execute("DELETE FROM comments       WHERE ticket_id = %s", (ticket_id,))
    cursor.execute("DELETE FROM tickets        WHERE id = %s",        (ticket_id,))
    conn.commit()
    conn.close()
    flash("Ticket deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users")
@login_required(role="admin")
def manage_users():
    search_q = request.args.get("q", "").strip()
    conn   = get_db()
    cursor = conn.cursor(dictionary=True)
    
    if search_q:
        sql = """SELECT * FROM users 
                 WHERE username LIKE %s 
                 OR full_name LIKE %s 
                 OR email LIKE %s 
                 OR department LIKE %s
                 ORDER BY created_at DESC"""
        params = [f"%{search_q}%"] * 4
        cursor.execute(sql, params)
    else:
        cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
        
    users = cursor.fetchall()
    conn.close()
    return render_template("manage_users.html", users=users, search_q=search_q)


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@login_required(role="admin")
def toggle_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot deactivate yourself.", "warning")
        return redirect(url_for("manage_users"))
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_active = NOT is_active WHERE id = %s", (user_id,))
    conn.commit()
    conn.close()
    flash("User status updated.", "success")
    return redirect(url_for("manage_users"))


@app.route("/admin/users/<int:user_id>/make-admin", methods=["POST"])
@login_required(role="admin")
def make_admin(user_id):
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role = 'admin' WHERE id = %s", (user_id,))
    conn.commit()
    conn.close()
    flash("User promoted to Admin.", "success")
    return redirect(url_for("manage_users"))


# ── Set Specialization (admin only) ──────────────────────────────────────────

@app.route("/admin/users/<int:user_id>/set-specialization", methods=["POST"])
@login_required(role="admin")
def set_specialization(user_id):
    specialization = request.form.get("specialization") or None
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET specialization = %s WHERE id = %s", (specialization, user_id)
    )
    conn.commit()
    conn.close()
    flash("Specialization updated.", "success")
    return redirect(url_for("manage_users"))


# ── Profile ──────────────────────────────────────────────────────────────────

@app.route("/profile")
@login_required()
def profile():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    user = cursor.fetchone()
    conn.close()
    return render_template("profile.html", user=user)


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required()
def edit_profile():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    user = cursor.fetchone()

    if request.method == "POST":
        fullname = request.form["fullname"].strip()
        email = request.form["email"].strip()
        department = request.form["department"].strip()
        specialization = request.form.get("specialization", "").strip()
        
        cursor2 = conn.cursor()
        if session.get("role") == "admin":
            cursor2.execute(
                "UPDATE users SET full_name=%s, email=%s, department=%s, specialization=%s WHERE id=%s",
                (fullname, email, department, specialization or None, session["user_id"])
            )
        else:
            cursor2.execute(
                "UPDATE users SET full_name=%s, email=%s, department=%s WHERE id=%s",
                (fullname, email, department, session["user_id"])
            )
        conn.commit()
        session["fullname"] = fullname
        conn.close()
        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))

    conn.close()
    return render_template("edit_profile.html", user=user)


# ── Change Password ──────────────────────────────────────────────────────────

@app.route("/change-password", methods=["GET", "POST"])
@login_required()
def change_password():
    if request.method == "POST":
        current  = request.form["current_password"]
        new_pw   = request.form["new_password"]
        confirm  = request.form["confirm_password"]

        if new_pw != confirm:
            flash("New passwords do not match.", "danger")
            return render_template("change_password.html")
        if len(new_pw) < 6:
            flash("New password must be at least 6 characters.", "danger")
            return render_template("change_password.html")

        conn   = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT password FROM users WHERE id = %s", (session["user_id"],))
        user = cursor.fetchone()

        if not check_password_hash(user["password"], current):
            conn.close()
            flash("Current password is incorrect.", "danger")
            return render_template("change_password.html")

        cursor2 = conn.cursor()
        cursor2.execute(
            "UPDATE users SET password = %s WHERE id = %s",
            (generate_password_hash(new_pw), session["user_id"])
        )
        conn.commit()
        conn.close()
        flash("Password changed successfully.", "success")
        return redirect(url_for("home"))

    return render_template("change_password.html")


# ── Admin: All Tickets (separate page) ───────────────────────────────────────

@app.route("/admin/all-tickets")
@login_required(role="admin")
def admin_tickets():
    status_f   = request.args.get("status", "")
    category_f = request.args.get("category", "")
    priority_f = request.args.get("priority", "")
    search_q   = request.args.get("q", "").strip()
    sla_f      = request.args.get("sla", "")
    page       = max(1, int(request.args.get("page", 1)))
    per_page   = 20

    sql = (
        "SELECT t.*, u.username AS requester, u.department, u2.username AS assignee_name "
        "FROM tickets t "
        "JOIN users u ON t.created_by = u.id "
        "LEFT JOIN users u2 ON t.assigned_to = u2.id "
        "WHERE 1=1"
    )
    params = []
    if status_f:   sql += " AND t.status = %s";   params.append(status_f)
    if category_f: sql += " AND t.category = %s"; params.append(category_f)
    if priority_f: sql += " AND t.priority = %s"; params.append(priority_f)
    if search_q:
        sql += " AND (t.title LIKE %s OR u.username LIKE %s)"
        params += [f"%{search_q}%", f"%{search_q}%"]
    if sla_f == "breached":
        sql += " AND t.sla_due_at < NOW() AND t.status != 'Closed'"
    elif sla_f == "warning":
        sql += " AND t.sla_due_at BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL 2 HOUR) AND t.status != 'Closed'"
    elif sla_f == "ok":
        sql += " AND t.sla_due_at > DATE_ADD(NOW(), INTERVAL 2 HOUR) AND t.status != 'Closed'"

    conn   = get_db()
    cursor = conn.cursor(dictionary=True)

    count_sql = sql.replace(
        "SELECT t.*, u.username AS requester, u.department, u2.username AS assignee_name",
        "SELECT COUNT(*) AS cnt"
    )
    cursor.execute(count_sql, params)
    total       = cursor.fetchone()["cnt"]
    total_pages = max(1, -(-total // per_page))
    page        = min(page, total_pages)

    cursor.execute(
        sql + " ORDER BY FIELD(t.priority,'Urgent','High','Medium','Low'), t.created_at DESC LIMIT %s OFFSET %s",
        params + [per_page, (page - 1) * per_page]
    )
    tickets = cursor.fetchall()

    cursor.execute(
        "SELECT id, username, full_name, specialization FROM users "
        "WHERE role = 'admin' AND is_active = 1 ORDER BY specialization, full_name"
    )
    staff_list = cursor.fetchall()
    conn.close()

    return render_template("admin_tickets.html",
                           tickets=tickets, staff_list=staff_list,
                           status_f=status_f, category_f=category_f,
                           priority_f=priority_f, search_q=search_q,
                           sla_f=sla_f,
                           page=page, total_pages=total_pages, total=total)


# ── Admin: Contacts ───────────────────────────────────────────────────────────

@app.route("/admin/contacts")
@login_required(role="admin")
def contacts():
    search_q = request.args.get("q", "").strip()
    role_f   = request.args.get("role", "")
    dept_f   = request.args.get("dept", "")
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 20

    where  = "WHERE 1=1"
    params = []
    if search_q:
        where  += " AND (u.full_name LIKE %s OR u.username LIKE %s OR u.email LIKE %s)"
        params += [f"%{search_q}%", f"%{search_q}%", f"%{search_q}%"]
    if role_f:
        where += " AND u.role = %s"; params.append(role_f)
    if dept_f:
        where += " AND u.department = %s"; params.append(dept_f)

    conn   = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(f"SELECT COUNT(*) AS cnt FROM users u {where}", params)
    total       = cursor.fetchone()["cnt"]
    total_pages = max(1, -(-total // per_page))
    page        = min(page, total_pages)

    cursor.execute(
        f"SELECT u.*, (SELECT COUNT(*) FROM tickets t WHERE t.created_by = u.id) AS ticket_count "
        f"FROM users u {where} ORDER BY u.created_at DESC LIMIT %s OFFSET %s",
        params + [per_page, (page - 1) * per_page]
    )
    contact_list = cursor.fetchall()

    cursor.execute(
        "SELECT DISTINCT department FROM users "
        "WHERE department IS NOT NULL AND department != '' ORDER BY department"
    )
    departments = [r["department"] for r in cursor.fetchall()]
    conn.close()

    return render_template("contacts.html",
                           contact_list=contact_list, departments=departments,
                           search_q=search_q, role_f=role_f, dept_f=dept_f,
                           page=page, total_pages=total_pages, total=total)


# ── Admin: Approve / Reject pending users ─────────────────────────────────────

@app.route("/admin/users/<int:user_id>/approve", methods=["POST"])
@login_required(role="admin")
def approve_user(user_id):
    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET pending_approval = 0, is_active = 1 WHERE id = %s", (user_id,)
        )
        conn.commit()
        flash("User approved.", "success")
    except Exception:
        flash("Could not approve user.", "danger")
    finally:
        conn.close()
    return redirect(url_for("manage_users"))


@app.route("/admin/users/<int:user_id>/reject", methods=["POST"])
@login_required(role="admin")
def reject_user(user_id):
    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        flash("User request rejected and removed.", "success")
    except Exception:
        flash("Could not reject user.", "danger")
    finally:
        conn.close()
    return redirect(url_for("manage_users"))


if __name__ == "__main__":
    app.run(debug=True)
