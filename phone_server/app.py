from flask import Flask, request, jsonify, send_from_directory, render_template, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import uuid
import base64
import hashlib
import secrets
import shutil
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "robocase-dev-secret-change-me")

CORS(
    app,
    resources={r"/api/*": {"origins": os.environ.get("CORS_ALLOWED_ORIGINS", "*")}},
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
DB_NAME = os.path.join(BASE_DIR, "database.db")
AUTH_REQUIRED_FOR_ORDER_APIS = os.environ.get("AUTH_REQUIRED_FOR_ORDER_APIS", "1") == "1"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

robot_logs = []
robot_status = {
    "state": "IDLE",
    "stage": "WAITING",
    "pen": "NONE",
    "orderId": "-",
    "x": 0,
    "y": 0,
    "z": 0,
    "currentPath": 0,
    "totalPath": 0,
}


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def add_column_if_missing(cursor, table_name, column_name, ddl):
    columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT,
            phone TEXT,
            postal_code TEXT,
            address1 TEXT,
            address2 TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            model TEXT,
            case_type TEXT,
            case_color TEXT,
            total_price INTEGER,
            image_path TEXT,
            status TEXT DEFAULT 'waiting',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            progress INTEGER DEFAULT 0,
            estimated_time INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    add_column_if_missing(cursor, "users", "phone", "phone TEXT")
    add_column_if_missing(cursor, "users", "postal_code", "postal_code TEXT")
    add_column_if_missing(cursor, "users", "address1", "address1 TEXT")
    add_column_if_missing(cursor, "users", "address2", "address2 TEXT")
    add_column_if_missing(cursor, "orders", "user_id", "user_id INTEGER")
    add_column_if_missing(cursor, "orders", "progress", "progress INTEGER DEFAULT 0")
    add_column_if_missing(cursor, "orders", "estimated_time", "estimated_time INTEGER DEFAULT 0")

    admin_email = "admin@example.com"
    existing_admin = cursor.execute("SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone()
    if not existing_admin:
        cursor.execute(
            "INSERT INTO users (email, password_hash, name, role) VALUES (?, ?, ?, ?)",
            (admin_email, generate_password_hash("123456"), "Administrator", "admin"),
        )

    conn.commit()
    conn.close()


init_db()


def get_bearer_token():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_auth_token(user_id):
    token = secrets.token_urlsafe(32)
    token_hash = hash_token(token)
    conn = get_db()
    conn.execute(
        "INSERT INTO auth_tokens (user_id, token_hash) VALUES (?, ?)",
        (user_id, token_hash),
    )
    conn.commit()
    conn.close()
    return token


def revoke_auth_token(token):
    if not token:
        return
    conn = get_db()
    conn.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (hash_token(token),))
    conn.commit()
    conn.close()


def get_current_user():
    conn = get_db()

    bearer_token = get_bearer_token()
    if bearer_token:
        user = conn.execute(
            """
            SELECT u.id, u.email, u.name, u.phone, u.postal_code, u.address1, u.address2, u.role, u.created_at
            FROM auth_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ?
            """,
            (hash_token(bearer_token),),
        ).fetchone()
        conn.close()
        if user:
            return user
        return None

    user_id = session.get("user_id")
    if not user_id:
        conn.close()
        return None

    user = conn.execute(
        "SELECT id, email, name, phone, postal_code, address1, address2, role, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return user


def user_to_dict(user_row):
    return {
        "id": user_row["id"],
        "email": user_row["email"],
        "name": user_row["name"],
        "phone": user_row["phone"],
        "postalCode": user_row["postal_code"],
        "address1": user_row["address1"],
        "address2": user_row["address2"],
        "role": user_row["role"],
        "createdAt": user_row["created_at"],
    }


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        if user["role"] != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return view_func(*args, **kwargs)

    return wrapped


def serialize_order(row):
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "userEmail": row["user_email"],
        "model": row["model"],
        "caseType": row["case_type"],
        "caseColor": row["case_color"],
        "totalPrice": row["total_price"],
        "image_path": row["image_path"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "progress": row["progress"],
        "estimatedTime": row["estimated_time"],
    }


def fetch_order_rows(where_clause="", params=()):
    conn = get_db()
    query = f"""
        SELECT
            o.id,
            o.user_id,
            o.model,
            o.case_type,
            o.case_color,
            o.total_price,
            o.image_path,
            o.status,
            o.created_at,
            o.progress,
            o.estimated_time,
            u.email AS user_email
        FROM orders o
        LEFT JOIN users u ON u.id = o.user_id
        {where_clause}
        ORDER BY o.created_at DESC
    """
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


@app.route("/uploads/<filename>")
def serve_image(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/api/auth/signup", methods=["POST"])
def signup():
    try:
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        name = (data.get("name") or "").strip()
        phone = (data.get("phone") or "").strip()
        postal_code = (data.get("postalCode") or "").strip()
        address1 = (data.get("address1") or "").strip()
        address2 = (data.get("address2") or "").strip()

        if not email or "@" not in email:
            return jsonify({"error": "Valid email is required"}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400
        if not name or not phone or not postal_code or not address1:
            return jsonify({"error": "Shipping contact and address are required"}), 400

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            return jsonify({"error": "Email is already registered"}), 409

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (email, password_hash, name, phone, postal_code, address1, address2, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'user')
            """,
            (email, generate_password_hash(password), name or None, phone or None, postal_code or None, address1 or None, address2 or None),
        )
        user_id = cursor.lastrowid
        conn.commit()

        user = conn.execute(
            "SELECT id, email, name, phone, postal_code, address1, address2, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        conn.close()

        session.clear()
        session["user_id"] = user_id
        token = issue_auth_token(user_id)

        return jsonify({"success": True, "user": user_to_dict(user), "token": token})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/auth/login", methods=["POST"])
def login():
    try:
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""

        conn = get_db()
        user = conn.execute(
            "SELECT id, email, name, phone, postal_code, address1, address2, role, created_at, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Invalid email or password"}), 401

        session.clear()
        session["user_id"] = user["id"]
        token = issue_auth_token(user["id"])

        return jsonify({
            "success": True,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "name": user["name"],
                "phone": user["phone"],
                "postalCode": user["postal_code"],
                "address1": user["address1"],
                "address2": user["address2"],
                "role": user["role"],
                "createdAt": user["created_at"],
            },
            "token": token,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    revoke_auth_token(get_bearer_token())
    session.clear()
    return jsonify({"success": True})


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    user = get_current_user()
    if not user:
        return jsonify({"authenticated": False, "user": None}), 200
    return jsonify({"authenticated": True, "user": user_to_dict(user)})


@app.route("/api/auth/profile", methods=["PUT"])
@login_required
def update_profile():
    try:
        current_user = get_current_user()
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        phone = (data.get("phone") or "").strip()
        postal_code = (data.get("postalCode") or "").strip()
        address1 = (data.get("address1") or "").strip()
        address2 = (data.get("address2") or "").strip()

        if not name or not phone or not postal_code or not address1:
            return jsonify({"error": "Name, phone, postal code, and address are required"}), 400

        conn = get_db()
        conn.execute(
            """
            UPDATE users
            SET name = ?, phone = ?, postal_code = ?, address1 = ?, address2 = ?
            WHERE id = ?
            """,
            (name, phone, postal_code, address1, address2 or None, current_user["id"]),
        )
        conn.commit()
        user = conn.execute(
            "SELECT id, email, name, phone, postal_code, address1, address2, role, created_at FROM users WHERE id = ?",
            (current_user["id"],),
        ).fetchone()
        conn.close()

        return jsonify({"success": True, "user": user_to_dict(user)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/orders", methods=["POST"])
def create_order():
    try:
        current_user = get_current_user()
        if AUTH_REQUIRED_FOR_ORDER_APIS and not current_user:
            return jsonify({"error": "Login required to place orders"}), 401

        data = request.get_json(silent=True) or {}
        model = data.get("model") or "iPhone 15 Plus"
        case_type = data.get("caseType", "clear")
        case_color = data.get("caseColor", "black")
        total_price = data.get("totalPrice", 35000)
        image_base64 = data.get("imageBase64")

        if not image_base64:
            return jsonify({"error": "Image data is required"}), 400

        filename = f"{uuid.uuid4()}.png"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        image_saved = False

        if isinstance(image_base64, str) and image_base64.startswith("data:image"):
            header, encoded = image_base64.split(",", 1)
            image_data = base64.b64decode(encoded)
            with open(filepath, "wb") as file_obj:
                file_obj.write(image_data)
            image_saved = True
        elif isinstance(image_base64, str):
            project_root = os.path.dirname(BASE_DIR)
            source_path = os.path.normpath(os.path.join(project_root, "phone", image_base64))
            allowed_root = os.path.normpath(os.path.join(project_root, "phone", "load_img"))
            if source_path.startswith(allowed_root) and os.path.isfile(source_path):
                shutil.copyfile(source_path, filepath)
                image_saved = True

        if not image_saved:
            return jsonify({"error": "Valid image data or an allowed load_img path is required"}), 400

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO orders (user_id, model, case_type, case_color, total_price, image_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                current_user["id"] if current_user else None,
                model,
                case_type,
                case_color,
                total_price,
                filename,
                "waiting",
            ),
        )
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()

        robot_logs.append({
            "message": f"New order received: ID={order_id}, model={model}",
            "level": "info",
            "time": datetime.now().strftime("%H:%M:%S"),
        })

        return jsonify({"success": True, "order_id": order_id})
    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/orders", methods=["GET"])
def get_orders():
    rows = fetch_order_rows()
    return jsonify([serialize_order(row) for row in rows])


@app.route("/api/my/orders", methods=["GET"])
@login_required
def get_my_orders():
    user = get_current_user()
    rows = fetch_order_rows("WHERE o.user_id = ?", (user["id"],))
    return jsonify([serialize_order(row) for row in rows])


@app.route("/api/orders/<int:order_id>/status", methods=["PATCH"])
def update_status(order_id):
    try:
        data = request.get_json(silent=True) or {}
        status = data.get("status")

        conn = get_db()
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        conn.commit()
        conn.close()

        robot_logs.append({
            "message": f"Order #{order_id} status changed to {status}",
            "level": "info",
            "time": datetime.now().strftime("%H:%M:%S"),
        })

        return jsonify({"message": "Status updated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/orders/<int:order_id>/progress", methods=["PATCH"])
def update_progress(order_id):
    try:
        data = request.get_json(silent=True) or {}
        progress = data.get("progress", 0)
        estimated_time = data.get("estimated_time", 0)

        conn = get_db()
        conn.execute(
            "UPDATE orders SET progress = ?, estimated_time = ? WHERE id = ?",
            (progress, estimated_time, order_id),
        )
        conn.commit()
        conn.close()

        return jsonify({"message": "Progress updated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/robot_logs", methods=["POST"])
def add_robot_log():
    try:
        data = request.get_json(silent=True) or {}
        robot_logs.append({
            "message": data.get("message", ""),
            "level": data.get("level", "info"),
            "time": datetime.now().strftime("%H:%M:%S"),
        })
        if len(robot_logs) > 200:
            robot_logs.pop(0)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/robot_logs", methods=["GET"])
def get_robot_logs():
    return jsonify(robot_logs[-100:])


@app.route("/api/robot_status", methods=["GET"])
def get_robot_status():
    return jsonify(robot_status)


@app.route("/api/robot_status", methods=["PATCH"])
def update_robot_status():
    global robot_status
    data = request.get_json(silent=True) or {}
    robot_status.update(data)
    return jsonify({"success": True})


@app.route("/admin")
def admin():
    return render_template("admin.html")


@app.route("/api/orders/<int:order_id>/reset", methods=["PATCH"])
def reset_order(order_id):
    try:
        conn = get_db()
        conn.execute(
            """
            UPDATE orders
            SET status = 'waiting', progress = 0, estimated_time = 0
            WHERE id = ?
            """,
            (order_id,),
        )
        conn.commit()
        conn.close()
        return jsonify({"message": "Order reset complete"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================================================
# 주문 긴급 중단
# ==================================================
@app.route("/api/orders/<int:order_id>/cancel", methods=["PATCH"])
def cancel_order(order_id):

    try:

        conn = get_db()

        row = conn.execute(
            "SELECT status FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

        if not row:

            conn.close()

            return jsonify({
                "error": "Order not found"
            }), 404

        current_status = row["status"]

        # 이미 완료된 주문이면 중단 불가
        if current_status == "done":

            conn.close()

            return jsonify({
                "error": "Completed order cannot be cancelled"
            }), 400

        # 중단 요청 상태 변경
        conn.execute(
            """
            UPDATE orders
            SET status = 'cancel_requested'
            WHERE id = ?
            """,
            (order_id,)
        )

        conn.commit()
        conn.close()

        robot_logs.append({
            "message": f"⛔ Cancel requested for order #{order_id}",
            "level": "warn",
            "time": datetime.now().strftime("%H:%M:%S"),
        })

        return jsonify({
            "success": True,
            "status": "cancel_requested"
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

@app.route("/api/orders/<int:order_id>", methods=["DELETE"])
def delete_order(order_id):
    try:
        conn = get_db()
        row = conn.execute("SELECT image_path FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row and row["image_path"]:
            filepath = os.path.join(UPLOAD_FOLDER, row["image_path"])
            if os.path.exists(filepath):
                os.remove(filepath)

        conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        conn.commit()
        conn.close()
        return jsonify({"message": "Order deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
