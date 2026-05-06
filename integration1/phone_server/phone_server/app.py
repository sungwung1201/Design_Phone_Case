from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
import sqlite3
import os
import uuid
import base64
from datetime import datetime

app = Flask(__name__)
# 모든 출처(Origin)로부터의 API 요청을 허용 (CORS 문제 해결)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ==================================================
# 🔥 경로 설정
# ==================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
DB_NAME = os.path.join(BASE_DIR, "database.db")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ==================================================
# 🔥 실시간 로봇 로그 저장
# ==================================================
robot_logs = []

# ==================================================
# 🔥 실시간 로봇 상태 저장
# ==================================================
robot_status = {
    "state": "IDLE",
    "stage": "WAITING",
    "pen": "NONE",
    "orderId": "-",
    "x": 0,
    "y": 0,
    "z": 0,
    "currentPath": 0,
    "totalPath": 0
}

# ==================================================
# 🔥 DB 연결 함수
# ==================================================
def get_db():
    return sqlite3.connect(DB_NAME)

# ==================================================
# 🔥 DB 초기화
# ==================================================
def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model TEXT,
        case_type TEXT,
        case_color TEXT,
        total_price INTEGER,
        image_path TEXT,
        status TEXT DEFAULT 'waiting',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN progress INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN estimated_time INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

init_db()

# ==================================================
# 🔥 이미지 접근
# ==================================================
@app.route("/uploads/<filename>")
def serve_image(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ==================================================
# 🔥 주문 생성
# ==================================================
@app.route("/api/orders", methods=["POST"])
def create_order():
    try:
        data = request.json
        
        # 기종 정보 보정 (null 방지)
        model = data.get("model") or "iPhone 15 Plus"
        case_type = data.get("caseType", "clear")
        case_color = data.get("caseColor", "black")
        total_price = data.get("totalPrice", 35000)
        image_base64 = data.get("imageBase64")

        if not image_base64:
            return jsonify({"error": "이미지 없음"}), 400

        header, encoded = image_base64.split(",", 1)
        image_data = base64.b64decode(encoded)

        filename = f"{uuid.uuid4()}.png"
        filepath = os.path.join(UPLOAD_FOLDER, filename)

        with open(filepath, "wb") as f:
            f.write(image_data)

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO orders (model, case_type, case_color, total_price, image_path, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (model, case_type, case_color, total_price, filename, 'waiting'))

        order_id = cursor.lastrowid
        conn.commit()
        conn.close()

        robot_logs.append({
            "message": f"🔔 새 주문 접수: ID={order_id}, 기종={model}",
            "level": "info",
            "time": datetime.now().strftime("%H:%M:%S")
        })

        return jsonify({"success": True, "order_id": order_id})

    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"error": str(e)}), 500

# ==================================================
# 🔥 주문 목록 조회
# ==================================================
@app.route("/api/orders", methods=["GET"])
def get_orders():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id, model, case_type, case_color,
           total_price, image_path, status,
           created_at, progress, estimated_time
    FROM orders
    ORDER BY created_at DESC
    """)

    rows = cursor.fetchall()

    conn.close()

    result = []

    for row in rows:
        result.append({
            "id": row[0],
            "model": row[1],
            "caseType": row[2],
            "caseColor": row[3],
            "totalPrice": row[4],
            "image_path": row[5],
            "status": row[6],
            "createdAt": row[7],
            "progress": row[8],
            "estimatedTime": row[9]
        })

    return jsonify(result)

# ==================================================
# 🔥 상태 변경
# ==================================================
@app.route("/api/orders/<int:order_id>/status", methods=["PATCH"])
def update_status(order_id):
    try:
        data = request.json

        status = data.get("status")

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
        UPDATE orders
        SET status=?
        WHERE id=?
        """, (status, order_id))

        conn.commit()
        conn.close()

        robot_logs.append({
            "message": f"📦 주문 #{order_id} 상태 변경 → {status}",
            "level": "info",
            "time": datetime.now().strftime("%H:%M:%S")
        })

        return jsonify({"message": "상태 변경 완료"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================================================
# 🔥 진행률 업데이트
# ==================================================
@app.route("/api/orders/<int:order_id>/progress", methods=["PATCH"])
def update_progress(order_id):
    try:
        data = request.json

        progress = data.get("progress", 0)
        estimated_time = data.get("estimated_time", 0)

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
        UPDATE orders
        SET progress=?, estimated_time=?
        WHERE id=?
        """, (progress, estimated_time, order_id))

        conn.commit()
        conn.close()

        return jsonify({"message": "진행률 업데이트 완료"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================================================
# 🔥 실시간 로봇 로그 받기
# ==================================================
@app.route("/api/robot_logs", methods=["POST"])
def add_robot_log():
    try:
        data = request.json

        robot_logs.append({
            "message": data.get("message", ""),
            "level": data.get("level", "info"),
            "time": datetime.now().strftime("%H:%M:%S")
        })

        # 로그 최대 200개 유지
        if len(robot_logs) > 200:
            robot_logs.pop(0)

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================================================
# 🔥 실시간 로봇 로그 조회
# ==================================================
@app.route("/api/robot_logs", methods=["GET"])
def get_robot_logs():
    return jsonify(robot_logs[-100:])

# ==================================================
# 🔥 로봇 상태 조회
# ==================================================
@app.route("/api/robot_status", methods=["GET"])
def get_robot_status():
    return jsonify(robot_status)

# ==================================================
# 🔥 로봇 상태 업데이트
# ==================================================
@app.route("/api/robot_status", methods=["PATCH"])
def update_robot_status():

    global robot_status

    data = request.json

    robot_status.update(data)

    return jsonify({
        "success": True
    })

# ==================================================
# 🔥 관리자 페이지
# ==================================================
@app.route("/admin")
def admin():
    return render_template("admin.html")

# ==================================================
# 🔥 주문 초기화
# ==================================================
@app.route("/api/orders/<int:order_id>/reset", methods=["PATCH"])
def reset_order(order_id):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
        UPDATE orders
        SET status='waiting',
            progress=0,
            estimated_time=0
        WHERE id=?
        """, (order_id,))

        conn.commit()
        conn.close()

        return jsonify({"message": "주문 초기화 완료"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================================================
# 🔥 주문 삭제
# ==================================================
@app.route("/api/orders/<int:order_id>", methods=["DELETE"])
def delete_order(order_id):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT image_path
        FROM orders
        WHERE id=?
        """, (order_id,))

        row = cursor.fetchone()

        if row and row[0]:
            filepath = os.path.join(UPLOAD_FOLDER, row[0])

            if os.path.exists(filepath):
                os.remove(filepath)

        cursor.execute("""
        DELETE FROM orders
        WHERE id=?
        """, (order_id,))

        conn.commit()
        conn.close()

        return jsonify({"message": "주문 삭제 완료"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================================================
# 🔥 서버 실행
# ==================================================
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )