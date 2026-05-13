/**
 * sendOrder
 *
 * 클라이언트 캔버스 이미지(imageBase64)와 stroke JSON을 함께 서버로 전송합니다.
 * - strokes가 [[{x,y}], ...] 형태여도 전송
 * - strokes가 [{color, points:[{x,y}]}] 형태여도 전송
 * - 모바일/다른 PC에서 접속해도 localhost 대신 현재 접속한 서버 IP의 5000번 포트로 전송
 * - 로그인 토큰이 localStorage에 있으면 Authorization 헤더도 같이 전송
 */

function getApiBase() {
  if (window.ROBOCASE_API_BASE) {
    return window.ROBOCASE_API_BASE.replace(/\/$/, '');
  }

  const saved = localStorage.getItem('ROBOCASE_API_BASE');
  if (saved) {
    return saved.replace(/\/$/, '');
  }

  const { protocol, hostname, port } = window.location;

  // Flask에서 같은 포트(5000)로 페이지를 열었으면 상대경로 사용
  if (hostname && port === '5000') {
    return '';
  }

  // Vite/정적서버/모바일에서 열었으면 같은 IP의 Flask 5000번으로 전송
  if (hostname) {
    return `${protocol}//${hostname}:5000`;
  }

  // file:// 로 직접 연 경우
  return 'http://127.0.0.1:5000';
}

function getStoredToken() {
  return (
    localStorage.getItem('authToken') ||
    localStorage.getItem('token') ||
    localStorage.getItem('accessToken') ||
    localStorage.getItem('robocase_token') ||
    ''
  );
}

function normalizePoint(point) {
  if (!point) return null;

  if (Array.isArray(point) && point.length >= 2) {
    const x = Number(point[0]);
    const y = Number(point[1]);

    return Number.isFinite(x) && Number.isFinite(y)
      ? { x, y }
      : null;
  }

  if (typeof point === 'object') {
    const x = Number(
      point.x ??
      point.px ??
      point.offsetX ??
      point.clientX ??
      point.cx
    );

    const y = Number(
      point.y ??
      point.py ??
      point.offsetY ??
      point.clientY ??
      point.cy
    );

    return Number.isFinite(x) && Number.isFinite(y)
      ? { x, y }
      : null;
  }

  return null;
}

function normalizeStroke(stroke) {
  if (!stroke) return null;

  // [[{x,y}, {x,y}], ...] 안의 한 stroke
  if (Array.isArray(stroke)) {
    const points = stroke
      .map(normalizePoint)
      .filter(Boolean);

    return points.length >= 2
      ? {
          color: '#000000',
          points,
        }
      : null;
  }

  // [{ color, points:[...] }] 형태
  // 또는 { strokeStyle, path:[...] } 형태
  if (typeof stroke === 'object') {
    const rawPoints =
      stroke.points ||
      stroke.path ||
      stroke.coords ||
      stroke.data ||
      [];

    if (!Array.isArray(rawPoints)) {
      return null;
    }

    const points = rawPoints
      .map(normalizePoint)
      .filter(Boolean);

    if (points.length < 2) {
      return null;
    }

    return {
      color:
        stroke.color ||
        stroke.strokeStyle ||
        stroke.strokeColor ||
        stroke.pen ||
        '#000000',
      points,
    };
  }

  return null;
}

function buildStrokeData(strokes, canvas) {
  if (!Array.isArray(strokes)) {
    return null;
  }

  const normalized = strokes
    .map(normalizeStroke)
    .filter(Boolean);

  if (normalized.length === 0) {
    return null;
  }

  return {
    canvasWidth: canvas.width,
    canvasHeight: canvas.height,
    strokes: normalized,
  };
}

const sendOrder = async ({
  model = 'iPhone 15 Plus',
  caseType = 'clear',
  caseColor = 'black',
  totalPrice = 35000,
  canvas = null,
  strokes = null,
} = {}) => {
  if (!canvas) {
    throw new Error('canvas element is required');
  }

  const imageBase64 = canvas.toDataURL('image/png');

  const strokeData = buildStrokeData(strokes, canvas);

  const payload = {
    model,
    caseType,
    caseColor,
    totalPrice,
    imageBase64,
    strokeData,
  };

  console.log('[sendOrder] 전송 payload:', {
    model,
    caseType,
    caseColor,
    totalPrice,
    imageBase64Length: imageBase64.length,
    strokeCount: strokeData?.strokes?.length || 0,
    apiBase: getApiBase() || '(same-origin)',
  });

  const token = getStoredToken();

  const headers = {
    'Content-Type': 'application/json',
  };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${getApiBase()}/api/orders`, {
    method: 'POST',
    headers,
    credentials: 'include',
    body: JSON.stringify(payload),
  });

  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    console.error('[sendOrder] 서버 오류:', res.status, data);
    throw new Error(data.error || `Server error: ${res.status}`);
  }

  console.log('[sendOrder] 서버 응답:', data);

  if (data.strokeJsonSaved) {
    console.log(
      `[sendOrder] stroke JSON 저장 완료: ${data.strokeJsonFile}, strokes=${data.strokeCount}`
    );
  } else {
    console.warn(
      '[sendOrder] stroke JSON 저장 안 됨. strokes 배열 전달 여부를 확인하세요.'
    );
  }

  return data;
};

// 전역에서 호출할 수 있게 노출
window.sendOrder = sendOrder;
