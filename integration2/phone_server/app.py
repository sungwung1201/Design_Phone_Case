# ==================================================
# app.py — Flask 웹 서버 (백엔드 API)
#
# 역할:
#   - 클라이언트(index.html / app.js)로부터 주문을 받아 DB에 저장
#   - 주문 이미지(PNG)와 stroke JSON 파일을 uploads/ 폴더에 저장
#   - robot_drawer.py가 DB를 폴링하여 주문을 가져가고 처리 결과를 업데이트
#   - admin.html에 주문 목록, 로봇 상태, 로봇 로그를 제공
# ==================================================

from flask import Flask, request, jsonify, send_from_directory, render_template, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import uuid
import base64
import json
import hashlib
import secrets
import shutil
from datetime import datetime
from functools import wraps

# Flask 앱 생성
app = Flask(__name__)

# 세션 암호화 키: 환경변수로 설정하지 않으면 개발용 기본값 사용
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "robocase-dev-secret-change-me")

# CORS 설정: /api/* 경로에 대해 허용할 Origin을 환경변수로 제어 (기본: 전체 허용)
CORS(
    app,
    resources={r"/api/*": {"origins": os.environ.get("CORS_ALLOWED_ORIGINS", "*")}},
)

# 파일/DB 경로 설정 (app.py가 있는 폴더 기준)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")   # 이미지·JSON 저장 폴더
DB_NAME = os.path.join(BASE_DIR, "database.db")     # SQLite DB 파일

# 환경변수로 주문 API 인증 여부 제어 (기본: 인증 필요)
AUTH_REQUIRED_FOR_ORDER_APIS = os.environ.get("AUTH_REQUIRED_FOR_ORDER_APIS", "1") == "1"

# uploads 폴더 없으면 자동 생성
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ==================================================
# 인메모리 로봇 상태·로그 저장소
# (재시작 시 초기화됨 — 영속 저장이 필요하면 DB로 이전할 것)
# ==================================================
robot_logs = []  # admin 페이지에 표시할 로봇 로그 목록 (최대 200개)

robot_status = {
    "state": "IDLE",       # 로봇 동작 상태 (IDLE / DRAWING / STOPPED 등)
    "stage": "WAITING",    # 현재 작업 단계 (WAITING / DRAW / IMPACT_STOP 등)
    "pen": "NONE",         # 현재 잡고 있는 펜 색상
    "orderId": "-",        # 현재 처리 중인 주문 ID
    "x": 0,                # 로봇 TCP 현재 X 좌표 (mm)
    "y": 0,                # 로봇 TCP 현재 Y 좌표 (mm)
    "z": 0,                # 로봇 TCP 현재 Z 좌표 (mm)
    "currentPath": 0,      # 현재까지 그린 path 수
    "totalPath": 0,        # 전체 path 수
}


# ==================================================
# DB 유틸리티
# ==================================================

def get_db():
    """SQLite DB 연결을 열고 Row 객체로 반환 (컬럼명으로 접근 가능)."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def add_column_if_missing(cursor, table_name, column_name, ddl):
    """
    테이블에 컬럼이 없으면 ALTER TABLE로 추가한다.
    앱 업데이트 시 기존 DB 스키마와의 호환성 유지를 위해 사용.
    """
    columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def init_db():
    """
    앱 시작 시 DB 테이블을 초기화한다.
    - users: 회원 정보
    - orders: 주문 정보 (이미지 경로, 진행률 포함)
    - auth_tokens: Bearer 토큰 저장 (해시값만 저장)
    기존 DB가 있으면 누락된 컬럼만 추가하고, 관리자 계정이 없으면 생성한다.
    """
    conn = get_db()
    cursor = conn.cursor()

    # 회원 테이블
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
            role TEXT NOT NULL DEFAULT 'user',  -- 'user' 또는 'admin'
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # 주문 테이블
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            model TEXT,             -- 기종 (예: iPhone 15 Plus)
            case_type TEXT,         -- 케이스 타입 (clear / opaque / translucent)
            case_color TEXT,        -- 범퍼 색상 (black / white / pink)
            total_price INTEGER,    -- 결제 금액 (원)
            image_path TEXT,        -- uploads/ 내 PNG 파일명
            status TEXT DEFAULT 'waiting',  -- waiting / processing / done / cancelled 등
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            progress INTEGER DEFAULT 0,          -- 드로잉 진행률 (0~100%)
            estimated_time INTEGER DEFAULT 0,    -- 남은 예상 시간 (초)
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    # 인증 토큰 테이블 (원본 토큰은 저장하지 않고 SHA-256 해시만 저장)
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

    # 기존 DB에 누락된 컬럼 추가 (마이그레이션 대용)
    add_column_if_missing(cursor, "users", "phone", "phone TEXT")
    add_column_if_missing(cursor, "users", "postal_code", "postal_code TEXT")
    add_column_if_missing(cursor, "users", "address1", "address1 TEXT")
    add_column_if_missing(cursor, "users", "address2", "address2 TEXT")
    add_column_if_missing(cursor, "orders", "user_id", "user_id INTEGER")
    add_column_if_missing(cursor, "orders", "progress", "progress INTEGER DEFAULT 0")
    add_column_if_missing(cursor, "orders", "estimated_time", "estimated_time INTEGER DEFAULT 0")

    # 관리자 계정이 없으면 기본 생성 (이메일: admin@example.com / 비밀번호: 123456)
    admin_email = "admin@example.com"
    existing_admin = cursor.execute("SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone()
    if not existing_admin:
        cursor.execute(
            "INSERT INTO users (email, password_hash, name, role) VALUES (?, ?, ?, ?)",
            (admin_email, generate_password_hash("123456"), "Administrator", "admin"),
        )

    conn.commit()
    conn.close()


# 앱 시작 시 DB 초기화 실행
init_db()


# ==================================================
# 인증 유틸리티
# ==================================================

def get_bearer_token():
    """Authorization 헤더에서 Bearer 토큰을 추출한다."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def hash_token(token):
    """토큰을 SHA-256으로 해시한다 (DB에는 해시값만 저장)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_auth_token(user_id):
    """
    랜덤 토큰을 생성하여 해시를 DB에 저장하고, 원본 토큰을 반환한다.
    클라이언트는 이 토큰을 Authorization: Bearer <token> 헤더로 사용한다.
    """
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
    """로그아웃 시 토큰 해시를 DB에서 삭제한다."""
    if not token:
        return
    conn = get_db()
    conn.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (hash_token(token),))
    conn.commit()
    conn.close()


def get_current_user():
    """
    현재 요청의 사용자를 반환한다.
    1순위: Authorization 헤더의 Bearer 토큰으로 조회
    2순위: Flask 세션의 user_id로 조회
    인증 실패 시 None 반환.
    """
    conn = get_db()

    # Bearer 토큰 인증 시도
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

    # 세션 기반 인증 시도
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
    """SQLite Row 객체를 JSON 직렬화 가능한 딕셔너리로 변환한다."""
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


# ==================================================
# 인증 데코레이터
# ==================================================

def login_required(view_func):
    """로그인한 사용자만 접근 가능한 뷰에 사용하는 데코레이터."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        return view_func(*args, **kwargs)
    return wrapped


def admin_required(view_func):
    """관리자(role=admin)만 접근 가능한 뷰에 사용하는 데코레이터."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        if user["role"] != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return view_func(*args, **kwargs)
    return wrapped


# ==================================================
# 주문 직렬화 유틸리티
# ==================================================

def serialize_order(row):
    """주문 DB Row를 JSON 직렬화 가능한 딕셔너리로 변환한다."""
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "userEmail": row["user_email"],   # LEFT JOIN으로 가져온 이메일
        "model": row["model"],
        "caseType": row["case_type"],
        "caseColor": row["case_color"],
        "totalPrice": row["total_price"],
        "image_path": row["image_path"],  # uploads/ 내 파일명
        "status": row["status"],
        "createdAt": row["created_at"],
        "progress": row["progress"],
        "estimatedTime": row["estimated_time"],
    }


def fetch_order_rows(where_clause="", params=()):
    """
    orders 테이블을 users 테이블과 LEFT JOIN하여 조회한다.
    where_clause: 추가 WHERE 조건 (예: "WHERE o.user_id = ?")
    params: WHERE 조건에 바인딩할 파라미터 튜플
    """
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


# ==================================================
# 정적 파일 서빙
# ==================================================

@app.route("/uploads/<filename>")
def serve_image(filename):
    """
    uploads/ 폴더의 파일(이미지·JSON)을 클라이언트에 제공한다.
    admin.html에서 주문 이미지를 표시하거나
    robot_drawer.py가 stroke JSON을 읽을 때 사용한다.
    """
    return send_from_directory(UPLOAD_FOLDER, filename)


# ==================================================
# 인증 API
# ==================================================

@app.route("/api/auth/signup", methods=["POST"])
def signup():
    """
    회원가입 API.
    이메일·비밀번호·이름·연락처·주소를 받아 users 테이블에 저장한다.
    성공 시 세션과 Bearer 토큰을 발급한다.
    """
    try:
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        name = (data.get("name") or "").strip()
        phone = (data.get("phone") or "").strip()
        postal_code = (data.get("postalCode") or "").strip()
        address1 = (data.get("address1") or "").strip()
        address2 = (data.get("address2") or "").strip()

        # 입력값 검증
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
            (email, generate_password_hash(password), name or None, phone or None,
             postal_code or None, address1 or None, address2 or None),
        )
        user_id = cursor.lastrowid
        conn.commit()

        user = conn.execute(
            "SELECT id, email, name, phone, postal_code, address1, address2, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        conn.close()

        # 세션 발급
        session.clear()
        session["user_id"] = user_id
        token = issue_auth_token(user_id)

        return jsonify({"success": True, "user": user_to_dict(user), "token": token})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/auth/login", methods=["POST"])
def login():
    """
    로그인 API.
    이메일·비밀번호 검증 후 세션과 Bearer 토큰을 발급한다.
    """
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

        # 사용자가 없거나 비밀번호 불일치
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
    """로그아웃 API. Bearer 토큰을 DB에서 삭제하고 세션을 초기화한다."""
    revoke_auth_token(get_bearer_token())
    session.clear()
    return jsonify({"success": True})


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    """현재 로그인 상태와 사용자 정보를 반환한다."""
    user = get_current_user()
    if not user:
        return jsonify({"authenticated": False, "user": None}), 200
    return jsonify({"authenticated": True, "user": user_to_dict(user)})


@app.route("/api/auth/profile", methods=["PUT"])
@login_required
def update_profile():
    """회원정보 수정 API. 이름·연락처·주소를 업데이트한다."""
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


# ==================================================
# 주문 API
# ==================================================

@app.route("/api/orders", methods=["POST"])
def create_order():
    """
    주문 생성 API.
    클라이언트(app.js)로부터 아래 데이터를 JSON으로 받는다:
      - model: 기종 문자열
      - caseType: 케이스 타입
      - caseColor: 범퍼 색상
      - totalPrice: 결제 금액
      - imageBase64: data:image/png;base64,... 형태의 캔버스 이미지
      - strokeData: {canvasWidth, canvasHeight, strokes:[...]} 형태의 드로잉 경로

    처리 순서:
      1. 이미지를 PNG로 디코딩하여 uploads/<uuid>.png 로 저장
      2. strokeData가 있으면 uploads/<uuid>.json 으로 저장
         (robot_drawer.py가 이 JSON을 읽어 로봇 경로로 변환함)
      3. orders 테이블에 주문 레코드 INSERT
      4. robot_logs에 신규 주문 수신 로그 추가
    """
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
        stroke_data = data.get("strokeData")  # 드로잉 stroke 좌표 데이터

        if not image_base64:
            return jsonify({"error": "Image data is required"}), 400

        # UUID 기반 고유 파일명 생성
        filename = f"{uuid.uuid4()}.png"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        image_saved = False

        # data:image/png;base64,... 형태 처리
        if isinstance(image_base64, str) and image_base64.startswith("data:image"):
            header, encoded = image_base64.split(",", 1)
            image_data = base64.b64decode(encoded)
            with open(filepath, "wb") as file_obj:
                file_obj.write(image_data)
            image_saved = True
        elif isinstance(image_base64, str):
            # 허용된 load_img 폴더 내 파일 경로인 경우 복사 (보안: 경로 탈출 방지)
            project_root = os.path.dirname(BASE_DIR)
            source_path = os.path.normpath(os.path.join(project_root, "phone", image_base64))
            allowed_root = os.path.normpath(os.path.join(project_root, "phone", "load_img"))
            if source_path.startswith(allowed_root) and os.path.isfile(source_path):
                shutil.copyfile(source_path, filepath)
                image_saved = True

        if not image_saved:
            return jsonify({"error": "Valid image data or an allowed load_img path is required"}), 400

        # --------------------------------------------------
        # stroke JSON 저장
        # strokeData.strokes 배열이 존재할 때만 JSON으로 저장
        # 저장 경로: uploads/<uuid>.json
        # robot_drawer.py의 find_stroke_json_path()가 이 경로를 탐색함
        # --------------------------------------------------
        stroke_json_saved = False
        stroke_json_file = None
        if stroke_data and isinstance(stroke_data, dict):
            strokes = stroke_data.get("strokes")
            if isinstance(strokes, list) and strokes:
                stroke_json_file = os.path.splitext(filename)[0] + ".json"
                stroke_json_path = os.path.join(UPLOAD_FOLDER, stroke_json_file)
                with open(stroke_json_path, "w", encoding="utf-8") as file_obj:
                    json.dump(stroke_data, file_obj, ensure_ascii=False)
                stroke_json_saved = True

        # 주문 DB 저장
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
                filename,       # PNG 파일명 (uploads/ 기준 상대경로)
                "waiting",      # 초기 상태: 로봇 대기 중
            ),
        )
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # 관리자 대시보드용 로그 추가
        robot_logs.append({
            "message": f"New order received: ID={order_id}, model={model}",
            "level": "info",
            "time": datetime.now().strftime("%H:%M:%S"),
        })

        return jsonify({
            "success": True,
            "order_id": order_id,
            "strokeJsonSaved": stroke_json_saved,  # JSON 저장 성공 여부
            "strokeJsonFile": stroke_json_file,    # 저장된 JSON 파일명 (없으면 None)
        })
    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/orders", methods=["GET"])
def get_orders():
    """전체 주문 목록을 반환한다 (admin 페이지용)."""
    rows = fetch_order_rows()
    return jsonify([serialize_order(row) for row in rows])


@app.route("/api/my/orders", methods=["GET"])
@login_required
def get_my_orders():
    """현재 로그인한 사용자의 주문 목록을 반환한다 (상태 폴링용)."""
    user = get_current_user()
    rows = fetch_order_rows("WHERE o.user_id = ?", (user["id"],))
    return jsonify([serialize_order(row) for row in rows])


@app.route("/api/orders/<int:order_id>/status", methods=["PATCH"])
def update_status(order_id):
    """
    주문 상태 변경 API.
    robot_drawer.py가 드로잉 시작/완료 시 이 엔드포인트를 호출한다.
    """
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
    """
    주문 진행률·예상 시간 업데이트 API.
    robot_drawer.py가 각 path를 그릴 때마다 호출한다.
    """
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


# ==================================================
# 로봇 로그 API
# ==================================================

@app.route("/api/robot_logs", methods=["POST"])
def add_robot_log():
    """
    로봇 로그 추가 API.
    robot_drawer.py와 tcp_monitor.py가 send_log()로 이 엔드포인트를 호출한다.
    최대 200개까지 인메모리로 유지하고, 초과 시 가장 오래된 것을 삭제한다.
    """
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
    """최근 100개의 로봇 로그를 반환한다 (admin 페이지 폴링용)."""
    return jsonify(robot_logs[-100:])


# ==================================================
# 로봇 상태 API
# ==================================================

@app.route("/api/robot_status", methods=["GET"])
def get_robot_status():
    """현재 로봇 상태(좌표, 단계, 펜, 주문 ID 등)를 반환한다."""
    return jsonify(robot_status)


@app.route("/api/robot_status", methods=["PATCH"])
def update_robot_status():
    """
    로봇 상태 업데이트 API.
    robot_drawer.py의 update_robot_status()가 이 엔드포인트를 호출한다.
    전달된 필드만 부분 업데이트(dict.update)한다.
    """
    global robot_status
    data = request.get_json(silent=True) or {}
    robot_status.update(data)
    return jsonify({"success": True})


# ==================================================
# 관리자 페이지
# ==================================================

@app.route("/admin")
def admin():
    """admin.html 페이지를 반환한다 (Flask 템플릿 렌더링)."""
    return render_template("admin.html")


# ==================================================
# 주문 관리 API (admin용)
# ==================================================

@app.route("/api/orders/<int:order_id>/reset", methods=["PATCH"])
def reset_order(order_id):
    """
    주문을 대기 상태로 초기화한다.
    오류가 발생하거나 재작업이 필요한 주문을 다시 대기열에 넣을 때 사용한다.
    """
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


@app.route("/api/orders/<int:order_id>", methods=["DELETE"])
def delete_order(order_id):
    """
    주문과 관련 이미지 파일을 삭제한다.
    DB 레코드와 uploads/ 내 PNG 파일을 함께 삭제한다.
    (stroke JSON 파일은 별도로 삭제하지 않음 — 필요 시 추가 가능)
    """
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


# ==================================================
# 앱 진입점
# ==================================================

if __name__ == "__main__":
    # 모든 네트워크 인터페이스(0.0.0.0)의 5000번 포트에서 실행
    # debug=True: 코드 변경 시 자동 재시작, 상세 오류 표시 (프로덕션에서는 False로)
    app.run(host="0.0.0.0", port=5000, debug=True)
