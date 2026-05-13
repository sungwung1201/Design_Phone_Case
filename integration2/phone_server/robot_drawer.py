# -*- coding: utf-8 -*-
# ============================================================
# robot_drawer.py
# ------------------------------------------------------------
# 역할:
#   1) SQLite orders 테이블에서 waiting 주문을 주기적으로 조회합니다.
#   2) 주문 이미지와 같은 이름의 stroke JSON 파일을 우선 읽습니다.
#      예: uploads/abc.png → uploads/abc.json 또는 uploads/abc_strokes.json
#   3) stroke JSON이 있으면 캔버스 좌표를 로봇 좌표로 변환해서 그립니다.
#   4) stroke JSON이 없으면 이미지 contour 기반 centerline 경로 추출로 fallback합니다.
#   5) Doosan M0609 ROS2 서비스(move_line, move_stop, digital output)를 호출해
#      케이스 픽업 → 케이스 배치 → 펜 픽업 → 드로잉 → 펜 반납 → 완성 케이스 이동을 수행합니다.
#   6) 작업 상태, 진행률, 로봇 로그를 Flask 서버 관리자 페이지로 전송합니다.
#
# 주의:
#   - 이 파일은 기존 동작 로직을 유지하고, 이해를 돕기 위한 설명 주석을 추가한 버전입니다.
#   - 실제 로봇 좌표/속도/Z값은 현장 세팅에 맞춰 조정해야 합니다.
# ============================================================

# ROS2 Python 클라이언트 라이브러리입니다.
# Node 생성, topic/service 통신, spin 실행에 사용됩니다.
import rclpy
from rclpy.node import Node

# 주문 상태 조회/수정용 SQLite, 경로/시간/스레드/수학/JSON 처리용 표준 라이브러리입니다.
import sqlite3
import os
import time
from pathlib import Path
import threading
import math
import json

# 이미지 fallback 경로 추출에 사용하는 OpenCV와 NumPy입니다.
import cv2
import numpy as np

# Flask 서버 관리자 페이지에 로그/상태를 보내기 위한 HTTP 요청 라이브러리입니다.
import requests


# 주문 취소 흐름을 일반 예외와 분리하기 위한 사용자 정의 예외입니다.
# 작업 중 cancel_requested 상태가 감지되면 이 예외를 발생시켜 복구 루틴으로 이동합니다.
class OrderCancelled(Exception):
    pass


# 외력/충돌/Protective Stop 감지 흐름을 일반 예외와 분리하기 위한 사용자 정의 예외입니다.
# move_line 실패나 RobotState 위험 신호가 감지되면 이 예외를 발생시켜 안전 정지 상태로 전환합니다.
class ImpactStopped(Exception):
    pass

# Doosan ROS2 서비스 메시지를 불러옵니다.
# 실제 로봇 환경에서는 dsr_msgs2가 있어야 move_line, gripper I/O, move_stop을 호출할 수 있습니다.
try:
    from dsr_msgs2.srv import MoveLine, SetCtrlBoxDigitalOutput, MoveStop
    HAS_DSR_MSGS = True
    HAS_MOVE_STOP = True
except ImportError:
    try:
        from dsr_msgs2.srv import MoveLine, SetCtrlBoxDigitalOutput
        MoveStop = None
        HAS_DSR_MSGS = True
        HAS_MOVE_STOP = False
    except ImportError:
        MoveLine = None
        SetCtrlBoxDigitalOutput = None
        MoveStop = None
        HAS_DSR_MSGS = False
        HAS_MOVE_STOP = False

# RobotState 메시지가 있으면 보호정지/충돌/외력 키워드를 topic으로도 감시합니다.
try:
    from dsr_msgs2.msg import RobotState
    HAS_DSR_STATE_MSG = True
except ImportError:
    RobotState = None
    HAS_DSR_STATE_MSG = False


# ==================================================
# 로봇 드로잉 가능 영역 좌표
# ==================================================
# ---------------- 로봇 드로잉 가능 영역 좌표 ----------------
# 실제 케이스 위에 그림을 그릴 수 있는 로봇 좌표 범위입니다.
ROBOT_MIN_X = 499.116
ROBOT_MAX_X = 563.307
ROBOT_MIN_Y = -61.348
ROBOT_MAX_Y = 49.571

# 로봇 대기 위치입니다. 작업 시작/종료 시 이 위치로 이동합니다.
ROBOT_HOME_X = 486.372
ROBOT_HOME_Y = -26.267

# 펜이 종이에 닿는 높이(DRAW_Z)와 안전 이동 높이(SAFE_Z)입니다.
DRAW_Z = 344.344
SAFE_Z = 422.344

# 선과 선 사이를 이동할 때 펜을 살짝 들어 올리는 높이 보정값입니다.
DRAW_HOP_OFFSET = 8.0

# 툴 방향값입니다. move_line의 rx, ry, rz에 사용됩니다.
TOOL_RX = 19.757
TOOL_RY = -179.020
TOOL_RZ = 20.665


# ==================================================
# iPhone 15 Plus 드로잉 영역 가장자리에서 제외할 여백
# ==================================================
# 드로잉 영역 가장자리에서 제외할 여백입니다.
DRAW_MARGIN = 2.0


# ==================================================
# Z 보정값
# ==================================================
# 케이스 표면이 완전히 평평하지 않을 때를 대비한 4점 Z 보정값입니다.
Z_LT = DRAW_Z
Z_RT = DRAW_Z
Z_LB = DRAW_Z
Z_RB = DRAW_Z


# ==================================================
# 펜 거치대 좌표
# ==================================================
# ---------------- 펜 거치대 좌표 ----------------
# 파랑/검정/빨강 펜을 집고 반납할 위치입니다.
STAND_PICK_BLUE_X = 371.864
STAND_PICK_BLUE_Y = -63.731
STAND_PICK_BLUE_Z = 271.854
STAND_PICK_BLUE_RX = 5.755
STAND_PICK_BLUE_RY = -178.407
STAND_PICK_BLUE_RZ = 6.937

STAND_PICK_BLACK_X = 365.756
STAND_PICK_BLACK_Y = 17.580
STAND_PICK_BLACK_Z = STAND_PICK_BLUE_Z
STAND_PICK_BLACK_RX = 27.510
STAND_PICK_BLACK_RY = -178.967
STAND_PICK_BLACK_RZ = 28.983

STAND_PICK_RED_X = 370.738
STAND_PICK_RED_Y = 96.293
STAND_PICK_RED_Z = STAND_PICK_BLUE_Z
STAND_PICK_RED_RX = 26.408
STAND_PICK_RED_RY = -178.803
STAND_PICK_RED_RZ = 27.580


# ==================================================
# 빈 케이스/완성 케이스 위치 좌표
# ==================================================
# ---------------- 빈 케이스/완성 케이스 위치 좌표 ----------------
# 빈 케이스 픽업, 작업대 배치, 완성 케이스 드롭 위치입니다.
CASE_PICK_X = 499.754
CASE_PICK_Y = 206.938
CASE_PICK_Z = 266.801
CASE_PICK_RX = 122.330
CASE_PICK_RY = -178.767
CASE_PICK_RZ = -145.604

CASE_PICK_SAFE_X = 499.754
CASE_PICK_SAFE_Y = 206.938
CASE_PICK_SAFE_Z = 350.0
CASE_PICK_SAFE_RX = 122.330
CASE_PICK_SAFE_RY = -178.767
CASE_PICK_SAFE_RZ = -145.604

CASE_PLACE_SAFE_X = 489.233
CASE_PLACE_SAFE_Y = -4.865
CASE_PLACE_SAFE_Z = 350.0
CASE_PLACE_SAFE_RX = 156.240
CASE_PLACE_SAFE_RY = -179.332
CASE_PLACE_SAFE_RZ = 154.718

CASE_PLACE_X = 489.233
CASE_PLACE_Y = -4.865
CASE_PLACE_Z = 295.000
CASE_PLACE_RX = 156.240
CASE_PLACE_RY = -179.332
CASE_PLACE_RZ = 154.718

CASE_DROP_X = 503.178
CASE_DROP_Y = -211.982
CASE_DROP_Z = 275.109
CASE_DROP_RX = 132.609
CASE_DROP_RY = -176.921
CASE_DROP_RZ = -137.694

CASE_DROP_SAFE_X = 503.178
CASE_DROP_SAFE_Y = -211.982
CASE_DROP_SAFE_Z = 350.0
CASE_DROP_SAFE_RX = 132.609
CASE_DROP_SAFE_RY = -176.921
CASE_DROP_SAFE_RZ = -137.694


# ==================================================
# 구간별 이동 속도/가속도
# ==================================================
# ---------------- 구간별 이동 속도/가속도 ----------------
# 전체 기본 이동, 케이스 이동, 펜 이동, 드로잉 이동에 각각 다른 속도를 사용합니다.
MOVE_VEL = 200.0
MOVE_ACC = 100.0


CASE_TRAVEL_VEL = 400.0
CASE_TRAVEL_ACC = 150.0

CASE_PICK_DESCEND_VEL = 300.0
CASE_PICK_DESCEND_ACC = 100.0

CASE_PLACE_DESCEND_VEL = 300.0
CASE_PLACE_DESCEND_ACC = 100.0

CASE_DROP_DESCEND_VEL = 300.0
CASE_DROP_DESCEND_ACC = 100.0

CASE_LOADED_ASCEND_VEL = 300.0
CASE_LOADED_ASCEND_ACC = 100.0

CASE_EMPTY_ASCEND_VEL = 300.0
CASE_EMPTY_ASCEND_ACC = 100.0

PEN_TRAVEL_VEL = 300.0
PEN_TRAVEL_ACC = 100.0

PEN_PICK_DESCEND_VEL = 300.0
PEN_PICK_DESCEND_ACC = 100.0

PEN_INSERT_DESCEND_VEL = 300.0
PEN_INSERT_DESCEND_ACC = 100.0

PEN_LOADED_ASCEND_VEL = 300.0
PEN_LOADED_ASCEND_ACC = 100.0

PEN_EMPTY_ASCEND_VEL = 300.0
PEN_EMPTY_ASCEND_ACC = 150.0

DRAW_APPROACH_VEL = 300.0
DRAW_APPROACH_ACC = 100.0

DRAW_DESCEND_VEL = 50.0
DRAW_DESCEND_ACC = 25.0

DRAW_LINE_VEL = 100.0
DRAW_LINE_ACC = 30.0

DRAW_LIFT_VEL = 100.0
DRAW_LIFT_ACC = 80.0

HOME_RETURN_VEL = 400.0
HOME_RETURN_ACC = 150.0


# ==================================================
# 드로잉 경로 처리 파라미터
# ==================================================
# ---------------- 드로잉 경로 처리 파라미터 ----------------
# 선 연결, 경로 샘플링, 짧은 경로 제거 등에 사용하는 값입니다.
LINE_BLEND_RADIUS = 0.5

LINE_POINT_MIN_WAIT = 0.10
LINE_POINT_MAX_WAIT = 0.20

CURVE_MIN_DIST_MM = 0.2


MIN_MASK_COMPONENT_AREA = 5

SPLINE_RESAMPLE_STEP_MM = 0.2
MIN_PATH_LENGTH_MM = 0.1
PATH_CONNECT_HARD_LIMIT_MM = 1.3
PATH_CONNECT_ENDPOINT_TO_PATH_MM = 1.3
NO_LIFT_BETWEEN_PATH_GAP_MM = 1.3
PATH_CONNECT_GAP_MM = PATH_CONNECT_HARD_LIMIT_MM


# ==================================================
# 디버그 모드 설정
# ==================================================
# ---------------- 디버그 모드 설정 ----------------
# 특정 단계/색상/path 수만 실행하고 싶을 때 사용합니다.
DEBUG_MODE = False

DEBUG_START_STAGE = "PICKUP_PEN"
DEBUG_END_STAGE = "PICKUP_PEN"

DEBUG_COLOR = "BLUE"
DEBUG_MAX_PATHS = None

STAGE_ORDER = [
    "CASE_PICKUP",
    "CASE_PLACE",
    "PICKUP_PEN",
    "DRAW",
    "PLACE_PEN",
    "FINISHED_CASE_PICKUP",
    "CASE_DROP",
    "HOME",
]


# ==================================================
# 파일/DB 경로
# ==================================================
# ---------------- 파일/DB 경로 ----------------
# Flask 서버와 같은 폴더 기준으로 database.db와 uploads 폴더를 사용합니다.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "database.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
DEBUG_OUTPUT_FOLDER = os.path.join(BASE_DIR, "debug_draw")
SAVE_DRAW_DEBUG_IMAGES = True

# ---------------- stroke JSON 우선 처리 설정 ----------------
# JSON 좌표가 있으면 이미지 분석보다 JSON을 우선 사용합니다.
USE_STROKE_JSON_FIRST = True
STROKE_JSON_CANDIDATE_SUFFIXES = [".json", "_strokes.json", ".strokes.json"]
CATMULL_ROM_SAMPLES_PER_SEGMENT = 8
CONTOUR_MIN_AREA_PX = 3.0
CONTOUR_APPROX_EPSILON_PX = 0.0

# 설명: Flask 관리자 페이지의 /api/robot_logs로 로봇 로그를 전송합니다. 실패해도 로봇 작업은 계속 진행합니다.
def send_log(msg, level="info"):
    try:
        requests.post(
            "http://127.0.0.1:5000/api/robot_logs",
            json={
                "message": msg,
                "level": level
            },
            timeout=1
        )
    except:
        pass


# 설명: Flask 관리자 페이지의 /api/robot_status로 현재 로봇 상태, stage, 좌표, 진행률을 전송합니다.
def update_robot_status(**kwargs):

    try:

        requests.patch(
            "http://127.0.0.1:5000/api/robot_status",
            json=kwargs,
            timeout=1
        )

    except:
        pass

class RobotDrawerNode(Node):
    # 설명: ROS2 노드를 초기화하고 DB, timer, ROS 서비스 클라이언트, 상태 감시 topic을 준비합니다.
    def __init__(self):
        super().__init__("robot_drawer")

        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.timer = self.create_timer(1.0, self.poll_database)
        self.is_drawing = False
        self.current_order_id = None
        self.cancel_event = threading.Event()
        self.cancel_monitor_thread = None
        self.pen_in_gripper = False
        self.active_pen_color = None
        self.case_in_gripper = False
        self.case_on_work_area = False
        self.pen_is_down = False
        self.last_draw_point = None
        self.robot_state_subscriptions = []
        self.last_impact_signature = None
        self.impact_stop_event = threading.Event()
        self.impact_stop_reason = None
        self.last_commanded_pose = None
        self.motion_stop_requested = False
        self.cancel_recovery_active = False

        if HAS_DSR_MSGS:
            self.move_line_client = self.create_client(
                MoveLine,
                "/dsr01/motion/move_line",
            )
            self.move_stop_client = None
            if HAS_MOVE_STOP:
                self.move_stop_client = self.create_client(
                    MoveStop,
                    "/dsr01/motion/move_stop",
                )
            self.set_io_client = self.create_client(
                SetCtrlBoxDigitalOutput,
                "/dsr01/io/set_ctrl_box_digital_output",
            )

            if HAS_DSR_STATE_MSG:
                self.setup_robot_state_monitor()
            else:
                self.get_logger().warn("RobotState message type not available. Impact-stop topic monitoring disabled.")

            self.get_logger().info("Required robot and drawing packages loaded")
        else:
            self.get_logger().warn("dsr_msgs2 not available. Running in log-only simulation mode.")

        self.get_logger().info("Robot drawer ready. Waiting for orders.")
        self.get_logger().info("Mode: Canvas Stroke JSON preferred + Component Boundary Centerline fallback + MoveLine")
        self.get_logger().info(f"iPhone 15 Plus case safe margin: {DRAW_MARGIN} mm")
        self.get_logger().info(f"Curve assist min_dist: {CURVE_MIN_DIST_MM} mm")
        self.get_logger().info(f"DRAW_HOP_OFFSET: {DRAW_HOP_OFFSET} mm")
        self.get_logger().info(f"SPLINE_RESAMPLE_STEP_MM: {SPLINE_RESAMPLE_STEP_MM} mm")
        self.get_logger().info(f"PATH_CONNECT_HARD_LIMIT_MM: {PATH_CONNECT_HARD_LIMIT_MM} mm")
        self.get_logger().info("Path connect rule: connect under 1.3mm, otherwise keep direction-agnostic separation")
        self.get_logger().info(f"Endpoint-to-path connect threshold: {PATH_CONNECT_ENDPOINT_TO_PATH_MM} mm")
        self.get_logger().info(f"No-lift gap threshold between nearby paths: {NO_LIFT_BETWEEN_PATH_GAP_MM} mm")
        self.get_logger().info(f"Save debug images: {SAVE_DRAW_DEBUG_IMAGES}, path: {DEBUG_OUTPUT_FOLDER}")

        if DEBUG_MODE:
            self.get_logger().warn("DEBUG_MODE=True")
            self.get_logger().warn(f"DEBUG_START_STAGE={DEBUG_START_STAGE}")
            self.get_logger().warn(f"DEBUG_END_STAGE={DEBUG_END_STAGE}")
            self.get_logger().warn(f"DEBUG_COLOR={DEBUG_COLOR}")
            self.get_logger().warn(f"DEBUG_MAX_PATHS={DEBUG_MAX_PATHS}")

        self.move_home_on_startup()

    # 설명: RobotState topic 후보들을 구독해 외력/보호정지 신호를 감시합니다.
    def setup_robot_state_monitor(self):
        topics = [
            "/dsr01/state/robot_state",
            "/dsr01/system/robot_state",
            "/robot_state",
        ]

        for topic in topics:
            try:
                sub = self.create_subscription(
                    RobotState,
                    topic,
                    self.on_robot_state_message,
                    10,
                )
                self.robot_state_subscriptions.append(sub)
                self.get_logger().info(f"Subscribed to robot state topic: {topic}")
            except Exception as e:
                self.get_logger().warn(f"Failed to subscribe robot state topic {topic}: {e}")

        if not self.robot_state_subscriptions:
            self.get_logger().warn("No robot state subscriptions were created. Impact-stop topic monitoring is unavailable.")

    # 설명: RobotState 메시지 내용을 검사해서 충돌/외력/정지 관련 플래그나 키워드가 있으면 안전정지 처리합니다.
    def on_robot_state_message(self, msg):
        summary = self.describe_robot_state_message(msg)
        lowered = summary.lower()

        bool_indicators = []
        for attr in ("collision_detected", "protective_stop", "safety_stop", "emergency_stop", "is_stopped"):
            value = getattr(msg, attr, None)
            if isinstance(value, bool) and value:
                bool_indicators.append(attr)

        keyword_hits = [
            keyword for keyword in (
                "collision",
                "collison",
                "impact",
                "external force",
                "protective stop",
                "protective_stop",
                "safety stop",
                "safety_stop",
                "emergency stop",
                "robot stop",
                "robot_stop",
                "crash",
                "shock",
                "force",
            )
            if keyword in lowered
        ]

        if bool_indicators or keyword_hits:
            reason_parts = []
            if bool_indicators:
                reason_parts.append("flags=" + ",".join(bool_indicators))
            if keyword_hits:
                reason_parts.append("keywords=" + ",".join(keyword_hits))
            reason_parts.append(summary)
            self.report_impact_stop(" | ".join(reason_parts))

    # 설명: RobotState 메시지에서 의미 있는 필드만 뽑아 로그용 문자열로 만듭니다.
    def describe_robot_state_message(self, msg):
        parts = []
        for attr in (
            "robot_state",
            "state",
            "robot_state_str",
            "state_str",
            "state_string",
            "state_name",
            "stop_state",
            "stop_reason",
            "alarm",
            "error",
            "error_code",
            "collision_detected",
            "protective_stop",
            "safety_stop",
            "emergency_stop",
        ):
            if hasattr(msg, attr):
                try:
                    value = getattr(msg, attr)
                    parts.append(f"{attr}={value}")
                except Exception:
                    pass

        if parts:
            return "; ".join(parts)

        return str(msg)

    # 설명: 외력/보호정지 상황을 latch 처리하고 서버 관리자 페이지에 STOPPED/IMPACT_STOP 상태를 알립니다.
    def report_impact_stop(self, reason):
        reason = str(reason).strip() or "unknown reason"
        signature = reason.lower()
        if signature == self.last_impact_signature:
            return

        self.last_impact_signature = signature
        self.impact_stop_reason = reason
        self.impact_stop_event.set()
        order_suffix = f" during order {self.current_order_id}" if self.current_order_id else ""
        message = f"External impact or protective stop detected{order_suffix}: {reason}"

        self.get_logger().error(message)
        send_log(message, "error")
        update_robot_status(
            state="STOPPED",
            stage="IMPACT_STOP",
            orderId=self.current_order_id,
            stopReason=reason[:160],
        )

    # 설명: move_line 응답 내용을 분석해서 success=False 또는 stop 키워드가 있으면 안전정지로 전환합니다.
    def inspect_motion_result_for_stop(self, result, x, y, z):
        try:
            summary = str(result)
        except Exception:
            summary = "<unprintable motion result>"

        lowered = summary.lower()
        stop_keywords = (
            "collision",
            "collison",
            "impact",
            "external force",
            "protective stop",
            "protective_stop",
            "safety stop",
            "safety_stop",
            "emergency stop",
            "robot stop",
            "shock",
        )

        if any(keyword in lowered for keyword in stop_keywords):
            self.report_impact_stop(
                f"move_line result near x={x:.3f}, y={y:.3f}, z={z:.3f}: {summary}"
            )
            return

        success = getattr(result, "success", None)
        if success is False:
            self.report_impact_stop(
                f"move_line reported failure near x={x:.3f}, y={y:.3f}, z={z:.3f}: {summary}"
            )

    # 설명: DEBUG_MODE일 때 현재 단계가 실행 범위 안에 있는지 판단합니다.
    def should_run_stage(self, stage_name):
        if not DEBUG_MODE:
            return True

        if stage_name not in STAGE_ORDER:
            self.get_logger().error(f"Unknown stage_name: {stage_name}")
            return False

        if DEBUG_START_STAGE not in STAGE_ORDER:
            self.get_logger().error(f"Invalid DEBUG_START_STAGE: {DEBUG_START_STAGE}")
            return False

        if DEBUG_END_STAGE not in STAGE_ORDER:
            self.get_logger().error(f"Invalid DEBUG_END_STAGE: {DEBUG_END_STAGE}")
            return False

        start_idx = STAGE_ORDER.index(DEBUG_START_STAGE)
        end_idx = STAGE_ORDER.index(DEBUG_END_STAGE)
        current_idx = STAGE_ORDER.index(stage_name)

        if start_idx > end_idx:
            self.get_logger().error("DEBUG_START_STAGE must not come after DEBUG_END_STAGE.")
            return False

        return start_idx <= current_idx <= end_idx

    # 설명: 추출된 경로 중 실제로 그릴 색상 순서를 결정합니다.
    def get_colors_to_draw(self, color_paths):
        available = [c for c in ["RED", "BLUE", "BLACK"] if color_paths[c]]

        if not DEBUG_MODE:
            return available

        if DEBUG_COLOR == "ALL":
            return available

        if DEBUG_COLOR in ["RED", "BLUE", "BLACK"]:
            if color_paths[DEBUG_COLOR]:
                return [DEBUG_COLOR]

            self.get_logger().warn(f"No paths found for debug color: {DEBUG_COLOR}")
            return []

        self.get_logger().warn(f"Invalid DEBUG_COLOR setting: {DEBUG_COLOR}")
        return available

    # 설명: Doosan move_line 서비스를 호출해 TCP를 지정 좌표로 이동합니다. 취소/외력 이벤트를 중간에 계속 확인합니다.
    def move_to_pos(
        self,
        x,
        y,
        z,
        rx=TOOL_RX,
        ry=TOOL_RY,
        rz=TOOL_RZ,
        vel=None,
        acc=None,
        radius=0.0,
        wait_response=False,
        response_timeout=None,   # None → 거리/속도 기반 자동 계산
        allow_cancel=True,
    ):
        if self.impact_stop_event.is_set() and self.current_order_id is not None and not self.cancel_recovery_active:
            raise ImpactStopped(self.current_order_id)

        if allow_cancel and self.cancel_event.is_set() and not self.cancel_recovery_active and self.current_order_id is not None:
            self.stop_robot_motion()
            raise OrderCancelled(self.current_order_id)

        if vel is None:
            vel = MOVE_VEL

        if acc is None:
            acc = MOVE_ACC

        # ✅ response_timeout 자동 계산: 이전 명령 위치 → 현재 목표 거리 / 속도 + 여유 5초
        if response_timeout is None:
            if self.last_commanded_pose is not None:
                px, py, pz = self.last_commanded_pose[:3]
                dist_mm = math.sqrt(
                    (x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2
                )
            else:
                dist_mm = 500.0  # 초기값: 보수적으로 큰 값
            # 거리(mm) / 속도(mm/s) + 가속/감속 여유 3초 + 서비스 지연 여유 3초
            estimated_travel_sec = (dist_mm / max(vel, 1.0)) + 6.0
            response_timeout = max(10.0, estimated_travel_sec)

        if not HAS_DSR_MSGS:
            self.get_logger().info(
                f"[SIM] move_to_pos "
                f"x={x:.3f}, y={y:.3f}, z={z:.3f}, "
                f"rx={rx:.3f}, ry={ry:.3f}, rz={rz:.3f}, "
                f"vel={vel:.1f}, acc={acc:.1f}, radius={radius:.2f}"
            )
            return

        while not self.move_line_client.wait_for_service(timeout_sec=1.0):
            if self.impact_stop_event.is_set() and self.current_order_id is not None and not self.cancel_recovery_active:
                raise ImpactStopped(self.current_order_id)
            if allow_cancel and self.cancel_event.is_set() and not self.cancel_recovery_active and self.current_order_id is not None:
                self.stop_robot_motion()
                raise OrderCancelled(self.current_order_id)
            self.get_logger().info("Waiting for motion service...")

        req = MoveLine.Request()
        req.pos = [float(x), float(y), float(z), float(rx), float(ry), float(rz)]
        req.vel = [float(vel), float(vel)]
        req.acc = [float(acc), float(acc)]
        req.time = 0.0
        req.radius = float(radius)
        req.ref = 0
        req.mode = 0
        req.blend_type = 0
        req.sync_type = 0

        update_robot_status(
            x=round(x, 2),
            y=round(y, 2),
            z=round(z, 2)
        )

        self.last_commanded_pose = (float(x), float(y), float(z), float(rx), float(ry), float(rz))
        future = self.move_line_client.call_async(req)

        if wait_response:
            start_time = time.time()

            while rclpy.ok() and not future.done():
                if self.impact_stop_event.is_set() and self.current_order_id is not None and not self.cancel_recovery_active:
                    raise ImpactStopped(self.current_order_id)
                if allow_cancel and self.cancel_event.is_set() and not self.cancel_recovery_active and self.current_order_id is not None:
                    self.stop_robot_motion()
                    raise OrderCancelled(self.current_order_id)
                if response_timeout is not None and time.time() - start_time > response_timeout:
                    self.get_logger().warn("move_line service response timeout")
                    send_log("move_line service response timeout", "warn")
                    break

                time.sleep(0.01)

            if future.done():
                try:
                    result = future.result()
                    self.inspect_motion_result_for_stop(result, x, y, z)
                    if self.impact_stop_event.is_set() and self.current_order_id is not None and not self.cancel_recovery_active:
                        raise ImpactStopped(self.current_order_id)
                except ImpactStopped:
                    raise
                except Exception as e:
                    self.report_impact_stop(
                        f"move_line service exception near x={x:.3f}, y={y:.3f}, z={z:.3f}: {e}"
                    )
                    if self.impact_stop_event.is_set() and self.current_order_id is not None:
                        raise ImpactStopped(self.current_order_id)
                    raise

    # 설명: 주문 취소나 이상 상황에서 /dsr01/motion/move_stop 서비스를 호출해 즉시 정지를 요청합니다.
    def stop_robot_motion(self):
        if self.motion_stop_requested:
            return

        self.motion_stop_requested = True
        self.get_logger().warn("Immediate motion stop requested. Calling /dsr01/motion/move_stop")
        send_log("Immediate motion stop requested. Calling /dsr01/motion/move_stop", "warn")

        if not HAS_DSR_MSGS or not HAS_MOVE_STOP or self.move_stop_client is None:
            self.get_logger().warn("MoveStop service is not available. Only the software stop flag is active.")
            send_log("MoveStop service is not available. Only the software stop flag is active.", "warn")
            return

        try:
            if not self.move_stop_client.wait_for_service(timeout_sec=0.2):
                self.get_logger().warn("/dsr01/motion/move_stop service is not ready")
                return

            req = MoveStop.Request()
            if hasattr(req, "stop_mode"):
                req.stop_mode = 1
            elif hasattr(req, "mode"):
                req.mode = 1

            self.move_stop_client.call_async(req)
        except Exception as e:
            self.get_logger().error(f"MoveStop request failed: {e}")

    # 설명: 디지털 출력 1,2번 핀 조합으로 펜/케이스 그리퍼 폭을 제어합니다.
    def control_gripper(self, mode):
        if self.impact_stop_event.is_set() and self.current_order_id is not None and not self.cancel_recovery_active:
            raise ImpactStopped(self.current_order_id)
        if self.cancel_event.is_set() and not self.cancel_recovery_active and self.current_order_id is not None:
            self.stop_robot_motion()
            raise OrderCancelled(self.current_order_id)

        if not HAS_DSR_MSGS:
            self.get_logger().info(f"[SIM] control_gripper mode={mode}")
            return

        while not self.set_io_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info("Waiting for I/O service...")

        if mode == "PEN_CLOSE":
            p1, p2 = 1, 0
            desc = "Pen open (0mm)"
        elif mode == "PEN_OPEN":
            p1, p2 = 0, 0
            desc = "Pen close (30mm)"
        elif mode == "CASE_OPEN":
            p1, p2 = 0, 1
            desc = "Case open (105mm)"
        elif mode == "CASE_CLOSE":
            p1, p2 = 1, 1
            desc = "Case close (92mm)"
        else:
            self.get_logger().warn(f"Unknown gripper mode: {mode}")
            return

        req1 = SetCtrlBoxDigitalOutput.Request()
        req1.index = 1
        req1.value = p1
        self.set_io_client.call_async(req1)

        req2 = SetCtrlBoxDigitalOutput.Request()
        req2.index = 2
        req2.value = p2
        self.set_io_client.call_async(req2)

        self.get_logger().info(f"Gripper command: {desc} (Pin1:{p1}, Pin2:{p2})")
        self.interruptible_sleep(1.5)

    # 설명: ROBOT_MIN/MAX 좌표에 여백을 적용한 실제 드로잉 가능 영역을 반환합니다.
    def get_draw_area_bounds(self):
        min_x = ROBOT_MIN_X + DRAW_MARGIN
        max_x = ROBOT_MAX_X - DRAW_MARGIN
        min_y = ROBOT_MIN_Y + DRAW_MARGIN
        max_y = ROBOT_MAX_Y - DRAW_MARGIN
        return min_x, max_x, min_y, max_y

    # 설명: 로봇 좌표가 드로잉 가능 영역 안에 있는지 검사합니다.
    def is_safe_draw_point(self, x, y):
        min_x, max_x, min_y, max_y = self.get_draw_area_bounds()

        return min_x <= x <= max_x and min_y <= y <= max_y

    # 설명: 경로 중 안전 영역 밖으로 나간 구간을 잘라내고 안전한 구간만 남깁니다.
    def split_path_by_safe_area(self, path):
        safe_paths = []
        current_path = []

        for x, y in path:
            if self.is_safe_draw_point(x, y):
                current_path.append((x, y))
            else:
                if len(current_path) > 2:
                    safe_paths.append(current_path)
                current_path = []

        if len(current_path) > 2:
            safe_paths.append(current_path)

        return safe_paths

    # 설명: 4점 Z 보정값을 기준으로 현재 x,y 위치의 드로잉 Z 높이를 보간합니다.
    def get_draw_z(self, x, y):
        min_x, max_x, min_y, max_y = self.get_draw_area_bounds()

        tx = (x - min_x) / (max_x - min_x)
        ty = (y - min_y) / (max_y - min_y)

        tx = max(0.0, min(1.0, tx))
        ty = max(0.0, min(1.0, ty))

        z_bottom = Z_LB * (1.0 - tx) + Z_RB * tx
        z_top = Z_LT * (1.0 - tx) + Z_RT * tx

        return z_bottom * (1.0 - ty) + z_top * ty

    # 설명: 현재 위치에서 펜을 들어 올린 이동 높이를 계산합니다.
    def get_draw_hop_z(self, x, y):
        return self.get_draw_z(x, y) + DRAW_HOP_OFFSET

    # 설명: 두 점 사이의 2D 거리(mm)를 계산합니다.
    def distance(self, p1, p2):
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    # 설명: move_line을 비동기로 보낸 뒤 선분 길이/속도 기준으로 대기할 시간을 계산합니다.
    def calc_line_wait_time(self, p1, p2, vel):
        d = self.distance(p1, p2)
        t = d / max(float(vel), 1.0)

        if t < LINE_POINT_MIN_WAIT:
            return LINE_POINT_MIN_WAIT

        if t > LINE_POINT_MAX_WAIT:
            return LINE_POINT_MAX_WAIT

        return t

    # 설명: 경로 전체 길이를 계산합니다.
    def path_length(self, path):
        if len(path) < 2:
            return 0.0

        total = 0.0
        for i in range(1, len(path)):
            total += self.distance(path[i - 1], path[i])

        return total

    # 설명: 너무 짧아서 그릴 필요가 없는 경로를 제거합니다.
    def filter_short_paths(self, paths):
        filtered = []

        for path in paths:
            if len(path) < 2:
                continue

            if self.path_length(path) >= MIN_PATH_LENGTH_MM:
                filtered.append(path)

        return filtered

    # 설명: 두 경로가 이어질 수 있을 때 중복점을 피해서 합칩니다.
    def concat_paths(self, path_a, path_b):
        if not path_a:
            return path_b

        if not path_b:
            return path_a

        if self.distance(path_a[-1], path_b[0]) <= 0.05:
            return path_a + path_b[1:]

        return path_a + path_b

    # 설명: 두 경로 사이 간격이 연결 허용 범위인지 판단합니다.
    def can_connect_paths(self, prev_path, next_path, gap):
        if gap >= PATH_CONNECT_HARD_LIMIT_MM:
            return False

        return True

    # 설명: 지정한 점과 가장 가까운 경로상의 점 인덱스를 찾습니다.
    def nearest_point_index(self, point, path):
        best_idx = 0
        best_dist = float("inf")

        for i, p in enumerate(path):
            d = self.distance(point, p)
            if d < best_dist:
                best_dist = d
                best_idx = i

        return best_idx, best_dist

    # 설명: 가까운 중간점에서 후보 경로를 나누어 연결 가능한 부분을 선택합니다.
    def split_candidate_from_index(self, candidate, idx):
        if idx <= 0:
            return list(candidate), []

        if idx >= len(candidate) - 1:
            return list(reversed(candidate)), []

        forward = list(candidate[idx:])
        backward = list(reversed(candidate[:idx + 1]))

        if self.path_length(forward) >= self.path_length(backward):
            selected = forward
            leftover = backward
        else:
            selected = backward
            leftover = forward

        leftovers = []
        if len(leftover) >= 2 and self.path_length(leftover) >= MIN_PATH_LENGTH_MM:
            leftovers.append(leftover)

        return selected, leftovers

    # 설명: 가까운 경로들을 이어서 펜 들기 횟수를 줄입니다.
    def connect_close_paths(self, paths, max_gap=PATH_CONNECT_GAP_MM):
        remaining = [list(path) for path in paths if len(path) >= 2]
        connected_paths = []

        while remaining:
            current = remaining.pop(0)
            changed = True

            while changed:
                changed = False
                best_idx = None
                best_dist = float("inf")
                best_mode = None
                best_candidate = None
                best_leftovers = []

                for i, candidate_original in enumerate(remaining):
                    candidates = [
                        (
                            candidate_original,
                            "append",
                            self.distance(current[-1], candidate_original[0]),
                            [],
                        ),
                        (
                            list(reversed(candidate_original)),
                            "append",
                            self.distance(current[-1], candidate_original[-1]),
                            [],
                        ),
                        (
                            candidate_original,
                            "prepend",
                            self.distance(candidate_original[-1], current[0]),
                            [],
                        ),
                        (
                            list(reversed(candidate_original)),
                            "prepend",
                            self.distance(candidate_original[0], current[0]),
                            [],
                        ),
                    ]

                    end_idx, end_dist = self.nearest_point_index(current[-1], candidate_original)
                    if end_dist < PATH_CONNECT_ENDPOINT_TO_PATH_MM:
                        candidate_from_mid, leftovers = self.split_candidate_from_index(candidate_original, end_idx)
                        candidates.append((candidate_from_mid, "append", end_dist, leftovers))

                    start_idx, start_dist = self.nearest_point_index(current[0], candidate_original)
                    if start_dist < PATH_CONNECT_ENDPOINT_TO_PATH_MM:
                        candidate_from_mid, leftovers = self.split_candidate_from_index(candidate_original, start_idx)
                        candidates.append((list(reversed(candidate_from_mid)), "prepend", start_dist, leftovers))

                    for candidate, mode, dist_value, leftovers in candidates:
                        if dist_value >= max_gap:
                            continue

                        if mode == "append":
                            can_connect = self.can_connect_paths(current, candidate, dist_value)
                        else:
                            can_connect = self.can_connect_paths(candidate, current, dist_value)

                        if not can_connect:
                            continue

                        if dist_value < best_dist:
                            best_idx = i
                            best_dist = dist_value
                            best_mode = mode
                            best_candidate = candidate
                            best_leftovers = leftovers

                if best_idx is not None and best_candidate is not None:
                    remaining.pop(best_idx)

                    for leftover in best_leftovers:
                        if len(leftover) >= 2 and self.path_length(leftover) >= MIN_PATH_LENGTH_MM:
                            remaining.append(leftover)

                    if best_mode == "append":
                        current = self.concat_paths(current, best_candidate)
                    else:
                        current = self.concat_paths(best_candidate, current)

                    changed = True

            connected_paths.append(current)

        return connected_paths

    # 설명: 경로 연결 결과가 안정될 때까지 여러 번 연결 최적화를 반복합니다.
    def connect_paths_until_stable(self, paths, max_gap=PATH_CONNECT_GAP_MM, max_passes=5):
        result = [list(path) for path in paths if len(path) >= 2]

        for _ in range(max_passes):
            before_count = len(result)
            result = self.connect_close_paths(result, max_gap)
            after_count = len(result)

            if after_count >= before_count:
                break

        return result

    # 설명: 일정 간격(mm)으로 경로 점을 재샘플링해 로봇 이동을 부드럽게 합니다.
    def resample_path(self, path, step_mm=SPLINE_RESAMPLE_STEP_MM):
        if len(path) < 2:
            return path

        resampled = [path[0]]
        carry = 0.0
        prev = path[0]

        for i in range(1, len(path)):
            curr = path[i]
            seg_len = self.distance(prev, curr)

            if seg_len <= 1e-6:
                prev = curr
                continue

            direction_x = (curr[0] - prev[0]) / seg_len
            direction_y = (curr[1] - prev[1]) / seg_len
            dist_along = step_mm - carry

            while dist_along <= seg_len:
                new_point = (
                    prev[0] + direction_x * dist_along,
                    prev[1] + direction_y * dist_along,
                )
                resampled.append(new_point)
                dist_along += step_mm

            carry = seg_len - (dist_along - step_mm)
            prev = curr

        if self.distance(resampled[-1], path[-1]) > 0.05:
            resampled.append(path[-1])

        return resampled

    # 설명: 재샘플링된 경로 점들을 move_line으로 순서대로 그립니다.
    def draw_path_with_moveline(self, path, order_id=None):
        prev_point = path[0]

        for i, (rx, ry) in enumerate(path):
            if order_id is not None:
                self.ensure_not_cancelled(order_id)

            z = self.get_draw_z(rx, ry)

            if i < len(path) - 1:
                r = LINE_BLEND_RADIUS
            else:
                r = 0.0

            if len(path) < 10:
                r = 0.0

            self.last_draw_point = (rx, ry)

            self.move_to_pos(
                rx,
                ry,
                z,
                vel=DRAW_LINE_VEL,
                acc=DRAW_LINE_ACC,
                radius=r,
                wait_response=False,
            )

            wait_time = self.calc_line_wait_time(prev_point, (rx, ry), DRAW_LINE_VEL)

            if order_id is not None:
                self.interruptible_sleep(wait_time)
            else:
                time.sleep(wait_time)

            prev_point = (rx, ry)

    # 설명: 경로를 다시 샘플링한 뒤 실제 드로잉을 수행합니다.
    def draw_path_smooth(self, path, order_id=None):
        if order_id is not None:
            self.ensure_not_cancelled(order_id)

        resampled_path = self.resample_path(path, SPLINE_RESAMPLE_STEP_MM)
        self.draw_path_with_moveline(resampled_path, order_id=order_id)

    # 설명: 빈 케이스를 집는 단계입니다.
    def handle_case_pickup(self):
        self.get_logger().info("Picking up blank case. (CASE_PICKUP)")

        self.move_to_pos(
            CASE_PICK_SAFE_X,
            CASE_PICK_SAFE_Y,
            CASE_PICK_SAFE_Z,
            CASE_PICK_SAFE_RX,
            CASE_PICK_SAFE_RY,
            CASE_PICK_SAFE_RZ,
            vel=CASE_TRAVEL_VEL,
            acc=CASE_TRAVEL_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("CASE_OPEN")

        self.move_to_pos(
            CASE_PICK_X,
            CASE_PICK_Y,
            CASE_PICK_Z,
            CASE_PICK_RX,
            CASE_PICK_RY,
            CASE_PICK_RZ,
            vel=CASE_PICK_DESCEND_VEL,
            acc=CASE_PICK_DESCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("CASE_CLOSE")

        self.move_to_pos(
            CASE_PICK_SAFE_X,
            CASE_PICK_SAFE_Y,
            CASE_PICK_SAFE_Z,
            CASE_PICK_SAFE_RX,
            CASE_PICK_SAFE_RY,
            CASE_PICK_SAFE_RZ,
            vel=CASE_LOADED_ASCEND_VEL,
            acc=CASE_LOADED_ASCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)
        self.case_in_gripper = True
        self.case_on_work_area = False

    # 설명: 집은 빈 케이스를 작업대 드로잉 위치에 내려놓는 단계입니다.
    def handle_case_place(self):
        self.get_logger().info("Placing case on work area. (CASE_PLACE)")

        self.move_to_pos(
            CASE_PLACE_SAFE_X,
            CASE_PLACE_SAFE_Y,
            CASE_PLACE_SAFE_Z,
            CASE_PLACE_SAFE_RX,
            CASE_PLACE_SAFE_RY,
            CASE_PLACE_SAFE_RZ,
            vel=CASE_TRAVEL_VEL,
            acc=CASE_TRAVEL_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.move_to_pos(
            CASE_PLACE_X,
            CASE_PLACE_Y,
            CASE_PLACE_Z,
            CASE_PLACE_RX,
            CASE_PLACE_RY,
            CASE_PLACE_RZ,
            vel=CASE_PLACE_DESCEND_VEL,
            acc=CASE_PLACE_DESCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("CASE_OPEN")

        self.move_to_pos(
            CASE_PLACE_SAFE_X,
            CASE_PLACE_SAFE_Y,
            CASE_PLACE_SAFE_Z,
            CASE_PLACE_SAFE_RX,
            CASE_PLACE_SAFE_RY,
            CASE_PLACE_SAFE_RZ,
            vel=CASE_EMPTY_ASCEND_VEL,
            acc=CASE_EMPTY_ASCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)
        self.case_in_gripper = False
        self.case_on_work_area = True

    # 설명: 드로잉이 끝난 케이스를 작업대에서 다시 집는 단계입니다.
    def handle_finished_case_pickup(self):
        self.get_logger().info("Picking up finished case from work area. (FINISHED_CASE_PICKUP)")

        self.move_to_pos(
            CASE_PLACE_SAFE_X,
            CASE_PLACE_SAFE_Y,
            CASE_PLACE_SAFE_Z,
            CASE_PLACE_SAFE_RX,
            CASE_PLACE_SAFE_RY,
            CASE_PLACE_SAFE_RZ,
            vel=CASE_TRAVEL_VEL,
            acc=CASE_TRAVEL_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("CASE_OPEN")

        self.move_to_pos(
            CASE_PLACE_X,
            CASE_PLACE_Y,
            CASE_PLACE_Z,
            CASE_PLACE_RX,
            CASE_PLACE_RY,
            CASE_PLACE_RZ,
            vel=CASE_PLACE_DESCEND_VEL,
            acc=CASE_PLACE_DESCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("CASE_CLOSE")

        self.move_to_pos(
            CASE_PLACE_SAFE_X,
            CASE_PLACE_SAFE_Y,
            CASE_PLACE_SAFE_Z,
            CASE_PLACE_SAFE_RX,
            CASE_PLACE_SAFE_RY,
            CASE_PLACE_SAFE_RZ,
            vel=CASE_LOADED_ASCEND_VEL,
            acc=CASE_LOADED_ASCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)
        self.case_in_gripper = True
        self.case_on_work_area = False


    # 설명: 취소 복구 중 작업대에 남은 케이스를 다시 집는 단계입니다.
    def handle_cancel_case_pickup(self):
        self.get_logger().info("Picking up case from work area for cancel recovery. (CANCEL_CASE_PICKUP)")

        self.move_to_pos(
            CASE_PLACE_SAFE_X,
            CASE_PLACE_SAFE_Y,
            CASE_PLACE_SAFE_Z,
            CASE_PLACE_SAFE_RX,
            CASE_PLACE_SAFE_RY,
            CASE_PLACE_SAFE_RZ,
            vel=CASE_TRAVEL_VEL,
            acc=CASE_TRAVEL_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("CASE_OPEN")

        self.move_to_pos(
            CASE_PLACE_X,
            CASE_PLACE_Y,
            CASE_PLACE_Z,
            CASE_PLACE_RX,
            CASE_PLACE_RY,
            CASE_PLACE_RZ,
            vel=CASE_PLACE_DESCEND_VEL,
            acc=CASE_PLACE_DESCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("CASE_CLOSE")

        self.move_to_pos(
            CASE_PLACE_SAFE_X,
            CASE_PLACE_SAFE_Y,
            CASE_PLACE_SAFE_Z,
            CASE_PLACE_SAFE_RX,
            CASE_PLACE_SAFE_RY,
            CASE_PLACE_SAFE_RZ,
            vel=CASE_LOADED_ASCEND_VEL,
            acc=CASE_LOADED_ASCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)
        self.case_in_gripper = True
        self.case_on_work_area = False

    # 설명: 완성된 케이스를 배출 위치에 내려놓는 단계입니다.
    def handle_finished_case_drop(self):
        self.get_logger().info("Dropping finished case to output area. (CASE_DROP)")

        self.move_to_pos(
            CASE_DROP_SAFE_X,
            CASE_DROP_SAFE_Y,
            CASE_DROP_SAFE_Z,
            CASE_DROP_SAFE_RX,
            CASE_DROP_SAFE_RY,
            CASE_DROP_SAFE_RZ,
            vel=CASE_TRAVEL_VEL,
            acc=CASE_TRAVEL_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.move_to_pos(
            CASE_DROP_X,
            CASE_DROP_Y,
            CASE_DROP_Z,
            CASE_DROP_RX,
            CASE_DROP_RY,
            CASE_DROP_RZ,
            vel=CASE_DROP_DESCEND_VEL,
            acc=CASE_DROP_DESCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("CASE_OPEN")

        self.move_to_pos(
            CASE_DROP_SAFE_X,
            CASE_DROP_SAFE_Y,
            CASE_DROP_SAFE_Z,
            CASE_DROP_SAFE_RX,
            CASE_DROP_SAFE_RY,
            CASE_DROP_SAFE_RZ,
            vel=CASE_EMPTY_ASCEND_VEL,
            acc=CASE_EMPTY_ASCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)
        self.case_in_gripper = False
        self.case_on_work_area = False

    # 설명: 색상별 펜 거치대 좌표를 반환합니다.
    def get_pen_pose(self, color):
        if color == "RED":
            return (
                STAND_PICK_RED_X,
                STAND_PICK_RED_Y,
                STAND_PICK_RED_Z,
                STAND_PICK_RED_RX,
                STAND_PICK_RED_RY,
                STAND_PICK_RED_RZ,
            )

        if color == "BLUE":
            return (
                STAND_PICK_BLUE_X,
                STAND_PICK_BLUE_Y,
                STAND_PICK_BLUE_Z,
                STAND_PICK_BLUE_RX,
                STAND_PICK_BLUE_RY,
                STAND_PICK_BLUE_RZ,
            )

        return (
            STAND_PICK_BLACK_X,
            STAND_PICK_BLACK_Y,
            STAND_PICK_BLACK_Z,
            STAND_PICK_BLACK_RX,
            STAND_PICK_BLACK_RY,
            STAND_PICK_BLACK_RZ,
        )

    # 설명: 지정 색상 펜을 집는 단계입니다.
    def pickup_pen(self, color="BLACK"):
        self.get_logger().info(f"Picking up {color} pen. (PICKUP_PEN)")

        update_robot_status(
            state="DRAWING",
            stage="PICKUP_PEN",
            pen=color
        )

        px, py, pz, prx, pry, prz = self.get_pen_pose(color)

        self.move_to_pos(
            px,
            py,
            SAFE_Z,
            prx,
            pry,
            prz,
            vel=PEN_TRAVEL_VEL,
            acc=PEN_TRAVEL_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("PEN_OPEN")

        self.get_logger().info("[Pickup] Descending toward pen holder")
        self.move_to_pos(
            px,
            py,
            pz,
            prx,
            pry,
            prz,
            vel=PEN_PICK_DESCEND_VEL,
            acc=PEN_PICK_DESCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("PEN_CLOSE")

        self.get_logger().info("[Pickup] Grabbing pen and lifting")
        self.move_to_pos(
            px,
            py,
            SAFE_Z,
            prx,
            pry,
            prz,
            vel=PEN_LOADED_ASCEND_VEL,
            acc=PEN_LOADED_ASCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)
        self.pen_in_gripper = True
        self.active_pen_color = color

    # 설명: 사용한 펜을 원래 거치대에 반납하는 단계입니다.
    def place_pen(self, color="BLACK"):
        self.get_logger().info(f"Returning {color} pen to its slot. (PLACE_PEN)")

        px, py, pz, prx, pry, prz = self.get_pen_pose(color)

        self.move_to_pos(
            px,
            py,
            SAFE_Z,
            prx,
            pry,
            prz,
            vel=PEN_TRAVEL_VEL,
            acc=PEN_TRAVEL_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.get_logger().info("[Return] Lowering pen into holder")
        self.move_to_pos(
            px,
            py,
            pz,
            prx,
            pry,
            prz,
            vel=PEN_INSERT_DESCEND_VEL,
            acc=PEN_INSERT_DESCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)

        self.control_gripper("PEN_OPEN")

        self.get_logger().info("[Return] Releasing pen and lifting empty gripper")
        self.move_to_pos(
            px,
            py,
            SAFE_Z,
            prx,
            pry,
            prz,
            vel=PEN_EMPTY_ASCEND_VEL,
            acc=PEN_EMPTY_ASCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)
        self.pen_in_gripper = False
        self.pen_is_down = False
        self.active_pen_color = None

    # 설명: 노드 시작 시 로봇을 HOME 위치로 이동시켜 초기 자세를 맞춥니다.
    def move_home_on_startup(self):
        self.get_logger().info("Moving to HOME for initial position setup")
        update_robot_status(
            state="INITIALIZING",
            stage="HOME",
            pen="NONE"
        )

        self.move_to_pos(
            ROBOT_HOME_X,
            ROBOT_HOME_Y,
            SAFE_Z,
            vel=HOME_RETURN_VEL,
            acc=HOME_RETURN_ACC,
            radius=0.0,
            wait_response=True,
        )
        self.interruptible_sleep(1.0)
        self.get_logger().info("Initial HOME move complete. Starting order monitoring after 1 second.")

    # 설명: DB의 orders.status가 cancel_requested인지 확인합니다.
    def is_cancel_requested(self, order_id):

        try:

            self.cursor.execute(
                "SELECT status FROM orders WHERE id=?",
                (order_id,)
            )

            row = self.cursor.fetchone()

            if not row:
                return False

            return row[0] == "cancel_requested"

        except Exception as e:

            self.get_logger().error(
                f"Failed to check the cancel status: {e}"
            )

            return False

    # 설명: 작업 중 별도 스레드로 취소 요청을 빠르게 감시합니다.
    def monitor_cancel_request(self, order_id):
        while self.is_drawing and self.current_order_id == order_id and not self.cancel_event.is_set() and not self.impact_stop_event.is_set():
            if self.is_cancel_requested(order_id):
                self.get_logger().warn(f"Async cancel signal received for order {order_id}")
                self.cancel_event.set()
                self.stop_robot_motion()
                return
            time.sleep(0.05)

    # 설명: sleep 중에도 취소/외력 이벤트를 감지할 수 있게 짧게 나누어 대기합니다.
    def interruptible_sleep(self, duration, allow_cancel=True):
        end_time = time.time() + duration
        while time.time() < end_time:
            if self.impact_stop_event.is_set() and self.current_order_id is not None and not self.cancel_recovery_active:
                raise ImpactStopped(self.current_order_id)
            if allow_cancel and self.cancel_event.is_set() and self.current_order_id is not None and not self.cancel_recovery_active:
                self.stop_robot_motion()
                raise OrderCancelled(self.current_order_id)
            time.sleep(min(0.02, max(0.0, end_time - time.time())))

    # 설명: 현재 주문이 취소되었거나 외력 정지 상태인지 검사하고 예외를 발생시킵니다.
    def ensure_not_cancelled(self, order_id):
        if self.impact_stop_event.is_set():
            raise ImpactStopped(order_id)

        if self.cancel_event.is_set() or self.is_cancel_requested(order_id):
            self.cancel_event.set()
            self.stop_robot_motion()
            self.get_logger().warn(f"Order {order_id} cancel requested")
            raise OrderCancelled(order_id)

    # 설명: 주문을 cancelled 상태로 저장하고 관리자 UI 상태를 갱신합니다.
    def mark_order_cancelled(self, order_id):
        try:
            self.cursor.execute(
                "UPDATE orders SET status='cancelled', estimated_time=0 WHERE id=?",
                (order_id,)
            )
            self.conn.commit()
        except Exception as e:
            self.get_logger().error(f"Cancel status save failed: {e}")

        try:
            requests.patch(
                f"http://127.0.0.1:5000/api/orders/{order_id}/status",
                json={"status": "cancelled"},
                timeout=2,
            )
        except Exception:
            pass

        update_robot_status(
            state="IDLE",
            stage="CANCELLED",
            pen="NONE",
            currentPath=0,
            totalPath=0,
        )
        self.get_logger().warn(f"Order {order_id} cancelled")

    # 설명: 주문을 impact_stopped 상태로 저장하고 관리자 UI에 안전정지 상태를 표시합니다.
    def mark_order_impact_stopped(self, order_id):
        try:
            self.cursor.execute(
                "UPDATE orders SET status='impact_stopped', estimated_time=0 WHERE id=?",
                (order_id,),
            )
            self.conn.commit()
        except Exception as e:
            self.get_logger().error(f"Failed to save impact stop status: {e}")

        try:
            requests.patch(
                f"http://127.0.0.1:5000/api/orders/{order_id}/status",
                json={"status": "impact_stopped"},
                timeout=2,
            )
        except Exception:
            pass

        update_robot_status(
            state="STOPPED",
            stage="IMPACT_STOP",
            orderId=order_id,
            stopReason=(self.impact_stop_reason or "External impact or protective stop detected")[:160],
            pen="NONE",
            currentPath=0,
            totalPath=0,
        )
        self.get_logger().error(f"Order {order_id} stopped by external impact or protective stop")

    # 설명: 취소 복구 시 케이스를 공급 위치로 되돌립니다.
    def return_case_to_supply(self):
        self.move_to_pos(
            CASE_PICK_SAFE_X,
            CASE_PICK_SAFE_Y,
            CASE_PICK_SAFE_Z,
            CASE_PICK_SAFE_RX,
            CASE_PICK_SAFE_RY,
            CASE_PICK_SAFE_RZ,
            vel=CASE_TRAVEL_VEL,
            acc=CASE_TRAVEL_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(0.5)
        self.move_to_pos(
            CASE_PICK_X,
            CASE_PICK_Y,
            CASE_PICK_Z,
            CASE_PICK_RX,
            CASE_PICK_RY,
            CASE_PICK_RZ,
            vel=CASE_PICK_DESCEND_VEL,
            acc=CASE_PICK_DESCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(0.5)
        self.control_gripper("CASE_OPEN")
        self.move_to_pos(
            CASE_PICK_SAFE_X,
            CASE_PICK_SAFE_Y,
            CASE_PICK_SAFE_Z,
            CASE_PICK_SAFE_RX,
            CASE_PICK_SAFE_RY,
            CASE_PICK_SAFE_RZ,
            vel=CASE_EMPTY_ASCEND_VEL,
            acc=CASE_EMPTY_ASCEND_ACC,
            wait_response=True,
        )
        self.interruptible_sleep(0.5)
        self.case_in_gripper = False
        self.case_on_work_area = False

    # 설명: 작업 취소 후 펜/케이스/로봇 위치를 가능한 안전한 상태로 복구합니다.
    def recover_from_cancel(self, order_id):
        self.get_logger().warn(f"Cancel recovery start for order {order_id}")
        self.cancel_recovery_active = True
        update_robot_status(
            state="IDLE",
            stage="CANCEL_RECOVERY",
            pen=self.active_pen_color or "NONE",
            currentPath=0,
            totalPath=0,
        )

        if self.pen_is_down and self.last_draw_point is not None:
            try:
                lx, ly = self.last_draw_point
                self.move_to_pos(
                    lx,
                    ly,
                    self.get_draw_hop_z(lx, ly),
                    vel=DRAW_LIFT_VEL,
                    acc=DRAW_LIFT_ACC,
                    radius=0.0,
                    wait_response=True,
                )
                self.interruptible_sleep(0.2)
            except Exception as e:
                self.get_logger().error(f"Cancel lift failed: {e}")
            self.pen_is_down = False

        if self.pen_in_gripper and self.active_pen_color:
            try:
                self.place_pen(self.active_pen_color)
            except Exception as e:
                self.get_logger().error(f"Cancel pen return failed: {e}")

        if self.case_on_work_area and not self.case_in_gripper:
            try:
                self.handle_cancel_case_pickup()
            except Exception as e:
                self.get_logger().error(f"Cancel case pickup failed: {e}")

        if self.case_in_gripper:
            try:
                self.return_case_to_supply()
            except Exception as e:
                self.get_logger().error(f"Cancel case return failed: {e}")

        try:
            self.move_to_pos(
                ROBOT_HOME_X,
                ROBOT_HOME_Y,
                SAFE_Z,
                vel=HOME_RETURN_VEL,
                acc=HOME_RETURN_ACC,
                radius=0.0,
                wait_response=True,
            )
            self.interruptible_sleep(0.5)
        except Exception as e:
            self.get_logger().error(f"Cancel home return failed: {e}")

        self.pen_is_down = False
        self.last_draw_point = None
        self.cancel_recovery_active = False

    # 설명: 1초마다 DB에서 waiting 주문을 찾고, 새 주문이 있으면 작업 스레드를 시작합니다.
    def poll_database(self):
        if self.is_drawing:
            return

        self.cursor.execute(
            "SELECT id FROM orders WHERE status='cancel_requested' ORDER BY created_at ASC LIMIT 1"
        )
        cancel_row = self.cursor.fetchone()
        if cancel_row:
            self.mark_order_cancelled(cancel_row[0])
            return

        self.cursor.execute(
            "SELECT id, image_path FROM orders WHERE status='waiting' ORDER BY created_at ASC LIMIT 1"
        )
        row = self.cursor.fetchone()

        if not row:
            return

        order_id, filename = row
        image_path = os.path.join(UPLOAD_FOLDER, filename)

        self.get_logger().info(f"New order detected. ID={order_id}")

        update_robot_status(
            state="DRAWING",
            stage="ORDER_RECEIVED",
            orderId=order_id
        )

        self.cursor.execute(
            "UPDATE orders SET status='processing' WHERE id=?",
            (order_id,),
        )
        self.conn.commit()

        try:
            requests.patch(
                f"http://127.0.0.1:5000/api/orders/{order_id}/status",
                json={"status": "processing"},
                timeout=2,
            )
        except Exception:
            pass

        self.is_drawing = True
        self.current_order_id = order_id
        self.cancel_event.clear()
        self.impact_stop_event.clear()
        self.impact_stop_reason = None
        self.motion_stop_requested = False
        self.cancel_recovery_active = False
        self.last_impact_signature = None

        self.cancel_monitor_thread = threading.Thread(
            target=self.monitor_cancel_request,
            args=(order_id,),
            daemon=True,
        )
        self.cancel_monitor_thread.start()

        threading.Thread(
            target=self.process_and_draw,
            args=(order_id, image_path),
            daemon=True,
        ).start()

    # 설명: 너무 촘촘한 점을 줄여 경로를 간단하게 만듭니다.
    def simplify_and_smooth_path(self, path, min_dist=CURVE_MIN_DIST_MM):
        if not path:
            return []

        simplified = [path[0]]

        for p in path[1:]:
            if self.distance(p, simplified[-1]) >= min_dist:
                simplified.append(p)

        if simplified[-1] != path[-1] and self.distance(simplified[-1], path[-1]) > 0.05:
            simplified.append(path[-1])

        return simplified

    # 설명: 이미지 mask의 작은 노이즈를 제거하고 연결 요소를 정리합니다.
    def clean_binary_mask(self, mask):
        binary = np.where(mask > 0, 255, 0).astype(np.uint8)

        kernel = np.ones((3, 3), np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)

        cleaned = np.zeros_like(binary)

        for label in range(1, num_labels):
            area = stats[label, cv2.CC_STAT_AREA]

            if area >= MIN_MASK_COMPONENT_AREA:
                cleaned[labels == label] = 255

        return cleaned

    # 설명: HSV 이미지에서 RED/BLUE/BLACK 색상 mask를 생성합니다.
    def create_color_masks(self, hsv):
        mask_red1 = cv2.inRange(
            hsv,
            np.array([0, 50, 50]),
            np.array([10, 255, 255]),
        )

        mask_red2 = cv2.inRange(
            hsv,
            np.array([170, 50, 50]),
            np.array([180, 255, 255]),
        )

        mask_red = cv2.bitwise_or(mask_red1, mask_red2)

        mask_blue = cv2.inRange(
            hsv,
            np.array([100, 50, 50]),
            np.array([140, 255, 255]),
        )

        mask_black = cv2.inRange(
            hsv,
            np.array([0, 0, 0]),
            np.array([180, 255, 100]),
        )

        mask_black = cv2.bitwise_and(mask_black, cv2.bitwise_not(mask_red))
        mask_black = cv2.bitwise_and(mask_black, cv2.bitwise_not(mask_blue))

        return {
            "RED": mask_red,
            "BLUE": mask_blue,
            "BLACK": mask_black,
        }

    # 설명: 로봇 좌표를 디버그 이미지 표시용 픽셀 좌표로 변환합니다.
    def robot_to_pixel(self, x, y, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        px = int(round((x - draw_min_x - x_offset) / scale))
        py = int(round((draw_max_y - y_offset - y) / scale))
        return px, py

    # 설명: mask, centerline, 최종 경로 overlay 이미지를 debug_draw 폴더에 저장합니다.
    def save_draw_debug_images(self, image_path, color_name, mask, centerline_preview, paths, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        if not SAVE_DRAW_DEBUG_IMAGES:
            return

        try:
            os.makedirs(DEBUG_OUTPUT_FOLDER, exist_ok=True)

            base = os.path.splitext(os.path.basename(image_path))[0]
            safe_color = color_name.lower()

            cv2.imwrite(
                os.path.join(DEBUG_OUTPUT_FOLDER, f"{base}_{safe_color}_01_mask.png"),
                mask,
            )
            cv2.imwrite(
                os.path.join(DEBUG_OUTPUT_FOLDER, f"{base}_{safe_color}_02_centerline.png"),
                centerline_preview,
            )

            original = cv2.imread(image_path, cv2.IMREAD_COLOR)
            if original is None:
                original = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)

            overlay = original.copy()

            for path_idx, path in enumerate(paths):
                if len(path) < 2:
                    continue

                color_value = (
                    int((37 * path_idx) % 255),
                    int((91 * path_idx) % 255),
                    int((173 * path_idx) % 255),
                )

                pts = []
                for x, y in path:
                    px, py = self.robot_to_pixel(
                        x,
                        y,
                        scale,
                        x_offset,
                        y_offset,
                        draw_min_x,
                        draw_max_y,
                    )
                    pts.append((px, py))

                for i in range(1, len(pts)):
                    cv2.line(overlay, pts[i - 1], pts[i], color_value, 1, cv2.LINE_AA)

                if pts:
                    cv2.circle(overlay, pts[0], 2, (0, 255, 0), -1)
                    cv2.circle(overlay, pts[-1], 2, (0, 0, 255), -1)

            cv2.imwrite(
                os.path.join(DEBUG_OUTPUT_FOLDER, f"{base}_{safe_color}_03_final_paths.png"),
                overlay,
            )

        except Exception as e:
            self.get_logger().warn(f"Failed to save debug image: {e}")

    # 설명: 웹에서 넘어온 색상 문자열/hex 값을 RED/BLUE/BLACK 중 하나로 정규화합니다.
    def normalize_color_name(self, color):
        if color is None:
            return "BLACK"

        c = str(color).strip().upper()

        if c in ["R", "RED", "#FF0000", "RGB(255,0,0)", "255,0,0"]:
            return "RED"

        if c in ["B", "BLUE", "#0000FF", "RGB(0,0,255)", "0,0,255"]:
            return "BLUE"

        if c in ["K", "BLACK", "#000000", "RGB(0,0,0)", "0,0,0"]:
            return "BLACK"

        if c.startswith("#"):
            try:
                hex_value = c.lstrip("#")
                if len(hex_value) == 6:
                    r = int(hex_value[0:2], 16)
                    g = int(hex_value[2:4], 16)
                    b = int(hex_value[4:6], 16)

                    if r > b and r > g:
                        return "RED"

                    if b > r and b > g:
                        return "BLUE"

                    return "BLACK"
            except Exception:
                return "BLACK"

        if "RED" in c or "빨" in c:
            return "RED"

        if "BLUE" in c or "파" in c:
            return "BLUE"

        return "BLACK"

    # 설명: 주문 이미지와 같은 이름의 stroke JSON 후보 파일을 찾습니다.
    def find_stroke_json_path(self, image_path):
        image_path_obj = Path(image_path)
        candidates = []

        for suffix in STROKE_JSON_CANDIDATE_SUFFIXES:
            if suffix.startswith("."):
                candidates.append(image_path_obj.with_suffix(suffix))
            else:
                candidates.append(image_path_obj.with_name(image_path_obj.stem + suffix))

        candidates.append(image_path_obj.with_name(image_path_obj.stem + "_strokes.json"))
        candidates.append(Path(UPLOAD_FOLDER) / "strokes" / f"{image_path_obj.stem}.json")

        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

        return None

    # 설명: stroke JSON 파일을 읽고 strokes/paths/data 배열을 반환합니다.
    def read_stroke_json(self, json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.get_logger().warn(f"Failed to read stroke JSON: {e}")
            return None

        if isinstance(data, dict):
            if "strokes" in data and isinstance(data["strokes"], list):
                return data["strokes"]

            if "paths" in data and isinstance(data["paths"], list):
                return data["paths"]

            if "data" in data and isinstance(data["data"], list):
                return data["data"]

        if isinstance(data, list):
            return data

        return None

    # 설명: stroke 점 하나를 x,y 숫자 좌표로 파싱합니다.
    def parse_stroke_point(self, point):
        if isinstance(point, dict):
            if "x" in point and "y" in point:
                return float(point["x"]), float(point["y"])

            if "px" in point and "py" in point:
                return float(point["px"]), float(point["py"])

        if isinstance(point, (list, tuple)) and len(point) >= 2:
            return float(point[0]), float(point[1])

        return None

    # 설명: 캔버스 픽셀 좌표를 로봇 드로잉 좌표로 변환합니다.
    def canvas_point_to_robot(self, px, py, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        rx = draw_min_x + x_offset + (px * scale)
        ry = draw_max_y - y_offset - (py * scale)
        return rx, ry

    # 설명: Catmull-Rom spline으로 경로를 부드럽게 보간합니다.
    def catmull_rom_smooth_path(self, path, samples_per_segment=CATMULL_ROM_SAMPLES_PER_SEGMENT):
        if len(path) < 4:
            return list(path)

        result = []

        points = [path[0]] + list(path) + [path[-1]]

        for i in range(1, len(points) - 2):
            p0 = points[i - 1]
            p1 = points[i]
            p2 = points[i + 1]
            p3 = points[i + 2]

            for j in range(samples_per_segment):
                t = j / float(samples_per_segment)
                t2 = t * t
                t3 = t2 * t

                x = 0.5 * (
                    (2.0 * p1[0])
                    + (-p0[0] + p2[0]) * t
                    + (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * t2
                    + (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * t3
                )

                y = 0.5 * (
                    (2.0 * p1[1])
                    + (-p0[1] + p2[1]) * t
                    + (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * t2
                    + (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * t3
                )

                result.append((x, y))

        result.append(path[-1])
        return result

    # 설명: 현재 위치에서 가까운 path부터 그리도록 경로 순서를 최적화합니다.
    def optimize_paths_order(self, paths):
        paths = [list(path) for path in paths if len(path) >= 2]
        optimized = []
        current = (ROBOT_HOME_X, ROBOT_HOME_Y)

        while paths:
            best_idx = -1
            best_dist = float("inf")
            reverse = False

            for i, p in enumerate(paths):
                d_start = self.distance(current, p[0])
                d_end = self.distance(current, p[-1])

                if d_start < best_dist:
                    best_idx = i
                    best_dist = d_start
                    reverse = False

                if d_end < best_dist:
                    best_idx = i
                    best_dist = d_end
                    reverse = True

            best = paths.pop(best_idx)

            if reverse:
                best.reverse()

            optimized.append(best)
            current = best[-1]

        return optimized

    # 설명: 이미지 크기와 로봇 드로잉 영역의 비율을 맞추는 scale/offset을 계산합니다.
    def build_draw_area_transform(self, img_w, img_h):
        draw_min_x, draw_max_x, draw_min_y, draw_max_y = self.get_draw_area_bounds()
        draw_w = draw_max_x - draw_min_x
        draw_h = draw_max_y - draw_min_y

        img_aspect = img_w / float(img_h)
        draw_aspect = draw_w / draw_h

        if img_aspect > draw_aspect:
            scale = draw_w / float(img_w)
            x_offset = 0.0
            y_offset = (draw_h - (img_h * scale)) / 2.0
        else:
            scale = draw_h / float(img_h)
            x_offset = (draw_w - (img_w * scale)) / 2.0
            y_offset = 0.0

        return scale, x_offset, y_offset, draw_min_x, draw_max_y

    # 설명: stroke JSON을 읽어 색상별 로봇 경로로 변환합니다.
    def extract_strokes_from_json(self, image_path, img_w, img_h, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        json_path = self.find_stroke_json_path(image_path)

        if json_path is None:
            return None

        strokes = self.read_stroke_json(json_path)

        if not strokes:
            self.get_logger().warn(f"Stroke JSON was found but did not contain usable stroke data: {json_path}")
            return None

        color_paths = {"RED": [], "BLUE": [], "BLACK": []}

        for stroke in strokes:
            if isinstance(stroke, dict):
                raw_points = stroke.get("points") or stroke.get("path") or stroke.get("coords") or []
                color_name = self.normalize_color_name(
                    stroke.get("color")
                    or stroke.get("strokeStyle")
                    or stroke.get("pen")
                    or stroke.get("name")
                )
            else:
                raw_points = stroke
                color_name = "BLACK"

            robot_path = []

            for raw_point in raw_points:
                parsed = self.parse_stroke_point(raw_point)

                if parsed is None:
                    continue

                px, py = parsed
                rx, ry = self.canvas_point_to_robot(
                    px,
                    py,
                    scale,
                    x_offset,
                    y_offset,
                    draw_min_x,
                    draw_max_y,
                )

                robot_path.append((rx, ry))

            if len(robot_path) < 2:
                continue

            safe_paths = self.split_path_by_safe_area(robot_path)

            for safe_path in safe_paths:
                if len(safe_path) < 2:
                    continue

                smoothed = self.catmull_rom_smooth_path(safe_path)
                resampled = self.resample_path(smoothed, SPLINE_RESAMPLE_STEP_MM)

                if len(resampled) >= 2 and self.path_length(resampled) >= MIN_PATH_LENGTH_MM:
                    color_paths[color_name].append(resampled)

        self.get_logger().info(f"Using canvas stroke JSON: {json_path}")
        send_log(f"Using canvas stroke JSON: {json_path}")

        for color_name in ["RED", "BLUE", "BLACK"]:
            self.get_logger().info(f"{color_name} stroke JSON paths: {len(color_paths[color_name])}")
            send_log(f"{color_name} stroke JSON paths: {len(color_paths[color_name])}")

        return color_paths

    # 설명: OpenCV contour를 일반 좌표 리스트로 변환합니다.
    def contour_to_points(self, contour):
        if contour is None or len(contour) < 2:
            return []

        return [(float(p[0]), float(p[1])) for p in contour.reshape(-1, 2)]

    # 설명: 픽셀 경로의 길이를 계산합니다.
    def pixel_polyline_length(self, points):
        if len(points) < 2:
            return 0.0

        total = 0.0
        for i in range(1, len(points)):
            total += math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])

        return total

    # 설명: 픽셀 경로를 지정한 점 개수로 균등 재샘플링합니다.
    def resample_pixel_polyline_by_count(self, points, count):
        if len(points) < 2:
            return list(points)

        count = int(max(2, count))
        lengths = [0.0]

        for i in range(1, len(points)):
            lengths.append(
                lengths[-1]
                + math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
            )

        total_len = lengths[-1]

        if total_len <= 1e-6:
            return [points[0] for _ in range(count)]

        result = []
        seg_idx = 1

        for k in range(count):
            target = total_len * k / float(count - 1)

            while seg_idx < len(lengths) - 1 and lengths[seg_idx] < target:
                seg_idx += 1

            prev_len = lengths[seg_idx - 1]
            next_len = lengths[seg_idx]
            p0 = points[seg_idx - 1]
            p1 = points[seg_idx]

            if next_len - prev_len <= 1e-6:
                result.append(p1)
                continue

            t = (target - prev_len) / (next_len - prev_len)
            result.append(
                (
                    p0[0] + (p1[0] - p0[0]) * t,
                    p0[1] + (p1[1] - p0[1]) * t,
                )
            )

        return result

    # 설명: contour에서 가장 멀리 떨어진 두 점을 찾아 open stroke 중심선 계산에 사용합니다.
    def farthest_contour_indices(self, points):
        n = len(points)

        if n < 2:
            return 0, 0

        if n <= 300:
            sample_indices = list(range(n))
        else:
            step = max(1, n // 300)
            sample_indices = list(range(0, n, step))
            if sample_indices[-1] != n - 1:
                sample_indices.append(n - 1)

        best_i = sample_indices[0]
        best_j = sample_indices[-1]
        best_d2 = -1.0

        for a in sample_indices:
            ax, ay = points[a]
            for b in sample_indices:
                if a == b:
                    continue
                bx, by = points[b]
                d2 = (ax - bx) * (ax - bx) + (ay - by) * (ay - by)
                if d2 > best_d2:
                    best_d2 = d2
                    best_i = a
                    best_j = b

        return best_i, best_j

    # 설명: 닫힌 contour를 두 개의 rail로 나누어 중심선을 계산할 준비를 합니다.
    def split_closed_contour_into_two_rails(self, contour_points, idx_a, idx_b):
        n = len(contour_points)

        if n < 4 or idx_a == idx_b:
            return [], []

        if idx_a > idx_b:
            idx_a, idx_b = idx_b, idx_a

        rail_a = contour_points[idx_a:idx_b + 1]
        rail_b = contour_points[idx_b:] + contour_points[:idx_a + 1]
        rail_b = list(reversed(rail_b))

        if len(rail_a) < 2 or len(rail_b) < 2:
            return [], []

        return rail_a, rail_b

    # 설명: 두 경계 rail의 중간점을 연결해 centerline을 만듭니다.
    def build_center_path_from_nearest_boundary_rails(self, outer_rail, inner_rail):
        if len(outer_rail) < 2 or len(inner_rail) < 2:
            return []

        outer_len = self.pixel_polyline_length(outer_rail)
        inner_len = self.pixel_polyline_length(inner_rail)
        avg_len = (outer_len + inner_len) * 0.5

        if avg_len <= 3.0:
            return []

        outer_count = int(max(6, min(600, outer_len)))
        inner_count = int(max(6, min(600, inner_len)))

        outer_sampled = self.resample_pixel_polyline_by_count(outer_rail, outer_count)
        inner_sampled = self.resample_pixel_polyline_by_count(inner_rail, inner_count)

        center_path = []

        for ox, oy in outer_sampled:
            best = None
            best_d2 = float("inf")

            for ix, iy in inner_sampled:
                d2 = (ox - ix) * (ox - ix) + (oy - iy) * (oy - iy)

                if d2 < best_d2:
                    best_d2 = d2
                    best = (ix, iy)

            if best is None:
                continue

            center_point = (
                (ox + best[0]) * 0.5,
                (oy + best[1]) * 0.5,
            )

            if center_path and math.hypot(center_point[0] - center_path[-1][0], center_point[1] - center_path[-1][1]) < 0.15:
                continue

            center_path.append(center_point)

        return center_path

    # 설명: 더 긴 rail을 outer로 보고 두 rail 사이 중심선을 만듭니다.
    def build_center_path_from_boundary_rails(self, rail_a, rail_b):
        if len(rail_a) < 2 or len(rail_b) < 2:
            return []

        len_a = self.pixel_polyline_length(rail_a)
        len_b = self.pixel_polyline_length(rail_b)

        if len_a >= len_b:
            outer_rail = rail_a
            inner_rail = rail_b
        else:
            outer_rail = rail_b
            inner_rail = rail_a

        return self.build_center_path_from_nearest_boundary_rails(outer_rail, inner_rail)

    # 설명: 두꺼운 열린 선 contour에서 중심선을 추출합니다.
    def build_center_path_from_open_stroke_contour(self, contour):
        contour_points = self.contour_to_points(contour)

        if len(contour_points) < 6:
            return []

        idx_a, idx_b = self.farthest_contour_indices(contour_points)
        rail_a, rail_b = self.split_closed_contour_into_two_rails(contour_points, idx_a, idx_b)

        if not rail_a or not rail_b:
            return []

        return self.build_center_path_from_boundary_rails(rail_a, rail_b)

    # 설명: 외곽 contour와 내부 contour 쌍에서 중심선을 추출합니다.
    def build_center_path_from_contour_pair(self, outer_contour, inner_contour):
        outer_points = self.contour_to_points(outer_contour)
        inner_points = self.contour_to_points(inner_contour)

        if len(outer_points) < 4 or len(inner_points) < 4:
            return []

        outer_len = self.pixel_polyline_length(outer_points + [outer_points[0]])
        inner_len = self.pixel_polyline_length(inner_points + [inner_points[0]])
        sample_count = int(max(12, min(500, (outer_len + inner_len) * 0.25)))

        inner_sampled = self.resample_pixel_polyline_by_count(inner_points + [inner_points[0]], sample_count)
        center_path = []

        for ix, iy in inner_sampled:
            best = None
            best_d2 = float("inf")

            for ox, oy in outer_points:
                d2 = (ix - ox) * (ix - ox) + (iy - oy) * (iy - oy)

                if d2 < best_d2:
                    best_d2 = d2
                    best = (ox, oy)

            if best is None:
                continue

            center_path.append(
                (
                    (ix + best[0]) * 0.5,
                    (iy + best[1]) * 0.5,
                )
            )

        if len(center_path) > 2 and math.hypot(center_path[0][0] - center_path[-1][0], center_path[0][1] - center_path[-1][1]) <= 1.0:
            center_path[-1] = center_path[0]

        return center_path

    # 설명: mask의 연결 요소 하나를 픽셀 centerline path 목록으로 변환합니다.
    def component_mask_to_center_pixel_paths(self, component_mask):
        contours, hierarchy = cv2.findContours(
            component_mask,
            cv2.RETR_TREE,
            cv2.CHAIN_APPROX_NONE,
        )

        if not contours:
            return []

        center_paths = []
        hierarchy_data = hierarchy[0] if hierarchy is not None else None

        used_contours = set()

        if hierarchy_data is not None:
            for idx, h in enumerate(hierarchy_data):
                parent = int(h[3])

                if parent != -1:
                    continue

                outer = contours[idx]
                child_indices = []
                child = int(h[2])

                while child != -1:
                    child_indices.append(child)
                    child = int(hierarchy_data[child][0])

                valid_children = []

                for child_idx in child_indices:
                    if cv2.contourArea(contours[child_idx]) >= CONTOUR_MIN_AREA_PX:
                        valid_children.append(child_idx)

                if valid_children:
                    for child_idx in valid_children:
                        center_path = self.build_center_path_from_contour_pair(outer, contours[child_idx])

                        if len(center_path) >= 2:
                            center_paths.append(center_path)
                            used_contours.add(idx)
                            used_contours.add(child_idx)

        if not center_paths:
            external_contours, _ = cv2.findContours(
                component_mask,
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_NONE,
            )

            if external_contours:
                main_contour = max(external_contours, key=cv2.contourArea)
                center_path = self.build_center_path_from_open_stroke_contour(main_contour)

                if len(center_path) >= 2:
                    center_paths.append(center_path)

        return center_paths

    # 설명: stroke JSON이 없을 때 이미지 mask에서 색상별 centerline 경로를 추출합니다.
    def extract_strokes_from_contours(self, image_path, img, masks, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        color_paths = {"RED": [], "BLUE": [], "BLACK": []}

        for color_name, mask in masks.items():
            cleaned = self.clean_binary_mask(mask)
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, 8)
            paths = []
            centerline_preview = np.zeros_like(cleaned)

            for label in range(1, num_labels):
                area = stats[label, cv2.CC_STAT_AREA]

                if area < MIN_MASK_COMPONENT_AREA:
                    continue

                component_mask = np.zeros_like(cleaned)
                component_mask[labels == label] = 255
                pixel_center_paths = self.component_mask_to_center_pixel_paths(component_mask)

                for pixel_path in pixel_center_paths:
                    if len(pixel_path) < 2:
                        continue

                    for i in range(1, len(pixel_path)):
                        p0 = (int(round(pixel_path[i - 1][0])), int(round(pixel_path[i - 1][1])))
                        p1 = (int(round(pixel_path[i][0])), int(round(pixel_path[i][1])))
                        cv2.line(centerline_preview, p0, p1, 255, 1, cv2.LINE_AA)

                    robot_path = []

                    for px, py in pixel_path:
                        rx, ry = self.canvas_point_to_robot(
                            px,
                            py,
                            scale,
                            x_offset,
                            y_offset,
                            draw_min_x,
                            draw_max_y,
                        )
                        robot_path.append((rx, ry))

                    safe_paths = self.split_path_by_safe_area(robot_path)

                    for safe_path in safe_paths:
                        if len(safe_path) < 2:
                            continue

                        simplified = self.simplify_and_smooth_path(safe_path, min_dist=CURVE_MIN_DIST_MM)

                        if len(simplified) < 2:
                            continue

                        smoothed = self.catmull_rom_smooth_path(simplified)
                        resampled = self.resample_path(smoothed, SPLINE_RESAMPLE_STEP_MM)

                        if len(resampled) >= 2 and self.path_length(resampled) >= MIN_PATH_LENGTH_MM:
                            paths.append(resampled)

            paths = self.filter_short_paths(paths)
            paths = self.connect_paths_until_stable(paths, PATH_CONNECT_GAP_MM)
            paths = self.filter_short_paths(paths)
            paths = [self.resample_path(path, SPLINE_RESAMPLE_STEP_MM) for path in paths if len(path) >= 2]
            paths = self.optimize_paths_order(paths)

            color_paths[color_name] = paths

            if SAVE_DRAW_DEBUG_IMAGES:
                self.save_draw_debug_images(
                    image_path,
                    color_name,
                    cleaned,
                    centerline_preview,
                    paths,
                    scale,
                    x_offset,
                    y_offset,
                    draw_min_x,
                    draw_max_y,
                )

            self.get_logger().info(f"{color_name} boundary centerline paths extracted: {len(paths)}")

        return color_paths

    # 설명: 주문 이미지에서 그릴 경로를 추출합니다. JSON 우선, 없으면 contour fallback입니다.
    def extract_strokes(self, image_path):
        img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)

        if img is None:
            return {"RED": [], "BLUE": [], "BLACK": []}

        if img.ndim == 3 and img.shape[2] == 4:
            alpha = img[:, :, 3] / 255.0

            for color_idx in range(3):
                img[:, :, color_idx] = alpha * img[:, :, color_idx] + (1 - alpha) * 255

            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        img_h, img_w = img.shape[:2]
        scale, x_offset, y_offset, draw_min_x, draw_max_y = self.build_draw_area_transform(img_w, img_h)

        if USE_STROKE_JSON_FIRST:
            json_color_paths = self.extract_strokes_from_json(
                image_path,
                img_w,
                img_h,
                scale,
                x_offset,
                y_offset,
                draw_min_x,
                draw_max_y,
            )

            if json_color_paths is not None:
                return json_color_paths

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        masks = self.create_color_masks(hsv)

        self.get_logger().warn("Stroke JSON missing. Falling back to boundary centerline path extraction.")
        send_log("Stroke JSON missing. Falling back to boundary centerline path extraction.", "warn")

        return self.extract_strokes_from_contours(
            image_path,
            img,
            masks,
            scale,
            x_offset,
            y_offset,
            draw_min_x,
            draw_max_y,
        )

    # 설명: 색상 하나의 모든 path를 실제 로봇으로 그리며 진행률을 업데이트합니다.
    def draw_line_paths(self, order_id, color, paths, current_time_spent, total_estimated_time):
        if DEBUG_MODE and DEBUG_MAX_PATHS is not None:
            paths = paths[:DEBUG_MAX_PATHS]
            self.get_logger().warn(
                f"DEBUG_MAX_PATHS={DEBUG_MAX_PATHS}, running only {len(paths)} {color} paths"
            )

        self.get_logger().info(f"Starting {color} drawing. Total paths: {len(paths)}")

        total_paths = len(paths)

        # ✅ 시작 시점: currentPath=0으로 초기화
        update_robot_status(
            state="DRAWING",
            stage="DRAW",
            pen=color,
            currentPath=0,
            totalPath=total_paths,
        )

        pen_down = False
        last_draw_point = None
        self.active_pen_color = color

        for idx, path in enumerate(paths):
            self.ensure_not_cancelled(order_id)

            if len(path) < 2:
                continue

            sx, sy = path[0]
            start_z = self.get_draw_z(sx, sy)
            start_hop_z = self.get_draw_hop_z(sx, sy)

            self.get_logger().info(f"[{color}] Starting path {idx + 1}/{len(paths)}")

            need_descend = True

            if pen_down and last_draw_point is not None:
                gap_from_last = self.distance(last_draw_point, (sx, sy))

                if gap_from_last <= NO_LIFT_BETWEEN_PATH_GAP_MM:
                    self.get_logger().info(
                        f"[{color}] previous path gap {gap_from_last:.3f}mm, keep pen down and continue"
                    )
                    need_descend = False
                else:
                    lx, ly = last_draw_point
                    self.move_to_pos(
                        lx,
                        ly,
                        self.get_draw_hop_z(lx, ly),
                        vel=DRAW_LIFT_VEL,
                        acc=DRAW_LIFT_ACC,
                        radius=0.0,
                        wait_response=True,
                    )
                    pen_down = False
                    self.pen_is_down = False

            if need_descend:
                if idx == 0 or not pen_down:
                    self.move_to_pos(
                        sx,
                        sy,
                        SAFE_Z if idx == 0 else start_hop_z,
                        vel=DRAW_APPROACH_VEL,
                        acc=DRAW_APPROACH_ACC,
                        radius=0.0,
                        wait_response=True,
                    )

                self.interruptible_sleep(0.2)

                self.move_to_pos(
                    sx,
                    sy,
                    start_z,
                    vel=DRAW_DESCEND_VEL,
                    acc=DRAW_DESCEND_ACC,
                    radius=0.0,
                    wait_response=True,
                )
                self.interruptible_sleep(0.15)
                pen_down = True
                self.pen_is_down = True

            self.draw_path_smooth(path, order_id=order_id)

            ex, ey = path[-1]
            last_draw_point = (ex, ey)
            self.last_draw_point = last_draw_point

            # ✅ 실제 path 그리기 완료 후 currentPath 업데이트 (싱크 맞춤)
            update_robot_status(
                state="DRAWING",
                stage="DRAW",
                pen=color,
                currentPath=idx + 1,
                totalPath=total_paths,
            )

            next_path = None
            if idx + 1 < len(paths):
                next_path = paths[idx + 1]

            keep_down_for_next = False
            if next_path is not None and len(next_path) >= 2:
                next_sx, next_sy = next_path[0]
                next_gap = self.distance((ex, ey), (next_sx, next_sy))
                keep_down_for_next = next_gap <= NO_LIFT_BETWEEN_PATH_GAP_MM

                if keep_down_for_next:
                    self.get_logger().info(
                        f"[{color}] next path gap {next_gap:.3f}mm, skip lift"
                    )

            if not keep_down_for_next:
                end_hop_z = self.get_draw_hop_z(ex, ey)

                self.move_to_pos(
                    ex,
                    ey,
                    end_hop_z,
                    vel=DRAW_LIFT_VEL,
                    acc=DRAW_LIFT_ACC,
                    radius=0.0,
                    wait_response=True,
                )
                self.interruptible_sleep(0.2)
                pen_down = False
                self.pen_is_down = False

            current_time_spent += 5
            progress_pct = int((current_time_spent / total_estimated_time) * 100)

            self.update_progress(
                order_id,
                min(95, progress_pct),
                max(0, total_estimated_time - current_time_spent),
            )

        if paths:
            last_x, last_y = paths[-1][-1]

            if pen_down:
                self.move_to_pos(
                    last_x,
                    last_y,
                    self.get_draw_hop_z(last_x, last_y),
                    vel=DRAW_LIFT_VEL,
                    acc=DRAW_LIFT_ACC,
                    radius=0.0,
                    wait_response=True,
                )
                self.interruptible_sleep(0.2)
                self.pen_is_down = False

            self.move_to_pos(
                last_x,
                last_y,
                SAFE_Z,
                vel=DRAW_LIFT_VEL,
                acc=DRAW_LIFT_ACC,
                radius=0.0,
                wait_response=True,
            )
            self.interruptible_sleep(0.8)

        return current_time_spent

    # 설명: 주문 하나의 전체 작업 시나리오를 실행합니다.
    def process_and_draw(self, order_id, image_path):
        cancelled = False
        impact_stopped = False
        failed = False
        try:
            self.ensure_not_cancelled(order_id)

            if not os.path.exists(image_path):
                self.get_logger().error(f"Image file not found: {image_path}")
                return

            color_paths = self.extract_strokes(image_path)
            self.ensure_not_cancelled(order_id)

            total_strokes = sum(len(paths) for paths in color_paths.values())

            if total_strokes == 0:
                self.get_logger().warn("No drawable paths were extracted.")
                return

            colors_to_draw = self.get_colors_to_draw(color_paths)

            if not colors_to_draw:
                self.get_logger().warn("No drawable colors are available.")
                return

            total_estimated_time = 25 + (len(colors_to_draw) * 30) + (total_strokes * 5) + 20
            current_time_spent = 0

            self.update_progress(order_id, 5, total_estimated_time)
            self.ensure_not_cancelled(order_id)

            if self.should_run_stage("CASE_PICKUP"):
                self.handle_case_pickup()
                self.ensure_not_cancelled(order_id)

            if self.should_run_stage("CASE_PLACE"):
                self.handle_case_place()
                self.ensure_not_cancelled(order_id)

            current_time_spent += 25
            progress_pct = int((current_time_spent / total_estimated_time) * 100)

            self.update_progress(
                order_id,
                progress_pct,
                max(0, total_estimated_time - current_time_spent),
            )
            self.ensure_not_cancelled(order_id)

            for color in ["RED", "BLUE", "BLACK"]:
                if color not in colors_to_draw:
                    continue

                paths = color_paths[color]
                if not paths:
                    continue

                self.ensure_not_cancelled(order_id)

                if self.should_run_stage("PICKUP_PEN"):
                    self.pickup_pen(color)
                    self.ensure_not_cancelled(order_id)

                current_time_spent += 15
                progress_pct = int((current_time_spent / total_estimated_time) * 100)

                self.update_progress(
                    order_id,
                    min(95, progress_pct),
                    max(0, total_estimated_time - current_time_spent),
                )
                self.ensure_not_cancelled(order_id)

                if self.should_run_stage("DRAW"):
                    current_time_spent = self.draw_line_paths(
                        order_id,
                        color,
                        paths,
                        current_time_spent,
                        total_estimated_time,
                    )
                    self.ensure_not_cancelled(order_id)
                    self.interruptible_sleep(3.0)
                    self.ensure_not_cancelled(order_id)

                if self.should_run_stage("PLACE_PEN"):
                    self.interruptible_sleep(2.0)
                    self.ensure_not_cancelled(order_id)
                    self.place_pen(color)
                    self.ensure_not_cancelled(order_id)

                current_time_spent += 15
                progress_pct = int((current_time_spent / total_estimated_time) * 100)

                self.update_progress(
                    order_id,
                    min(95, progress_pct),
                    max(0, total_estimated_time - current_time_spent),
                )
                self.ensure_not_cancelled(order_id)

            self.update_progress(order_id, 96, 20)
            self.ensure_not_cancelled(order_id)

            if self.should_run_stage("FINISHED_CASE_PICKUP"):
                self.handle_finished_case_pickup()
                self.ensure_not_cancelled(order_id)

            if self.should_run_stage("CASE_DROP"):
                self.handle_finished_case_drop()
                self.ensure_not_cancelled(order_id)

            if self.should_run_stage("HOME"):
                self.move_to_pos(
                    ROBOT_HOME_X,
                    ROBOT_HOME_Y,
                    SAFE_Z,
                    vel=HOME_RETURN_VEL,
                    acc=HOME_RETURN_ACC,
                    radius=0.0,
                    wait_response=True,
                )
                self.interruptible_sleep(1.0)

        except OrderCancelled:
            cancelled = True
        except ImpactStopped:
            impact_stopped = True
        except Exception as e:
            failed = True
            self.get_logger().error(f"Unhandled drawing error: {e}")
            send_log(f"Drawing job failed for order {order_id}: {e}", "error")

        finally:
            if cancelled:
                self.recover_from_cancel(order_id)
                self.mark_order_cancelled(order_id)
            elif impact_stopped:
                self.mark_order_impact_stopped(order_id)
            elif failed:
                self.mark_order_failed(order_id)
                update_robot_status(
                    state="ERROR",
                    stage="FAILED",
                    pen="NONE",
                    currentPath=0,
                    totalPath=0
                )
            else:
                self.complete_order(order_id)
                update_robot_status(
                    state="IDLE",
                    stage="WAITING",
                    pen="NONE",
                    currentPath=0,
                    totalPath=0
                )

            self.pen_is_down = False
            self.last_draw_point = None
            self.pen_in_gripper = False
            self.active_pen_color = None
            self.case_in_gripper = False
            self.case_on_work_area = False
            self.current_order_id = None
            self.cancel_event.clear()
            self.impact_stop_event.clear()
            self.impact_stop_reason = None
            self.motion_stop_requested = False
            self.cancel_recovery_active = False
            self.last_impact_signature = None
            self.is_drawing = False

    # 설명: DB와 Flask API에 주문 진행률과 예상 시간을 반영합니다.
    def update_progress(self, order_id, progress, estimated_time):
        try:
            self.cursor.execute(
                "UPDATE orders SET progress=?, estimated_time=? WHERE id=?",
                (int(progress), int(estimated_time), order_id),
            )
            self.conn.commit()

        except Exception as e:
            self.get_logger().error(f"Failed to update progress: {e}")

        try:
            url = f"http://127.0.0.1:5000/api/orders/{order_id}/progress"
            requests.patch(
                url,
                json={
                    "progress": int(progress),
                    "estimated_time": int(estimated_time),
                },
                timeout=2,
            )
        except Exception:
            pass

    # 설명: 예외 발생 시 주문을 error 상태로 저장합니다.
    def mark_order_failed(self, order_id):
        try:
            self.cursor.execute(
                "UPDATE orders SET status='error' WHERE id=?",
                (order_id,),
            )
            self.conn.commit()
        except Exception as e:
            self.get_logger().error(f"Failed to save error status: {e}")

        try:
            requests.patch(
                f"http://127.0.0.1:5000/api/orders/{order_id}/status",
                json={"status": "error"},
                timeout=2,
            )
        except Exception:
            pass

        self.get_logger().error(f"Order {order_id} marked as failed")

    # 설명: 모든 작업 완료 후 주문을 done 상태로 저장합니다.
    def complete_order(self, order_id):
        self.update_progress(order_id, 100, 0)

        self.cursor.execute(
            "UPDATE orders SET status='done' WHERE id=?",
            (order_id,),
        )
        self.conn.commit()

        try:
            requests.patch(
                f"http://127.0.0.1:5000/api/orders/{order_id}/status",
                json={"status": "done"},
                timeout=2,
            )
        except Exception:
            pass

        self.get_logger().info(f"Order {order_id} completed")

        update_robot_status(
            state="IDLE",
            stage="COMPLETE",
            pen="NONE",
            currentPath=0,
            totalPath=0
        )

    


# 설명: ROS2 노드를 생성하고 spin을 시작하는 진입점입니다.
def main(args=None):
    rclpy.init(args=args)

    node = RobotDrawerNode()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.conn.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

