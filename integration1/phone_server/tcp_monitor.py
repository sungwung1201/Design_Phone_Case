import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "database.db")


class TCPMonitor(Node):
    def __init__(self):
        super().__init__('tcp_monitor')

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

    def callback(self, msg):
        data = msg.position

        self.cursor.execute(
            "INSERT INTO log (topic, data) VALUES (?, ?)",
            ('joint', str(data))
        )
        self.conn.commit()

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