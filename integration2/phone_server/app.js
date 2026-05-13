/**
 * sendOrder
 *
 * canvas 위에 그린 stroke 데이터를 imageBase64와 함께 서버로 전송합니다.
 * 서버는 strokeData를 {이미지파일명}_strokes.json 으로 저장하고,
 * robot_drawer.py 가 해당 파일을 읽어 실제 로봇 경로로 변환합니다.
 *
 * strokeData 형식:
 *   {
 *     canvasWidth:  <number>,   // 캔버스 픽셀 너비
 *     canvasHeight: <number>,   // 캔버스 픽셀 높이
 *     strokes: [                // 사용자가 그린 선 배열
 *       [
 *         { x: <number>, y: <number> },  // 각 점의 좌표
 *         ...
 *       ],
 *       ...
 *     ]
 *   }
 *
 * strokes 가 없거나 비어있으면 strokeData 는 null 로 전송됩니다.
 * 서버는 strokeData 가 null 이면 JSON 파일을 생성하지 않고,
 * 로봇은 이미지 contour 기반 fallback 으로 동작합니다.
 */
const sendOrder = async ({
  model       = "iphone14",
  caseType    = "hard",
  caseColor   = "black",
  totalPrice  = 12000,
  canvas      = null,   // HTMLCanvasElement
  strokes     = null,   // 2D 배열: [[{x,y}, ...], ...]  ← app 에서 관리하는 stroke 상태
} = {}) => {

  // ── 1. 이미지 base64 추출 ────────────────────────────────────────────────
  if (!canvas) {
    throw new Error("canvas element is required");
  }
  const imageBase64 = canvas.toDataURL("image/png");

  // ── 2. strokeData 구성 ──────────────────────────────────────────────────
  // strokes 배열이 실제로 점을 가진 선을 하나 이상 포함할 때만 전송합니다.
  let strokeData = null;
  const hasStrokes =
    Array.isArray(strokes) &&
    strokes.length > 0 &&
    strokes.some((stroke) => Array.isArray(stroke) && stroke.length > 0);

  if (hasStrokes) {
    strokeData = {
      canvasWidth:  canvas.width,
      canvasHeight: canvas.height,
      strokes:      strokes,   // [[{x,y}, ...], ...]
    };
  }

  // ── 3. 서버로 전송 ────────────────────────────────────────────────────────
  const payload = {
    model,
    caseType,
    caseColor,
    totalPrice,
    imageBase64,
    strokeData,   // null 또는 { canvasWidth, canvasHeight, strokes }
  };

  const res = await fetch("http://localhost:5000/api/orders", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}));
    throw new Error(errBody.error || `Server error: ${res.status}`);
  }

  const data = await res.json();
  console.log("[sendOrder] 서버 응답:", data);

  // data.strokeJsonSaved === true  → 로봇이 JSON 경로로 동작
  // data.strokeJsonSaved === false → 로봇이 이미지 contour fallback 으로 동작
  if (data.strokeJsonSaved) {
    console.log("[sendOrder] stroke JSON 저장 완료 → 로봇이 stroke JSON 경로로 동작합니다.");
  } else {
    console.log("[sendOrder] stroke 데이터 없음 → 로봇이 이미지 contour fallback 으로 동작합니다.");
  }

  return data;   // { success, order_id, strokeJsonSaved }
};
