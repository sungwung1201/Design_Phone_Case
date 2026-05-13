# ==================================================
# tcp_monitor.py
# ROS2 노드: /dsr01/joint_states 토픽을 구독하여
# 로봇 관절값을 SQLite DB에 저장하고,
# 50번에 1번씩 Flask 서버 로그 API로 전송한다.
# ==================================================

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState  # ROS2 관절 상태 메시지 타입
import sqlite3
import os
import requests

# 이 파일이 위치한 디렉토리를 기준으로 DB 경로를 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "database.db")


def send_log(msg, level="info"):
    """
    Flask 서버의 /api/robot_logs 엔드포인트로 로그 메시지를 전송한다.
    timeout=1초로 설정하여 서버가 없어도 블로킹되지 않게 한다.
    실패해도 예외를 무시한다 (로봇 동작에 영향 없도록).
    """
    try:
        requests.post(
            "http://127.0.0.1:5000/api/robot_logs",
            json={"message": msg, "level": level},
            timeout=1
        )
    except:
        pass


class TCPMonitor(Node):
    """
    ROS2 노드 클래스.
    /dsr01/joint_states 토픽을 구독하여 관절값을 DB에 저장한다.
    """

    def __init__(self):
        super().__init__('tcp_monitor')

        # 로그 전송 빈도 조절용 카운터 (50번에 1번만 전송)
        self.log_counter = 0

        # /dsr01/joint_states 토픽 구독 설정
        # 큐 사이즈 10: 처리 못 한 메시지를 최대 10개까지 버퍼링
        self.subscription = self.create_subscription(
            JointState,
            '/dsr01/joint_states',
            self.callback,
            10
        )

        # SQLite DB 연결
        self.conn = sqlite3.connect(DB_NAME)
        self.cursor = self.conn.cursor()

        # log 테이블이 없으면 자동 생성
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT,          -- 수신한 ROS 토픽 이름
            data TEXT,           -- 관절값 문자열
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        self.conn.commit()

        self.get_logger().info("🔥 JointState → SQLite 저장 시작")
        send_log("✅ TCPMonitor 시작됨", "info")

    def callback(self, msg):
        """
        /dsr01/joint_states 토픽 메시지 수신 시 호출되는 콜백.
        관절 위치값(position)을 DB에 저장하고,
        50번에 1번만 Flask 서버로 로그를 전송한다.
        """
        # msg.position: 로봇 각 관절의 현재 각도 배열
        data = msg.position

        # DB에 관절값 INSERT
        self.cursor.execute(
            "INSERT INTO log (topic, data) VALUES (?, ?)",
            ('joint', str(data))
        )
        self.conn.commit()

        # 매 수신마다 로그를 보내면 서버 부하가 크므로 50번에 1번만 전송
        self.log_counter += 1
        if self.log_counter % 50 == 0:
            send_log(f"관절값 수신 중... {data}", "info")

        self.get_logger().info(f"저장됨 → {data}")


def main(args=None):
    """
    ROS2 노드 진입점.
    노드를 초기화하고 spin()으로 토픽 수신 대기 루프를 실행한다.
    종료 시 DB 연결을 닫고 노드를 정리한다.
    """
    rclpy.init(args=args)
    node = TCPMonitor()
    rclpy.spin(node)  # 토픽 수신 대기 (블로킹)

    # 정상 종료 처리
    node.conn.close()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
