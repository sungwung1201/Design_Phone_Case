import rclpy
from rclpy.node import Node
import sqlite3
import os
import time
from pathlib import Path
import cv2
import numpy as np
import threading
import math
import json
import requests

try:
    from dsr_msgs2.srv import MoveLine, SetCtrlBoxDigitalOutput
    HAS_DSR_MSGS = True
except ImportError:
    HAS_DSR_MSGS = False

try:
    from dsr_msgs2.srv import MoveSplineTask
    from std_msgs.msg import Float64MultiArray
    HAS_SPLINE_TASK = True
except ImportError:
    HAS_SPLINE_TASK = False


# ==================================================
# 로봇 좌표 설정
# ==================================================
ROBOT_MIN_X = 499.116
ROBOT_MAX_X = 563.307
ROBOT_MIN_Y = -61.348
ROBOT_MAX_Y = 49.571

ROBOT_HOME_X = 486.372
ROBOT_HOME_Y = -26.267

DRAW_Z = 344.344
SAFE_Z = 422.344

DRAW_HOP_OFFSET = 8.0

TOOL_RX = 19.757
TOOL_RY = -179.020
TOOL_RZ = 20.665


# ==================================================
# iPhone 15 Plus 케이스 드로잉 안전 설정
# ==================================================
DRAW_MARGIN = 2.0


# ==================================================
# Z 보정 설정
# ==================================================
Z_LT = DRAW_Z
Z_RT = DRAW_Z
Z_LB = DRAW_Z
Z_RB = DRAW_Z


# ==================================================
# 펜 좌표
# ==================================================
STAND_PICK_BLUE_X = 371.864
STAND_PICK_BLUE_Y = -63.731
STAND_PICK_BLUE_Z = 271.854
STAND_PICK_BLUE_RX = 5.755
STAND_PICK_BLUE_RY = -178.407
STAND_PICK_BLUE_RZ = 6.937

STAND_PICK_BLACK_X = 323.632
STAND_PICK_BLACK_Y = -5.650
STAND_PICK_BLACK_Z = 306.257
STAND_PICK_BLACK_RX = 93.948
STAND_PICK_BLACK_RY = 179.951
STAND_PICK_BLACK_RZ = 93.680

STAND_PICK_RED_X = 328.517
STAND_PICK_RED_Y = 75.732
STAND_PICK_RED_Z = 306.825
STAND_PICK_RED_RX = 30.451
STAND_PICK_RED_RY = -179.541
STAND_PICK_RED_RZ = 30.245


# ==================================================
# 폰 케이스 좌표
# ==================================================
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
CASE_PLACE_Z = 297.000
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
# 구간별 속도 설정
# ==================================================
MOVE_VEL = 200.0
MOVE_ACC = 100.0

DRAW_VEL = 80.0
DRAW_ACC = 30.0

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
# 선 그리기 품질 설정
# ==================================================
LINE_BLEND_RADIUS = 0.5

LINE_POINT_MIN_WAIT = 0.10
LINE_POINT_MAX_WAIT = 0.20

CURVE_MIN_DIST_MM = 0.2

SKELETON_MIN_PIXELS = 3

MIN_MASK_COMPONENT_AREA = 5

USE_SPLINE_TASK = False
MOVE_SPLINE_TASK_SERVICE = "/dsr01/motion/move_spline_task"
SPLINE_MAX_POINTS = 120
SPLINE_MIN_POINTS = 3
SPLINE_SERVICE_WAIT_SEC = 2.0
SPLINE_RESAMPLE_STEP_MM = 0.2
MIN_PATH_LENGTH_MM = 0.1
PATH_CONNECT_FORCE_GAP_MM = 1.0
PATH_CONNECT_HARD_LIMIT_MM = 1.3
PATH_CONNECT_MAX_ANGLE_DEG = None
PATH_CONNECT_ENDPOINT_TO_PATH_MM = 1.3
NO_LIFT_BETWEEN_PATH_GAP_MM = 1.3
PATH_CONNECT_GAP_MM = PATH_CONNECT_HARD_LIMIT_MM


# ==================================================
# 디버그 모드 설정
# ==================================================
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
# 파일 및 DB 경로
# ==================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "database.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
DEBUG_OUTPUT_FOLDER = os.path.join(BASE_DIR, "debug_draw")
SAVE_DRAW_DEBUG_IMAGES = True

# ==================================================
# Skeleton 대체 경로 추출 설정
# 1순위: Canvas stroke JSON 직접 사용
# 2순위: 이미지 contour 기반 path 추출
# ==================================================
USE_STROKE_JSON_FIRST = True
STROKE_JSON_CANDIDATE_SUFFIXES = [".json", "_strokes.json", ".strokes.json"]
CATMULL_ROM_SAMPLES_PER_SEGMENT = 8
CONTOUR_MIN_AREA_PX = 3.0
CONTOUR_APPROX_EPSILON_PX = 0.0

# ==================================================
# 서버 관리자 로그 전송
# ==================================================
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

# ==================================================
# 서버 관리자 상태 전송
# ==================================================
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
    def __init__(self):
        super().__init__("robot_drawer")

        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.timer = self.create_timer(1.0, self.poll_database)
        self.is_drawing = False

        if HAS_DSR_MSGS:
            self.move_line_client = self.create_client(
                MoveLine,
                "/dsr01/motion/move_line",
            )
            self.set_io_client = self.create_client(
                SetCtrlBoxDigitalOutput,
                "/dsr01/io/set_ctrl_box_digital_output",
            )

            if HAS_SPLINE_TASK:
                self.move_spline_task_client = self.create_client(
                    MoveSplineTask,
                    MOVE_SPLINE_TASK_SERVICE,
                )
            else:
                self.move_spline_task_client = None

            self.get_logger().info("두산 로봇 & 그리퍼 패키지 로드 완료")
        else:
            self.get_logger().warn("dsr_msgs2 없음. 실제 로봇 동작 없이 로그만 출력.")

        self.get_logger().info("노드 가동. 주문 대기 중")
        self.get_logger().info("현재 모드: Canvas Stroke JSON 우선 + Contour path fallback + MoveSplineTask")
        self.get_logger().info(f"iPhone 15 Plus 케이스 안전 마진: {DRAW_MARGIN} mm")
        self.get_logger().info(f"곡선 보존 min_dist: {CURVE_MIN_DIST_MM} mm")
        self.get_logger().info(f"DRAW_HOP_OFFSET: {DRAW_HOP_OFFSET} mm")
        self.get_logger().info(f"SPLINE_RESAMPLE_STEP_MM: {SPLINE_RESAMPLE_STEP_MM} mm")
        self.get_logger().info(f"PATH_CONNECT_FORCE_GAP_MM: {PATH_CONNECT_FORCE_GAP_MM} mm")
        self.get_logger().info(f"PATH_CONNECT_HARD_LIMIT_MM: {PATH_CONNECT_HARD_LIMIT_MM} mm")
        self.get_logger().info("PATH 연결 기준: 1.0mm 이하면 무조건 연결, 1.3mm 이상이면 연결 안 함, 방향 무시")
        self.get_logger().info(f"endpoint-to-path 연결 기준: {PATH_CONNECT_ENDPOINT_TO_PATH_MM} mm")
        self.get_logger().info(f"가까운 다음 path no-lift 기준: {NO_LIFT_BETWEEN_PATH_GAP_MM} mm")
        self.get_logger().info(f"디버그 이미지 저장: {SAVE_DRAW_DEBUG_IMAGES}, 경로: {DEBUG_OUTPUT_FOLDER}")
        self.get_logger().info(f"MoveSplineTask 사용 설정: {USE_SPLINE_TASK}, 서비스명: {MOVE_SPLINE_TASK_SERVICE}")

        if DEBUG_MODE:
            self.get_logger().warn("DEBUG_MODE=True")
            self.get_logger().warn(f"DEBUG_START_STAGE={DEBUG_START_STAGE}")
            self.get_logger().warn(f"DEBUG_END_STAGE={DEBUG_END_STAGE}")
            self.get_logger().warn(f"DEBUG_COLOR={DEBUG_COLOR}")
            self.get_logger().warn(f"DEBUG_MAX_PATHS={DEBUG_MAX_PATHS}")

    def should_run_stage(self, stage_name):
        if not DEBUG_MODE:
            return True

        if stage_name not in STAGE_ORDER:
            self.get_logger().error(f"알 수 없는 stage_name: {stage_name}")
            return False

        if DEBUG_START_STAGE not in STAGE_ORDER:
            self.get_logger().error(f"DEBUG_START_STAGE 오류: {DEBUG_START_STAGE}")
            return False

        if DEBUG_END_STAGE not in STAGE_ORDER:
            self.get_logger().error(f"DEBUG_END_STAGE 오류: {DEBUG_END_STAGE}")
            return False

        start_idx = STAGE_ORDER.index(DEBUG_START_STAGE)
        end_idx = STAGE_ORDER.index(DEBUG_END_STAGE)
        current_idx = STAGE_ORDER.index(stage_name)

        if start_idx > end_idx:
            self.get_logger().error("DEBUG_START_STAGE가 DEBUG_END_STAGE보다 뒤에 있습니다.")
            return False

        return start_idx <= current_idx <= end_idx

    def get_colors_to_draw(self, color_paths):
        available = [c for c in ["RED", "BLUE", "BLACK"] if color_paths[c]]

        if not DEBUG_MODE:
            return available

        if DEBUG_COLOR == "ALL":
            return available

        if DEBUG_COLOR in ["RED", "BLUE", "BLACK"]:
            if color_paths[DEBUG_COLOR]:
                return [DEBUG_COLOR]

            self.get_logger().warn(f"{DEBUG_COLOR} 색상 경로가 없습니다.")
            return []

        self.get_logger().warn(f"DEBUG_COLOR 설정 오류: {DEBUG_COLOR}")
        return available

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
        response_timeout=5.0,
    ):
        if vel is None:
            vel = MOVE_VEL

        if acc is None:
            acc = MOVE_ACC

        if not HAS_DSR_MSGS:
            self.get_logger().info(
                f"[SIM] move_to_pos "
                f"x={x:.3f}, y={y:.3f}, z={z:.3f}, "
                f"rx={rx:.3f}, ry={ry:.3f}, rz={rz:.3f}, "
                f"vel={vel:.1f}, acc={acc:.1f}, radius={radius:.2f}"
            )
            return

        while not self.move_line_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info("모션 서비스 대기...")

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

        future = self.move_line_client.call_async(req)

        if wait_response:
            start_time = time.time()

            while rclpy.ok() and not future.done():
                if response_timeout is not None and time.time() - start_time > response_timeout:
                    self.get_logger().warn("move_line 서비스 응답 대기 시간 초과")
                    send_log("⚠️ move_line 서비스 응답 대기 시간 초과", "warn")
                    break

                time.sleep(0.01)

    def control_gripper(self, mode):
        if not HAS_DSR_MSGS:
            self.get_logger().info(f"[SIM] control_gripper mode={mode}")
            return

        while not self.set_io_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info("I/O 서비스 대기...")

        if mode == "PEN_CLOSE":
            p1, p2 = 1, 0
            desc = "펜 닫기(0mm)"
        elif mode == "PEN_OPEN":
            p1, p2 = 0, 0
            desc = "펜 열기(30mm)"
        elif mode == "CASE_OPEN":
            p1, p2 = 0, 1
            desc = "케이스 열기(105mm)"
        elif mode == "CASE_CLOSE":
            p1, p2 = 1, 1
            desc = "케이스 닫기(92mm)"
        else:
            self.get_logger().warn(f"알 수 없는 그리퍼 모드: {mode}")
            return

        req1 = SetCtrlBoxDigitalOutput.Request()
        req1.index = 1
        req1.value = p1
        self.set_io_client.call_async(req1)

        req2 = SetCtrlBoxDigitalOutput.Request()
        req2.index = 2
        req2.value = p2
        self.set_io_client.call_async(req2)

        self.get_logger().info(f"그리퍼 작동: {desc} (Pin1:{p1}, Pin2:{p2})")
        time.sleep(1.5)

    def get_draw_area_bounds(self):
        min_x = ROBOT_MIN_X + DRAW_MARGIN
        max_x = ROBOT_MAX_X - DRAW_MARGIN
        min_y = ROBOT_MIN_Y + DRAW_MARGIN
        max_y = ROBOT_MAX_Y - DRAW_MARGIN
        return min_x, max_x, min_y, max_y

    def is_safe_draw_point(self, x, y):
        min_x, max_x, min_y, max_y = self.get_draw_area_bounds()

        return min_x <= x <= max_x and min_y <= y <= max_y

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

    def get_draw_z(self, x, y):
        min_x, max_x, min_y, max_y = self.get_draw_area_bounds()

        tx = (x - min_x) / (max_x - min_x)
        ty = (y - min_y) / (max_y - min_y)

        tx = max(0.0, min(1.0, tx))
        ty = max(0.0, min(1.0, ty))

        z_bottom = Z_LB * (1.0 - tx) + Z_RB * tx
        z_top = Z_LT * (1.0 - tx) + Z_RT * tx

        return z_bottom * (1.0 - ty) + z_top * ty

    def get_draw_hop_z(self, x, y):
        return self.get_draw_z(x, y) + DRAW_HOP_OFFSET

    def distance(self, p1, p2):
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    def calc_line_wait_time(self, p1, p2, vel):
        d = self.distance(p1, p2)
        t = d / max(float(vel), 1.0)

        if t < LINE_POINT_MIN_WAIT:
            return LINE_POINT_MIN_WAIT

        if t > LINE_POINT_MAX_WAIT:
            return LINE_POINT_MAX_WAIT

        return t

    def path_length(self, path):
        if len(path) < 2:
            return 0.0

        total = 0.0
        for i in range(1, len(path)):
            total += self.distance(path[i - 1], path[i])

        return total

    def filter_short_paths(self, paths):
        filtered = []

        for path in paths:
            if len(path) < 2:
                continue

            if self.path_length(path) >= MIN_PATH_LENGTH_MM:
                filtered.append(path)

        return filtered

    def concat_paths(self, path_a, path_b):
        if not path_a:
            return path_b

        if not path_b:
            return path_a

        if self.distance(path_a[-1], path_b[0]) <= 0.05:
            return path_a + path_b[1:]

        return path_a + path_b

    def vector_angle_deg(self, v1, v2):
        n1 = math.hypot(v1[0], v1[1])
        n2 = math.hypot(v2[0], v2[1])

        if n1 <= 1e-6 or n2 <= 1e-6:
            return 0.0

        dot = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
        dot = max(-1.0, min(1.0, dot))
        return math.degrees(math.acos(dot))

    def path_start_vector(self, path, sample_count=3):
        if len(path) < 2:
            return (0.0, 0.0)

        end_idx = min(len(path) - 1, sample_count)
        return (
            path[end_idx][0] - path[0][0],
            path[end_idx][1] - path[0][1],
        )

    def path_end_vector(self, path, sample_count=3):
        if len(path) < 2:
            return (0.0, 0.0)

        start_idx = max(0, len(path) - 1 - sample_count)
        return (
            path[-1][0] - path[start_idx][0],
            path[-1][1] - path[start_idx][1],
        )

    def can_connect_paths(self, prev_path, next_path, gap):
        if gap >= PATH_CONNECT_HARD_LIMIT_MM:
            return False

        return True

    def nearest_point_index(self, point, path):
        best_idx = 0
        best_dist = float("inf")

        for i, p in enumerate(path):
            d = self.distance(point, p)
            if d < best_dist:
                best_dist = d
                best_idx = i

        return best_idx, best_dist

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

    def connect_paths_until_stable(self, paths, max_gap=PATH_CONNECT_GAP_MM, max_passes=5):
        result = [list(path) for path in paths if len(path) >= 2]

        for _ in range(max_passes):
            before_count = len(result)
            result = self.connect_close_paths(result, max_gap)
            after_count = len(result)

            if after_count >= before_count:
                break

        return result

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

    def split_path_for_spline(self, path, max_points=SPLINE_MAX_POINTS):
        if len(path) <= max_points:
            return [path]

        chunks = []
        start = 0

        while start < len(path) - 1:
            end = min(start + max_points, len(path))
            chunk = path[start:end]

            if len(chunk) >= SPLINE_MIN_POINTS:
                chunks.append(chunk)

            if end >= len(path):
                break

            start = end - 1

        return chunks

    def move_spline_task(self, path, vel=None, acc=None, wait_response=True, response_timeout=30.0):
        if vel is None:
            vel = DRAW_LINE_VEL

        if acc is None:
            acc = DRAW_LINE_ACC

        if len(path) < SPLINE_MIN_POINTS:
            return False

        if not HAS_DSR_MSGS or not HAS_SPLINE_TASK:
            return False

        if self.move_spline_task_client is None:
            return False

        if not self.move_spline_task_client.wait_for_service(timeout_sec=SPLINE_SERVICE_WAIT_SEC):
            self.get_logger().warn("MoveSplineTask 서비스가 없어 MoveLine 방식으로 대체합니다.")
            send_log("MoveSplineTask 서비스가 없어 MoveLine 방식으로 대체합니다.", "warn")
            return False

        req = MoveSplineTask.Request()
        req.pos = []

        for x, y in path:
            z = self.get_draw_z(x, y)
            point_msg = Float64MultiArray()
            point_msg.data = [
                float(x),
                float(y),
                float(z),
                float(TOOL_RX),
                float(TOOL_RY),
                float(TOOL_RZ),
            ]
            req.pos.append(point_msg)

        req.pos_cnt = len(req.pos)
        req.vel = [float(vel), float(vel)]
        req.acc = [float(acc), float(acc)]
        req.time = 0.0
        req.ref = 0
        req.mode = 0
        req.opt = 0
        req.sync_type = 0

        future = self.move_spline_task_client.call_async(req)

        if wait_response:
            start_time = time.time()

            while rclpy.ok() and not future.done():
                if response_timeout is not None and time.time() - start_time > response_timeout:
                    self.get_logger().warn("MoveSplineTask 서비스 응답 대기 시간 초과")
                    send_log("MoveSplineTask 서비스 응답 대기 시간 초과", "warn")
                    return False

                time.sleep(0.01)

            try:
                response = future.result()
                return bool(response.success)
            except Exception as e:
                self.get_logger().warn(f"MoveSplineTask 실행 실패: {e}")
                send_log(f"MoveSplineTask 실행 실패: {e}", "warn")
                return False

        return True

    def draw_path_with_moveline(self, path):
        prev_point = path[0]

        for i, (rx, ry) in enumerate(path):
            z = self.get_draw_z(rx, ry)

            if i < len(path) - 1:
                r = LINE_BLEND_RADIUS
            else:
                r = 0.0

            if len(path) < 10:
                r = 0.0

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
            time.sleep(wait_time)
            prev_point = (rx, ry)

    def draw_path_smooth(self, path):
        resampled_path = self.resample_path(path, SPLINE_RESAMPLE_STEP_MM)

        if USE_SPLINE_TASK and len(resampled_path) >= SPLINE_MIN_POINTS:
            chunks = self.split_path_for_spline(resampled_path, SPLINE_MAX_POINTS)

            self.get_logger().info(
                f"MoveSplineTask stroke 실행: points={len(resampled_path)}, chunks={len(chunks)}"
            )

            for chunk_idx, chunk in enumerate(chunks):
                self.get_logger().info(
                    f"MoveSplineTask chunk {chunk_idx + 1}/{len(chunks)}: points={len(chunk)}"
                )

                ok = self.move_spline_task(
                    chunk,
                    vel=DRAW_LINE_VEL,
                    acc=DRAW_LINE_ACC,
                    wait_response=True,
                )

                if not ok:
                    self.get_logger().warn(
                        f"MoveSplineTask chunk {chunk_idx + 1}/{len(chunks)} 실패. 해당 chunk만 MoveLine fallback 실행"
                    )
                    send_log(
                        f"MoveSplineTask chunk {chunk_idx + 1}/{len(chunks)} 실패. 해당 chunk만 MoveLine fallback 실행",
                        "warn",
                    )
                    self.draw_path_with_moveline(chunk)

            return

        self.draw_path_with_moveline(resampled_path)

    def handle_case_pickup(self):
        self.get_logger().info("빈 폰케이스를 가지러 갑니다. (CASE_PICKUP)")

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
        time.sleep(1.0)

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
        time.sleep(1.0)

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
        time.sleep(1.0)

    def handle_case_place(self):
        self.get_logger().info("폰케이스를 작업대에 세팅합니다. (CASE_PLACE)")

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
        time.sleep(1.0)

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
        time.sleep(1.0)

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
        time.sleep(1.0)

    def handle_finished_case_pickup(self):
        self.get_logger().info("완성된 폰케이스를 수거합니다. (FINISHED_CASE_PICKUP)")

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
        time.sleep(1.0)

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
        time.sleep(1.0)

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
        time.sleep(1.0)

    def handle_finished_case_drop(self):
        self.get_logger().info("완성품 보관함에 폰케이스를 배출합니다. (CASE_DROP)")

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
        time.sleep(1.0)

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
        time.sleep(1.0)

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
        time.sleep(1.0)

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

    def pickup_pen(self, color="BLACK"):
        self.get_logger().info(f"{color} 펜을 집어옵니다. (PICKUP_PEN)")

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
        time.sleep(1.0)

        self.control_gripper("PEN_OPEN")

        self.get_logger().info("[하강] 펜 거치대로 내려가는 중")
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
        time.sleep(1.0)

        self.control_gripper("PEN_CLOSE")

        self.get_logger().info("[상승] 펜을 뽑아 올리는 중")
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
        time.sleep(1.0)

    def place_pen(self, color="BLACK"):
        self.get_logger().info(f"{color} 펜을 제자리에 꽂습니다. (PLACE_PEN)")

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
        time.sleep(1.0)

        self.get_logger().info("[하강] 펜을 거치대에 꽂는 중")
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
        time.sleep(1.0)

        self.control_gripper("PEN_OPEN")

        self.get_logger().info("[상승] 펜을 놓고 빈 손으로 올라오는 중")
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
        time.sleep(1.0)

    def poll_database(self):
        if self.is_drawing:
            return

        self.cursor.execute(
            "SELECT id, image_path FROM orders WHERE status='waiting' ORDER BY created_at ASC LIMIT 1"
        )
        row = self.cursor.fetchone()

        if not row:
            return

        order_id, filename = row
        image_path = os.path.join(UPLOAD_FOLDER, filename)

        self.get_logger().info(f"주문 포착. ID={order_id}")

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

        threading.Thread(
            target=self.process_and_draw,
            args=(order_id, image_path),
            daemon=True,
        ).start()

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

    def skeletonize_mask(self, mask):
        binary = self.clean_binary_mask(mask)

        if hasattr(cv2, "ximgproc") and hasattr(cv2.ximgproc, "thinning"):
            return cv2.ximgproc.thinning(binary)

        skel = np.zeros(binary.shape, np.uint8)
        element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        img = binary.copy()

        while True:
            eroded = cv2.erode(img, element)
            temp = cv2.dilate(eroded, element)
            temp = cv2.subtract(img, temp)
            skel = cv2.bitwise_or(skel, temp)
            img = eroded.copy()

            if cv2.countNonZero(img) == 0:
                break

        return skel

    def skeleton_to_pixel_paths(self, skeleton):
        ys, xs = np.where(skeleton > 0)
        pixels = set(zip(xs.tolist(), ys.tolist()))

        if not pixels:
            return []

        neighbor_cache = {}

        def neighbors(p):
            if p in neighbor_cache:
                return neighbor_cache[p]

            x, y = p
            result = []

            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue

                    q = (x + dx, y + dy)

                    if q in pixels:
                        result.append(q)

            result.sort(key=lambda q: math.atan2(q[1] - y, q[0] - x))
            neighbor_cache[p] = result
            return result

        degree = {p: len(neighbors(p)) for p in pixels}
        visited_edges = set()
        paths = []

        def edge_key(a, b):
            if a <= b:
                return (a, b)
            return (b, a)

        def trace_path(start, nxt):
            path = [start, nxt]
            visited_edges.add(edge_key(start, nxt))

            prev = start
            curr = nxt
            guard = 0

            while True:
                if curr == start and len(path) > 2:
                    break

                if degree[curr] != 2:
                    break

                nbs = neighbors(curr)
                candidates = [n for n in nbs if n != prev]

                if not candidates:
                    break

                next_pixel = None

                for cand in candidates:
                    if edge_key(curr, cand) not in visited_edges:
                        next_pixel = cand
                        break

                if next_pixel is None:
                    break

                visited_edges.add(edge_key(curr, next_pixel))
                path.append(next_pixel)

                prev, curr = curr, next_pixel
                guard += 1

                if guard > len(pixels) * 2:
                    break

            return path

        nodes = [p for p, d in degree.items() if d != 2]

        for start in sorted(nodes):
            for nxt in neighbors(start):
                if edge_key(start, nxt) in visited_edges:
                    continue

                path = trace_path(start, nxt)

                if len(path) >= SKELETON_MIN_PIXELS:
                    paths.append(path)

        for start in sorted(pixels):
            for nxt in neighbors(start):
                if edge_key(start, nxt) in visited_edges:
                    continue

                path = trace_path(start, nxt)

                if len(path) >= SKELETON_MIN_PIXELS:
                    paths.append(path)

        return paths

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

    def pixel_path_to_robot_path(self, pixel_path, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        robot_path = []

        for px, py in pixel_path:
            rx = draw_min_x + x_offset + (px * scale)
            ry = draw_max_y - y_offset - (py * scale)
            robot_path.append((rx, ry))

        return robot_path

    def robot_to_pixel(self, x, y, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        px = int(round((x - draw_min_x - x_offset) / scale))
        py = int(round((draw_max_y - y_offset - y) / scale))
        return px, py

    def save_draw_debug_images(self, image_path, color_name, mask, skeleton, paths, scale, x_offset, y_offset, draw_min_x, draw_max_y):
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
                os.path.join(DEBUG_OUTPUT_FOLDER, f"{base}_{safe_color}_02_skeleton.png"),
                skeleton,
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
            self.get_logger().warn(f"디버그 이미지 저장 실패: {e}")

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

    def read_stroke_json(self, json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.get_logger().warn(f"stroke JSON 읽기 실패: {e}")
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

    def parse_stroke_point(self, point):
        if isinstance(point, dict):
            if "x" in point and "y" in point:
                return float(point["x"]), float(point["y"])

            if "px" in point and "py" in point:
                return float(point["px"]), float(point["py"])

        if isinstance(point, (list, tuple)) and len(point) >= 2:
            return float(point[0]), float(point[1])

        return None

    def canvas_point_to_robot(self, px, py, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        rx = draw_min_x + x_offset + (px * scale)
        ry = draw_max_y - y_offset - (py * scale)
        return rx, ry

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

    def extract_strokes_from_json(self, image_path, img_w, img_h, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        json_path = self.find_stroke_json_path(image_path)

        if json_path is None:
            return None

        strokes = self.read_stroke_json(json_path)

        if not strokes:
            self.get_logger().warn(f"stroke JSON은 발견됐지만 stroke 데이터가 없습니다: {json_path}")
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

        self.get_logger().info(f"Canvas stroke JSON 사용: {json_path}")
        send_log(f"Canvas stroke JSON 사용: {json_path}")

        for color_name in ["RED", "BLUE", "BLACK"]:
            self.get_logger().info(f"{color_name} stroke JSON path: {len(color_paths[color_name])}개")
            send_log(f"{color_name} stroke JSON path: {len(color_paths[color_name])}개")

        return color_paths

    def extract_strokes_from_contours(self, image_path, img, masks, scale, x_offset, y_offset, draw_min_x, draw_max_y):
        color_paths = {"RED": [], "BLUE": [], "BLACK": []}

        for color_name, mask in masks.items():
            cleaned = self.clean_binary_mask(mask)

            contours, _ = cv2.findContours(
                cleaned,
                cv2.RETR_LIST,
                cv2.CHAIN_APPROX_NONE,
            )

            paths = []

            for contour in contours:
                if contour is None or len(contour) < 2:
                    continue

                area = cv2.contourArea(contour)

                if area < CONTOUR_MIN_AREA_PX and len(contour) < 5:
                    continue

                if CONTOUR_APPROX_EPSILON_PX > 0.0:
                    contour = cv2.approxPolyDP(contour, CONTOUR_APPROX_EPSILON_PX, True)

                pixel_path = []

                for item in contour.reshape(-1, 2):
                    px = float(item[0])
                    py = float(item[1])
                    pixel_path.append((px, py))

                if len(pixel_path) < 2:
                    continue

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
                skeleton_like = np.zeros_like(cleaned)
                self.save_draw_debug_images(
                    image_path,
                    color_name,
                    cleaned,
                    skeleton_like,
                    paths,
                    scale,
                    x_offset,
                    y_offset,
                    draw_min_x,
                    draw_max_y,
                )

            self.get_logger().info(f"{color_name} contour path 추출 완료: {len(paths)}개")

        return color_paths

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

        self.get_logger().warn("stroke JSON이 없어 contour 기반 fallback을 사용합니다.")
        send_log("stroke JSON이 없어 contour 기반 fallback을 사용합니다.", "warn")

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

    def draw_line_paths(self, order_id, color, paths, current_time_spent, total_estimated_time):
        if DEBUG_MODE and DEBUG_MAX_PATHS is not None:
            paths = paths[:DEBUG_MAX_PATHS]
            self.get_logger().warn(
                f"DEBUG_MAX_PATHS={DEBUG_MAX_PATHS}, {color} {len(paths)}개 path만 실행"
            )

        self.get_logger().info(f"{color} 펜 선 그리기 시작. 총 {len(paths)}경로")

        update_robot_status(
            state="DRAWING",
            stage="DRAW",
            pen=color,
            totalPath=len(paths)
        )

        pen_down = False
        last_draw_point = None

        for idx, path in enumerate(paths):
            if len(path) < 2:
                continue

            sx, sy = path[0]
            start_z = self.get_draw_z(sx, sy)
            start_hop_z = self.get_draw_hop_z(sx, sy)

            self.get_logger().info(f"[{color}] {idx + 1}/{len(paths)} path 시작")

            update_robot_status(
                currentPath=idx + 1,
                totalPath=len(paths)
            )

            need_descend = True

            if pen_down and last_draw_point is not None:
                gap_from_last = self.distance(last_draw_point, (sx, sy))

                if gap_from_last <= NO_LIFT_BETWEEN_PATH_GAP_MM:
                    self.get_logger().info(
                        f"[{color}] 이전 path와 {gap_from_last:.3f}mm 거리 → 펜을 올리지 않고 연속 처리"
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

                time.sleep(0.2)

                self.move_to_pos(
                    sx,
                    sy,
                    start_z,
                    vel=DRAW_DESCEND_VEL,
                    acc=DRAW_DESCEND_ACC,
                    radius=0.0,
                    wait_response=True,
                )
                time.sleep(0.15)
                pen_down = True

            self.draw_path_smooth(path)

            ex, ey = path[-1]
            last_draw_point = (ex, ey)

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
                        f"[{color}] 다음 path까지 {next_gap:.3f}mm → 이번 path 끝에서 lift 생략"
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
                time.sleep(0.2)
                pen_down = False

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
                time.sleep(0.2)

            self.move_to_pos(
                last_x,
                last_y,
                SAFE_Z,
                vel=DRAW_LIFT_VEL,
                acc=DRAW_LIFT_ACC,
                radius=0.0,
                wait_response=True,
            )
            time.sleep(0.8)

        return current_time_spent

    def process_and_draw(self, order_id, image_path):
        try:
            if not os.path.exists(image_path):
                self.get_logger().error(f"파일 없음: {image_path}")
                return

            color_paths = self.extract_strokes(image_path)

            total_strokes = sum(len(paths) for paths in color_paths.values())

            if total_strokes == 0:
                self.get_logger().warn("그릴 경로 없음.")
                return

            colors_to_draw = self.get_colors_to_draw(color_paths)

            if not colors_to_draw:
                self.get_logger().warn("실행할 색상 경로가 없습니다.")
                return

            total_estimated_time = 25 + (len(colors_to_draw) * 30) + (total_strokes * 5) + 20
            current_time_spent = 0

            self.update_progress(order_id, 5, total_estimated_time)

            if self.should_run_stage("CASE_PICKUP"):
                self.handle_case_pickup()
            else:
                self.get_logger().info("CASE_PICKUP 단계 건너뜀")

            if self.should_run_stage("CASE_PLACE"):
                self.handle_case_place()
            else:
                self.get_logger().info("CASE_PLACE 단계 건너뜀")

            current_time_spent += 25
            progress_pct = int((current_time_spent / total_estimated_time) * 100)

            self.update_progress(
                order_id,
                progress_pct,
                max(0, total_estimated_time - current_time_spent),
            )

            for color in ["RED", "BLUE", "BLACK"]:
                if color not in colors_to_draw:
                    continue

                paths = color_paths[color]

                if not paths:
                    continue

                if self.should_run_stage("PICKUP_PEN"):
                    self.pickup_pen(color)
                else:
                    self.get_logger().info(f"{color} PICKUP_PEN 단계 건너뜀")

                current_time_spent += 15
                progress_pct = int((current_time_spent / total_estimated_time) * 100)

                self.update_progress(
                    order_id,
                    min(95, progress_pct),
                    max(0, total_estimated_time - current_time_spent),
                )

                if self.should_run_stage("DRAW"):
                    current_time_spent = self.draw_line_paths(
                        order_id,
                        color,
                        paths,
                        current_time_spent,
                        total_estimated_time,
                    )

                    self.get_logger().info(f"{color} 그림 명령 처리 대기")
                    time.sleep(3.0)
                else:
                    self.get_logger().info(f"{color} DRAW 단계 건너뜀")

                if self.should_run_stage("PLACE_PEN"):
                    time.sleep(2.0)  
                    self.place_pen(color)
                else:
                    self.get_logger().info(f"{color} PLACE_PEN 단계 건너뜀")

                current_time_spent += 15
                progress_pct = int((current_time_spent / total_estimated_time) * 100)

                self.update_progress(
                    order_id,
                    min(95, progress_pct),
                    max(0, total_estimated_time - current_time_spent),
                )

            self.update_progress(order_id, 96, 20)

            if self.should_run_stage("FINISHED_CASE_PICKUP"):
                self.handle_finished_case_pickup()
            else:
                self.get_logger().info("FINISHED_CASE_PICKUP 단계 건너뜀")

            if self.should_run_stage("CASE_DROP"):
                self.handle_finished_case_drop()
            else:
                self.get_logger().info("CASE_DROP 단계 건너뜀")

            if self.should_run_stage("HOME"):
                self.get_logger().info("모든 작업 완료. 홈 복귀")
                self.move_to_pos(
                    ROBOT_HOME_X,
                    ROBOT_HOME_Y,
                    SAFE_Z,
                    vel=HOME_RETURN_VEL,
                    acc=HOME_RETURN_ACC,
                    radius=0.0,
                    wait_response=True,
                )
                time.sleep(1.0)
            else:
                self.get_logger().info("HOME 단계 건너뜀")

        except Exception as e:
            self.get_logger().error(f"오류: {e}")

        finally:
            self.complete_order(order_id)
            self.is_drawing = False

    def update_progress(self, order_id, progress, estimated_time):
        try:
            self.cursor.execute(
                "UPDATE orders SET progress=?, estimated_time=? WHERE id=?",
                (int(progress), int(estimated_time), order_id),
            )
            self.conn.commit()

        except Exception as e:
            self.get_logger().error(f"진행률 업데이트 실패: {e}")

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

        self.get_logger().info(f"주문 {order_id} 완료")

        update_robot_status(
            state="IDLE",
            stage="COMPLETE",
            pen="NONE",
            currentPath=0,
            totalPath=0
        )

    


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