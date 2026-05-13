import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
import sqlite3
import os
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "database.db")


def send_log(msg, level="info"):
    try:
        requests.post(
            "http://127.0.0.1:5000/api/robot_logs",
            json={"message": msg, "level": level},
            timeout=1
        )
    except:
        pass

class TCPMonitor(Node):
    def __init__(self):
        super().__init__('tcp_monitor')
        self.log_counter = 0

        # 🔥 ROS 구독 (수정됨)
        self.subscription = self.create_subscription(
            JointState,
            '/dsr01/joint_states',
            self.callback,
            10
        )

        # 🔥 DB 연결
        self.conn = sqlite3.connect(DB_NAME)
        self.cursor = self.conn.cursor()

        # 🔥 테이블 자동 생성
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT,
            data TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        self.conn.commit()

        self.get_logger().info("🔥 JointState → SQLite 저장 시작")
        send_log("✅ TCPMonitor 시작됨", "info")

    def callback(self, msg):
        data = msg.position

        self.cursor.execute(
            "INSERT INTO log (topic, data) VALUES (?, ?)",
            ('joint', str(data))
        )
        self.conn.commit()

        self.log_counter += 1
        if self.log_counter % 50 == 0:  # ✅ 50번에 1번만 로그 전송
            send_log(f"관절값 수신 중... {data}", "info")
        self.get_logger().info(f"저장됨 → {data}")


def main(args=None):
    rclpy.init(args=args)
    node = TCPMonitor()
    rclpy.spin(node)

    node.conn.close()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
