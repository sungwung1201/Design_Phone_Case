// ==================================================
// ROBOCASE Phone UI
// - 사용자가 기종/케이스 옵션을 고르고, Canvas에서 도안을 만든 뒤 서버로 주문을 전송하는 프론트엔드입니다.
// - 직접 그린 선은 strokeData로 구조화해서 보내고, 최종 미리보기 이미지는 imageBase64로 함께 보냅니다.
// - 서버가 주문을 DB에 저장하면 상태 화면에서 주문/로봇 상태를 polling하며 진행률, 취소, 충격 정지를 보여줍니다.
// ==================================================

// 서버 주문 전송 API 설정입니다.
// 현재 앱은 이 주소로 로그인, 주문 생성, 주문 상태 조회, 로봇 상태 조회를 모두 요청합니다.
const API_BASE_URL = "http://192.168.10.92:5000";
// 로그인 성공 시 서버에서 받은 JWT/token을 브라우저 localStorage에 저장할 때 사용하는 key입니다.
const AUTH_TOKEN_KEY = "robocaseAuthToken";

// 주문 취소를 허용하는 최대 진행률입니다.
// 이 값보다 진행률이 높으면 로봇 작업이 많이 진행된 상태로 보고 UI에서 취소 버튼을 잠급니다.
const CANCEL_PROGRESS_LIMIT = 31;

// 앱 화면에서 "로봇이 실제로 그릴 수 있는 영역"을 케이스 프레임 대비 비율로 정의합니다.
// CSS의 safe area 표시와 같은 비율을 쓰며, stroke를 서버로 보낼 때 이 영역 기준으로 좌표를 다시 정규화합니다.
const SAFE_AREA_RATIO = {
    x: 0.11,
    y: 0.18,
    width: 0.78,
    height: 0.66
};

// localStorage에 저장된 인증 토큰을 읽습니다.
// 토큰이 없으면 빈 문자열을 반환해서 Authorization 헤더를 붙이지 않게 합니다.
function getAuthToken() {
    return localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

// 로그인/로그아웃 시 인증 토큰을 저장하거나 제거합니다.
// token이 falsy이면 로그아웃 상태로 보고 localStorage에서 삭제합니다.
function setAuthToken(token) {
    if (token) {
        localStorage.setItem(AUTH_TOKEN_KEY, token);
    } else {
        localStorage.removeItem(AUTH_TOKEN_KEY);
    }
}

// 서버 API 호출 공통 래퍼입니다.
// 모든 요청에 JSON Content-Type을 붙이고, 로그인 토큰이 있으면 Authorization 헤더도 자동으로 붙입니다.
async function apiFetch(path, options = {}) {
    const token = getAuthToken();
    const response = await fetch(`${API_BASE_URL}${path}`, {
        ...options,
        headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            'Content-Type': 'application/json',
            ...(options.headers || {})
        }
    });

    const data = await response.json().catch(() => ({}));
    return { response, data };
}

// stroke point 하나를 서버가 읽기 쉬운 { x, y } 형태로 통일합니다.
// 앱 내부 point, 배열 point, 다른 라이브러리식 px/py/offsetX/clientX 형태까지 방어적으로 처리합니다.
function normalizeStrokePointForServer(point) {
    if (!point) return null;

    if (Array.isArray(point) && point.length >= 2) {
        const x = Number(point[0]);
        const y = Number(point[1]);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    }

    if (typeof point === 'object') {
        const x = Number(point.x ?? point.px ?? point.offsetX ?? point.clientX);
        const y = Number(point.y ?? point.py ?? point.offsetY ?? point.clientY);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    }

    return null;
}

// 캔버스 내부 pixel 기준 safe area 사각형을 계산합니다.
// canvas.width/height는 CSS 크기보다 2배로 잡혀 있으므로, 서버 전송용 좌표도 실제 canvas pixel 기준을 사용합니다.
function getCanvasSafeAreaPixels(canvas) {
    if (!canvas) return null;
    return {
        x: canvas.width * SAFE_AREA_RATIO.x,
        y: canvas.height * SAFE_AREA_RATIO.y,
        width: canvas.width * SAFE_AREA_RATIO.width,
        height: canvas.height * SAFE_AREA_RATIO.height
    };
}

// 메인 캔버스 전체 좌표로 저장된 점을 "safe area만 잘라낸 뒤 전체 캔버스에 다시 펼친 좌표"로 변환합니다.
// 이유: 로봇 코드는 받은 이미지 전체를 draw area에 맞춰 변환하므로, 앱의 safe area 입력을 로봇 draw bounds와 맞추기 위해서입니다.
// safe area 밖의 점은 로봇이 그리면 안 되는 영역이므로 null로 버립니다.
function remapSafeAreaPointForServer(point, safeArea, canvas) {
    if (!point || !safeArea || !canvas) return null;

    const x = Number(point.x);
    const y = Number(point.y);

    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;

    const nx = (x - safeArea.x) / safeArea.width;
    const ny = (y - safeArea.y) / safeArea.height;

    if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return null;

    return {
        x: nx * canvas.width,
        y: ny * canvas.height
    };
}

// stroke 하나를 서버/로봇이 사용할 수 있는 구조로 정리합니다.
// 한 stroke는 "색상 + 브러시 굵기 + 좌표 목록"이며, 좌표는 safe area 기준으로 필터링/재매핑됩니다.
function normalizeStrokeForServer(stroke, safeArea, canvas) {
    if (!stroke) return null;

    let rawPoints = [];

    if (Array.isArray(stroke)) {
        rawPoints = stroke;
    } else if (typeof stroke === 'object') {
        rawPoints = stroke.points || stroke.path || stroke.coords || [];
    }

    if (!Array.isArray(rawPoints)) return null;

    const points = rawPoints
        .map(normalizeStrokePointForServer)
        .map(point => remapSafeAreaPointForServer(point, safeArea, canvas))
        .filter(Boolean);

    if (points.length < 2) return null;

    return {
        color: stroke.color || stroke.strokeStyle || stroke.strokeColor || stroke.pen || '#111111',
        size: Number(stroke.size || stroke.lineWidth || 5),
        points
    };
}

// 앱에 누적된 여러 stroke를 하나의 strokeData payload로 묶습니다.
// canvasWidth/canvasHeight를 같이 보내야 서버와 로봇이 이 좌표가 어떤 캔버스 크기 기준인지 알 수 있습니다.
function buildStrokeDataForServer(canvas, strokes) {
    if (!canvas || !Array.isArray(strokes)) return null;

    const safeArea = getCanvasSafeAreaPixels(canvas);
    const normalizedStrokes = strokes
        .map(stroke => normalizeStrokeForServer(stroke, safeArea, canvas))
        .filter(Boolean);

    if (normalizedStrokes.length === 0) return null;

    return {
        canvasWidth: canvas.width,
        canvasHeight: canvas.height,
        coordinateMode: "safe-area-remapped-to-canvas",
        safeArea,
        strokes: normalizedStrokes
    };
}

// 서버로 보낼 수 있는 실제 stroke가 있는지 확인합니다.
// 비어 있는 strokeData를 보냈다가 서버/로봇이 잘못된 JSON으로 해석하지 않게 하기 위한 guard입니다.
function hasDrawableStrokeData(strokeData) {
    return Boolean(
        strokeData &&
        Array.isArray(strokeData.strokes) &&
        strokeData.strokes.length > 0
    );
}

// 주문 생성 API 호출입니다.
// imageBase64는 사람이 보는 최종 미리보기/저장용이고, strokeData는 로봇이 따라 그릴 경로용입니다.
// 직접 그린 도안이라면 서버가 stroke JSON 저장을 확인해 줘야 주문 성공으로 처리합니다.
async function uploadOrderToServer(dataUrl, orderInfo) {
    app.showToast("서버로 주문 정보를 전송 중입니다...");
    console.log("uploadOrderToServer - Starting, API_BASE_URL:", API_BASE_URL);

    try {
        const canvas = app.canvasManager.canvas || document.getElementById('drawing-canvas');

        const strokeData = buildStrokeDataForServer(canvas, store.canvas.strokes);
        const shouldRequireStrokeJson = hasDrawableStrokeData(strokeData) && !store.canvas.containsRasterContent;

        // 서버의 기본 계약은 strokeData이지만, 서버 버전 차이를 줄이기 위해 stroke_data/strokes alias도 함께 넣습니다.
        // 최신 서버는 strokeData를 JSON 파일로 저장하고, 로봇은 이미지 파일명과 같은 .json 파일을 찾아 먼저 사용합니다.
        const payload = {
            model: orderInfo.model,
            caseType: orderInfo.caseType,
            caseColor: orderInfo.caseColor,
            totalPrice: orderInfo.totalPrice,
            imageBase64: dataUrl,
            strokeData: strokeData
        };

        if (hasDrawableStrokeData(strokeData)) {
            payload.stroke_data = strokeData;
            payload.strokes = strokeData.strokes;
            payload.canvasWidth = strokeData.canvasWidth;
            payload.canvasHeight = strokeData.canvasHeight;
        }

        // 디버깅용 로그입니다.
        // 브라우저 콘솔에서 strokeCount가 1 이상이고 서버 응답의 strokeJsonSaved가 true인지 확인하면 됩니다.
        console.log("========== ORDER PAYLOAD DEBUG ==========");
        console.log("containsRasterContent:", store.canvas.containsRasterContent);
        console.log("shouldRequireStrokeJson:", shouldRequireStrokeJson);
        console.log("raw strokes:", store.canvas.strokes);
        console.log("strokeData:", strokeData);
        console.log("strokeCount:", strokeData?.strokes?.length || 0);
        console.log("payload size:", JSON.stringify(payload).length);
        console.log("send to:", `${API_BASE_URL}/api/orders`);
        console.log("=========================================");

        const { response, data } = await apiFetch('/api/orders', {
            method: 'POST',
            body: JSON.stringify(payload)
        });

        console.log("uploadOrderToServer - Response received, status:", response.status);
        console.log("uploadOrderToServer - Response data:", data);

        if (!response.ok) {
            const errorMsg = data.error || `서버 응답 오류 (${response.status})`;
            console.error("uploadOrderToServer - Server error:", errorMsg);
            return errorMsg;
        }

        // 직접 그린 stroke가 있는데 서버가 JSON 저장 확인을 주지 않으면 로봇은 contour fallback으로 빠집니다.
        // 그래서 이 경우 앱에서 주문 성공으로 넘기지 않고 바로 문제를 드러냅니다.
        if (shouldRequireStrokeJson && data.strokeJsonSaved !== true) {
            const errorMsg = "서버가 stroke JSON 저장을 확인하지 않았습니다. 서버 app.py가 최신 코드인지 확인해 주세요.";
            console.error("uploadOrderToServer - Stroke JSON save missing:", data);
            return errorMsg;
        }

        if (hasDrawableStrokeData(strokeData) && !data.strokeJsonSaved) {
            console.warn("⚠ stroke JSON이 서버에 저장되지 않았습니다. strokeCount:", strokeData?.strokes?.length || 0);
        } else {
            console.log("✅ stroke JSON 저장 완료:", data.strokeJsonFile || "(filename not returned)");
        }

        console.log("✓ uploadOrderToServer - Success, order_id:", data.order_id);

        store.currentOrderId = data.order_id;
        store.impactStopPopupShown = false;
        store.orderStatus = null;
        store.robotStatus = null;
        sessionStorage.setItem('currentOrderId', data.order_id);

        return true;

    } catch (error) {
        const errorMsg = error.message || "알 수 없는 오류";
        console.error("✗ uploadOrderToServer - Catch error:", errorMsg, error);

        if (errorMsg.includes('Failed to fetch')) {
            return `서버에 연결할 수 없습니다. (${API_BASE_URL})`;
        } else if (errorMsg.includes('timeout')) {
            return "서버 응답 시간 초과";
        } else {
            return "서버 전송 오류: " + errorMsg;
        }
    }
}

// 지원 기종 목록입니다.
// 현재 UI에서는 Apple/Samsung 아코디언으로 보여 주고, 선택값은 store.order.model에 저장합니다.
const MODELS = {
    apple: ["iPhone 15 Pro Max", "iPhone 15 Pro", "iPhone 15 Plus", "iPhone 15", "iPhone 14 Pro Max", "iPhone 14 Pro", "iPhone 14 Plus", "iPhone 14", "iPhone 13 Pro", "iPhone 13", "iPhone 12", "iPhone 11", "iPhone X"],
    samsung: ["Galaxy S24 Ultra", "Galaxy S24+", "Galaxy S24", "Galaxy S23 Ultra", "Galaxy S23+", "Galaxy S23", "Galaxy S22 Ultra", "Galaxy S22", "Galaxy S21 Ultra", "Galaxy S21"]
};

// 케이스 타입과 추가 가격입니다.
// 선택 시 totalPrice 계산에 반영됩니다.
const CASE_TYPES = [
    { id: "clear", label: "Clear", price: 0 },
    { id: "opaque", label: "Matte", price: 1000 },
    { id: "translucent", label: "Translucent", price: 2000 }
];

// 범퍼/케이스 색상 옵션입니다.
const BUMPER_COLORS = [
    { id: "black", label: "Black" },
    { id: "white", label: "White" },
    { id: "pink", label: "Pink" }
];

// 인라인 SVG 템플릿을 img src로 바로 사용할 수 있게 data URI로 변환합니다.
const svgToDataUri = (svgText) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgText)}`;

// 기본 제공 고스트 템플릿입니다.
// 직접 stroke가 아니라 이미지/템플릿으로 취급되므로, 적용하면 containsRasterContent=true가 됩니다.
const GHOST_TEMPLATE_SVG = svgToDataUri(`
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 260 260">
  <rect width="260" height="260" fill="none"/>
  <path d="M66 223V94c0-42 28-71 64-71s64 29 64 71v129l-21-18-21 18-22-18-22 18-21-18-21 18Z" fill="#fff" stroke="#111827" stroke-width="12" stroke-linejoin="round"/>
  <path d="M93 112c0-12 8-21 18-21s18 9 18 21" fill="none" stroke="#111827" stroke-width="11" stroke-linecap="round"/>
  <path d="M131 112c0-12 8-21 18-21s18 9 18 21" fill="none" stroke="#111827" stroke-width="11" stroke-linecap="round"/>
  <path d="M101 151c20 18 42 18 60 0" fill="none" stroke="#ff3366" stroke-width="10" stroke-linecap="round"/>
</svg>
`);

const GHOST_IMAGE_DATA_URI = "data:image/png;base64,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";

// 사용자가 도안 화면에서 고를 수 있는 기본 템플릿 목록입니다.
const DESIGN_TEMPLATES = [
    { id: "ghost", label: "Ghost", src: GHOST_TEMPLATE_SVG }
];

// Feather icon은 HTML이 렌더링된 뒤 replace()를 다시 호출해야 실제 SVG 아이콘으로 바뀝니다.
const refreshIcons = () => {
    if (window.feather && typeof window.feather.replace === 'function') {
        window.feather.replace();
    }
};

// 이미지 경로 또는 data URI를 PNG dataURL로 변환합니다.
// 템플릿/외부 이미지가 서버로 갈 때 JSON payload 안에 imageBase64로 실릴 수 있게 만드는 보조 함수입니다.
const imageSrcToDataUrl = (src) => new Promise((resolve, reject) => {
    console.log("imageSrcToDataUrl - Starting conversion for:", src);
    const img = new Image();
    img.onload = () => {
        try {
            console.log("imageSrcToDataUrl - Image loaded, size:", img.width, "x", img.height);
            const c = document.createElement('canvas');
            c.width = img.naturalWidth || img.width || 1;
            c.height = img.naturalHeight || img.height || 1;
            console.log("imageSrcToDataUrl - Canvas size:", c.width, "x", c.height);
            
            const ctx = c.getContext('2d');
            if (!ctx) {
                throw new Error("Canvas context unavailable");
            }
            ctx.fillStyle = "#ffffff";
            ctx.fillRect(0, 0, c.width, c.height);
            ctx.drawImage(img, 0, 0);
            
            const dataUrl = c.toDataURL("image/png");
            if (!dataUrl || !dataUrl.startsWith('data:image')) {
                throw new Error("toDataURL returned invalid data: " + (dataUrl ? dataUrl.substring(0, 50) : "null"));
            }
            
            console.log("✓ imageSrcToDataUrl - Success, size:", dataUrl.length);
            resolve(dataUrl);
        } catch (e) {
            console.error("✗ imageSrcToDataUrl - Processing error:", e.message);
            reject(e);
        }
    };
    img.onerror = () => {
        const err = new Error(`Image load failed for: ${src}`);
        console.error("✗ imageSrcToDataUrl - Load error:", err.message);
        reject(err);
    };
    img.onabort = () => {
        const err = new Error(`Image load aborted for: ${src}`);
        console.error("✗ imageSrcToDataUrl - Load aborted:", err.message);
        reject(err);
    };
    img.src = src;
});

// 전역 앱 상태입니다.
// 이 프로젝트는 별도 프레임워크 없이 Vanilla JS SPA로 동작하므로, 화면 상태와 주문 상태를 이 store 객체에 모아 둡니다.
const store = {
    activeAccordion: null,
    currentOrderId: null,
    statusPollTimer: null,
    authMode: 'login',
    auth: {
        user: null
    },
    orderStatus: null,
    robotStatus: null,
    impactStopPopupShown: false,
    order: {
        // 사용자가 선택한 주문 옵션과 최종 도안 미리보기 이미지입니다.
        model: null,
        caseType: "clear",
        caseColor: "black",
        totalPrice: 35000,
        designDataUrl: null,
        selectedTemplateSrc: null,
    },
    canvas: {
        // 현재 브러시 색상/굵기와 Canvas 편집 상태입니다.
        color: '#111111',
        size: 5,
        isDrawing: false,
        // history는 Undo용 이미지 스냅샷, strokes는 로봇 경로 전송용 구조화 좌표입니다.
        history: [],
        strokes: [],
        currentStroke: null,
        // true이면 업로드 이미지/템플릿 같은 raster 이미지가 섞인 상태라 strokeData만으로 표현할 수 없습니다.
        containsRasterContent: false,
    }
};

// 모든 화면 위에 공통으로 붙는 상단 네비게이션입니다.
// 로그인 상태이면 회원정보 버튼을 보여 줍니다.
const TopNav = (showBack = true) => `
    <header class="top-nav">
        ${showBack ? '<button class="back-btn" onclick="app.goBack()"><i data-feather="chevron-left"></i> 뒤로</button>' : '<div class="nav-placeholder"></div>'}
        <div class="logo">ROBOCASE</div>
        ${store.auth.user
            ? '<button class="back-btn" onclick="app.navigate(\'account\')" style="justify-content:flex-end;"><i data-feather="user"></i> 회원정보</button>'
            : '<div class="nav-placeholder"></div>'}
    </header>
`;

// 화면별 HTML 템플릿 모음입니다.
// app.render(route)가 현재 route에 맞는 템플릿을 골라 #app에 삽입합니다.
const Views = {
    Main: () => `
        <div id="view-container" class="fade-in" style="
            height: 100vh; display: flex; flex-direction: column;
            justify-content: center; align-items: center; text-align: center;
            padding: 2rem; background: linear-gradient(160deg, #fff 60%, #fff0f3 100%);
        ">
            <div style="
                width: 80px; height: 80px; border-radius: 24px;
                background: linear-gradient(135deg, #ff3366, #ff6b3d);
                display: flex; align-items: center; justify-content: center;
                margin-bottom: 1.5rem;
                box-shadow: 0 12px 40px rgba(255,51,102,.3);
                font-size: 2.2rem;
            "><i data-feather="edit-3"></i></div>
            <div class="logo" style="font-size: 2.8rem; margin-bottom: 0.75rem;">ROBOCASE</div>
            <p style="color: var(--text2); font-size: 1rem; line-height: 1.7; max-width: 280px; margin-bottom: 0.5rem;">
                나만의 커스텀 폰케이스를<br><strong style="color:var(--text);">두산 로봇팔</strong>이 직접 그려드립니다.
            </p>
            <div style="display:flex; gap:1rem; margin: 1.5rem 0 2.5rem; flex-wrap:wrap; justify-content:center;">
                <span style="font-size:0.82rem; color:var(--text2); font-weight:600;">직접 도안</span>
                <span style="font-size:0.82rem; color:var(--text2); font-weight:600;">로봇 드로잉</span>
                <span style="font-size:0.82rem; color:var(--text2); font-weight:600;">즉시 수령</span>
            </div>
            <button class="btn btn-primary" onclick="app.navigate('login')" style="
                width: 85%; max-width: 320px;
                background: linear-gradient(135deg, #111827, #374151);
                padding: 1.1rem; font-size: 1rem;
            ">
                주문 시작하기
            </button>
            <p style="margin-top:1rem; font-size:0.78rem; color:var(--text3);">
                기본 가격 ₩35,000 · 도안 직접 선택 가능
            </p>
        </div>
    `,

    Login: () => `
        ${TopNav(false)}
        <div id="view-container" class="fade-in">
            <div style="text-align:center; margin-bottom:2rem;">
                <h1 class="view-title">시작하기</h1>
                <p class="view-subtitle">가입된 회원만 주문할 수 있습니다. 처음이면 회원가입 후 진행해 주세요.</p>
            </div>
            <div style="background:var(--surface); border:1.5px solid var(--border); border-radius:var(--radius-xl); padding:1.75rem; box-shadow:var(--shadow-md);">
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.6rem; margin-bottom:1.25rem;">
                    <button class="btn ${store.authMode === 'login' ? 'btn-primary' : 'btn-secondary'}" style="padding:0.85rem;" onclick="app.setAuthMode('login')">로그인</button>
                    <button class="btn ${store.authMode === 'signup' ? 'btn-primary' : 'btn-secondary'}" style="padding:0.85rem;" onclick="app.setAuthMode('signup')">회원가입</button>
                </div>
                <div class="form-group" style="${store.authMode === 'signup' ? '' : 'display:none;'}">
                    <label>이름</label>
                    <input id="auth-name" type="text" class="form-input" placeholder="홍길동">
                </div>
                <div class="form-group" style="${store.authMode === 'signup' ? '' : 'display:none;'}">
                    <label>연락처</label>
                    <input id="auth-phone" type="tel" class="form-input" placeholder="010-1234-5678">
                </div>
                <div class="form-group" style="${store.authMode === 'signup' ? '' : 'display:none;'}">
                    <label>우편번호</label>
                    <input id="auth-postal-code" type="text" class="form-input" placeholder="06236">
                </div>
                <div class="form-group" style="${store.authMode === 'signup' ? '' : 'display:none;'}">
                    <label>주소</label>
                    <input id="auth-address1" type="text" class="form-input" placeholder="서울시 강남구 테헤란로 123">
                </div>
                <div class="form-group" style="${store.authMode === 'signup' ? '' : 'display:none;'}">
                    <label>상세 주소</label>
                    <input id="auth-address2" type="text" class="form-input" placeholder="101동 1203호">
                </div>
                <div class="form-group">
                    <label>이메일</label>
                    <input id="auth-email" type="email" class="form-input" placeholder="admin@example.com" value="${store.authMode === 'login' ? 'admin@example.com' : ''}">
                </div>
                <div class="form-group">
                    <label>비밀번호</label>
                    <input id="auth-password" type="password" class="form-input" placeholder="6자 이상" value="${store.authMode === 'login' ? '123456' : ''}">
                </div>
                <button class="btn btn-primary" style="margin-top:1rem;" onclick="app.submitAuth()">
                    ${store.authMode === 'login' ? '로그인하고 시작하기' : '회원가입하고 시작하기'}
                </button>
            </div>
        </div>
    `,

    Account: () => {
        const user = store.auth.user || {};
        return `
        ${TopNav(true)}
        <div id="view-container" class="fade-in">
            <div style="text-align:center; margin-bottom:1.5rem;">
                <h2 class="view-title">회원정보</h2>
                <p class="view-subtitle">주문 전에 배송지와 연락처를 바로 수정할 수 있습니다.</p>
            </div>
            <div style="background:var(--surface); border:1.5px solid var(--border); border-radius:var(--radius-xl); padding:1.75rem; box-shadow:var(--shadow-md);">
                <div class="form-group">
                    <label>이름</label>
                    <input id="profile-name" type="text" class="form-input" placeholder="홍길동" value="${user.name || ''}">
                </div>
                <div class="form-group">
                    <label>이메일</label>
                    <input type="email" class="form-input" value="${user.email || ''}" disabled>
                </div>
                <div class="form-group">
                    <label>연락처</label>
                    <input id="profile-phone" type="tel" class="form-input" placeholder="010-1234-5678" value="${user.phone || ''}">
                </div>
                <div class="form-group">
                    <label>우편번호</label>
                    <input id="profile-postal-code" type="text" class="form-input" placeholder="06236" value="${user.postalCode || ''}">
                </div>
                <div class="form-group">
                    <label>주소</label>
                    <input id="profile-address1" type="text" class="form-input" placeholder="서울시 강남구 테헤란로 123" value="${user.address1 || ''}">
                </div>
                <div class="form-group">
                    <label>상세 주소</label>
                    <input id="profile-address2" type="text" class="form-input" placeholder="101동 1203호" value="${user.address2 || ''}">
                </div>
                <div style="display:grid; grid-template-columns:1fr; gap:0.75rem; margin-top:1rem;">
                    <button class="btn btn-primary" onclick="app.saveProfile()">정보 저장</button>
                    <button class="btn btn-secondary" onclick="app.logout()">로그아웃</button>
                </div>
            </div>
        </div>
        `;
    },

    OrderStep1: () => {
        const calculatePrice = () => {
            const type = CASE_TYPES.find(t => t.id === store.order.caseType);
            store.order.totalPrice = 35000 + (type ? type.price : 0);
            return store.order.totalPrice.toLocaleString();
        };

        const canProceed = store.order.model && store.order.caseType && store.order.caseColor;

        return `
        ${TopNav(true)}
        <div id="view-container" class="fade-in">
            <h2 class="view-title">기기 선택</h2>
            <p class="view-subtitle">사용 중인 기종을 선택해 주세요.</p>
            
            <!-- Apple Accordion -->
            <div class="accordion ${store.activeAccordion === 'apple' ? 'active' : ''}">
                <div class="accordion-header apple" onclick="app.toggleAccordion('apple')">
                    <span>Apple (iPhone)</span>
                    <i data-feather="chevron-down" class="accordion-icon"></i>
                </div>
                <div class="accordion-content">
                    <ul class="model-list">
                        ${MODELS.apple.map(model => `
                            <li class="model-item ${store.order.model === model ? 'selected' : ''}" onclick="app.setOrderData('model', '${model}')">
                                ${model}
                                ${store.order.model === model ? '<i data-feather="check"></i>' : ''}
                            </li>
                        `).join('')}
                    </ul>
                </div>
            </div>

            <!-- Samsung Accordion -->
            <div class="accordion ${store.activeAccordion === 'samsung' ? 'active' : ''}">
                <div class="accordion-header samsung" onclick="app.toggleAccordion('samsung')">
                    <span>Samsung (Galaxy)</span>
                    <i data-feather="chevron-down" class="accordion-icon"></i>
                </div>
                <div class="accordion-content">
                    <ul class="model-list">
                        ${MODELS.samsung.map(model => `
                            <li class="model-item ${store.order.model === model ? 'selected' : ''}" onclick="app.setOrderData('model', '${model}')">
                                ${model}
                                ${store.order.model === model ? '<i data-feather="check"></i>' : ''}
                            </li>
                        `).join('')}
                    </ul>
                </div>
            </div>

            <div style="${store.order.model ? 'display:block; animation: fadeIn 0.5s ease;' : 'display:none;'}">
                <h3 class="section-title">케이스 타입</h3>
                <div class="chip-group">
                    ${CASE_TYPES.map(type => `
                        <div class="chip ${store.order.caseType === type.id ? 'selected' : ''}" onclick="app.setOrderData('caseType', '${type.id}')">
                            ${type.label}
                            <span class="price">+ &#8361;${type.price.toLocaleString()}</span>
                        </div>
                    `).join('')}
                </div>

                <h3 class="section-title">범퍼 색상</h3>
                <div class="chip-group">
                    ${BUMPER_COLORS.map(color => `
                        <div class="chip ${store.order.caseColor === color.id ? 'selected' : ''}" onclick="app.setOrderData('caseColor', '${color.id}')" style="padding: 0.5rem 1.5rem;">
                            ${color.label}
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>
        
        <div class="bottom-bar fade-in">
            <div class="price-info">
                <span class="price-label">총 결제 금액</span>
                <span class="price-amount">&#8361;${calculatePrice()}</span>
            </div>
            <button class="btn btn-primary" onclick="app.navigate('orderStep2')" ${!canProceed ? 'disabled' : ''}>
                다음 단계
            </button>
        </div>
        `;
    },

    OrderStep2: () => `
        ${TopNav(true)}
        <div id="view-container" class="fade-in design-view">
            <h2 class="view-title">도안 그리기</h2>
            <p class="view-subtitle">이미지를 첨부하거나 직접 그릴 수 있습니다. 첨부한 이미지는 드로잉 가능 영역 안에 맞춰 배치됩니다.</p>
            <div class="canvas-wrapper design-workspace">
                <div class="canvas-stage">
                    <div class="canvas-container">
                        <div class="camera-hole-guide"></div>
                        <div class="robot-safe-area"></div>
                        <canvas id="drawing-canvas"></canvas>
                    </div>
                </div>
                <div class="upload-hint">
                    <strong>이미지 첨부</strong>
                    <span>사진, PNG, 일러스트를 넣으면 자동으로 드로잉 가능 영역에 맞춰집니다.</span>
                </div>
                <div class="canvas-tools">
                    <div class="color-palette">
                        <div class="color-swatch ${store.canvas.color === '#111111' ? 'selected' : ''}" style="background:#111111" data-color="#111111" onclick="app.setBrushColor('#111111')"></div>
                        <div class="color-swatch ${store.canvas.color === '#ff0000' ? 'selected' : ''}" style="background:#ff0000" data-color="#ff0000" onclick="app.setBrushColor('#ff0000')"></div>
                        <div class="color-swatch ${store.canvas.color === '#0000ff' ? 'selected' : ''}" style="background:#0000ff" data-color="#0000ff" onclick="app.setBrushColor('#0000ff')"></div>
                        <input type="hidden" id="brush-color" value="${store.canvas.color}">
                    </div>
                    <input type="range" id="brush-size" min="1" max="20" value="${store.canvas.size}">
                    <input type="file" id="image-upload" accept="image/*" style="display:none;" onchange="app.canvasManager.loadImage(event)">
                    <button class="tool-btn tool-btn-wide" onclick="app.canvasManager.openExpandedEditor()" title="크게 그리기"><i data-feather="maximize-2"></i><span>크게 그리기</span></button>
                    <button class="tool-btn" onclick="document.getElementById('image-upload').click()" title="이미지 첨부"><i data-feather="image"></i></button>
                    <button class="tool-btn" onclick="app.canvasManager.undo()" title="실행 취소"><i data-feather="corner-up-left"></i></button>
                    <button class="tool-btn" onclick="app.canvasManager.clear()" title="전체 지우기"><i data-feather="trash-2"></i></button>
                </div>
            </div>
        </div>
        <div class="bottom-bar fade-in">
            <div class="price-info">
                <span class="price-label">총 결제 금액</span>
                <span class="price-amount">&#8361;${store.order.totalPrice.toLocaleString()}</span>
            </div>
            <button class="btn btn-primary" onclick="app.saveCanvasAndNext()">
                다음 단계
            </button>
        </div>
    `,

    Checkout: () => {
        const modelName = store.order.model || "iPhone 15 Plus";
        const previewSrc = store.order.designDataUrl || store.order.selectedTemplateSrc || "";
        const shipping = store.auth.user || {};
        const shippingAddress = [shipping.address1, shipping.address2].filter(Boolean).join(' ');
        return `
        ${TopNav(true)}
        <div id="view-container" class="fade-in">
            <h2 class="view-title">주문 확인</h2>
            <p class="view-subtitle">선택하신 내용을 확인하고 결제를 진행합니다.</p>

            <div class="case-preview-wrap">
                <span class="case-preview-label">완성 예상 미리보기</span>
                <div class="case-preview-frame">
                    <div class="preview-camera"></div>
                    <img class="design-overlay" src="${previewSrc}" alt="나의 도안">
                </div>
            </div>

            <div class="receipt-card">
                <div class="receipt-row">
                    <span>기종</span>
                    <strong>${modelName}</strong>
                </div>
                <div class="receipt-row">
                    <span>케이스 타입</span>
                    <strong>${CASE_TYPES.find(t => t.id === store.order.caseType).label}</strong>
                </div>
                <div class="receipt-row">
                    <span>범퍼 색상</span>
                    <strong>${BUMPER_COLORS.find(c => c.id === store.order.caseColor).label}</strong>
                </div>
                <div class="receipt-row">
                    <span>받는 분</span>
                    <strong>${shipping.name || '-'}</strong>
                </div>
                <div class="receipt-row">
                    <span>연락처</span>
                    <strong>${shipping.phone || '-'}</strong>
                </div>
                <div class="receipt-row">
                    <span>배송지</span>
                    <strong>${shipping.postalCode ? `(${shipping.postalCode}) ` : ''}${shippingAddress || '-'}</strong>
                </div>
                <div class="receipt-row total">
                    <span>총 결제 금액</span>
                    <span>₩${store.order.totalPrice.toLocaleString()}</span>
                </div>
            </div>
        </div>

        <div class="bottom-bar fade-in" style="justify-content:center;">
            <button id="pay-btn" class="btn btn-primary" style="width:100%; background:var(--accent-grad);" onclick="app.processPayment()">
                결제하기
            </button>
        </div>
    `;
    },


    Status: () => `
        ${TopNav(false)}
        ${(() => {
            const progress = Number(store.orderStatus?.progress ?? 0);
            const cancelLocked = progress > CANCEL_PROGRESS_LIMIT;
            return `
        <div id="view-container" class="fade-in">
            <div class="status-header">
                <div class="status-icon-large spin" id="robot-spinner">
                    <i data-feather="loader"></i>
                </div>
                <h2 class="view-title" id="status-title">제작 준비 중</h2>
                <p class="view-subtitle" id="status-subtitle">로봇이 도안을 준비하고 있습니다.</p>
            </div>
            
            <div class="case-preview-wrap" style="margin: 1.5rem 0;">
                <div class="case-preview-frame" style="width: 140px; border-width: 6px; border-radius: 20px;">
                    <div class="preview-camera" style="width: 34px; height: 34px; top: 8px; left: 8px; border-radius: 8px;"></div>
                    <img id="status-design-img" class="design-overlay" src="" alt="디자인 미리보기" style="display: none;">
                </div>
            </div>
            
            <div class="progress-container" style="margin: 1.5rem 0 0.5rem 0; background: var(--border); border-radius: 10px; overflow: hidden; height: 20px;">
                <div id="progress-bar" style="width: 0%; height: 100%; background: var(--accent); transition: width 0.5s ease;"></div>
            </div>
            <div style="display: flex; justify-content: space-between; margin-bottom: 2rem; color: var(--text-secondary); font-size: 0.9rem; font-weight: bold;">
                <span id="progress-text">진행률 0%</span>
                <span id="eta-text">예상 시간 계산 중...</span>
            </div>
            
            <div class="receipt-card">
                <ul class="status-list">
                    <li class="status-item completed">
                        <div class="status-dot"></div>
                        <span>주문 접수 및 도안 전송 완료</span>
                    </li>
                    <li class="status-item active" id="step-2">
                        <div class="status-dot"></div>
                        <span>로봇 원점 복귀 및 드로잉 준비 중</span>
                    </li>
                    <li class="status-item" id="step-3">
                        <div class="status-dot"></div>
                        <span>Doosan M0609 드로잉 진행 중</span>
                    </li>
                    <li class="status-item" id="step-4">
                        <div class="status-dot"></div>
                        <span>건조 및 최종 완성</span>
                    </li>
                </ul>
            </div>

            <div style="display:grid; gap:0.85rem; margin-top: 2rem;">
                <button id="cancel-order-btn" class="btn btn-secondary" style="border-color:#ffd0da; color:#d7265e;" onclick="app.openCancelConfirm()" ${cancelLocked ? 'disabled title="진행률 31% 이후에는 주문 취소가 불가능합니다."' : ''}>
                    ${cancelLocked ? '취소 불가' : '주문 취소'}
                </button>
                <button class="btn btn-secondary" onclick="app.navigate('login')">
                    처음으로 돌아가기
                </button>
            </div>
        </div>
    `})()}
    `
};

// 앱 컨트롤러입니다.
// route 전환, 인증, 주문 옵션 저장, 결제 요청, 상태 조회, Canvas 관리까지 화면 동작을 담당합니다.
const app = {
    container: document.getElementById('app'),

    // 앱 최초 실행 함수입니다.
    // toast DOM 생성, sessionStorage 주문 복구, 뒤로가기 이벤트 연결, 로그인 복구 후 첫 화면을 렌더링합니다.
    async init() {
        if (location.protocol === 'file:') {
            alert('파일 직접 실행(file://)에서는 일부 기능이 제한될 수 있습니다.\n\n가능하면 아래처럼 로컬 서버로 실행해 주세요.\n\npython -m http.server 8080\n\n브라우저에서 http://localhost:8080 접속');
        }

        // toast 알림을 띄울 공통 DOM을 한 번만 생성합니다.
        const toast = document.createElement('div');
        toast.id = 'toast';
        toast.className = 'toast';
        document.body.appendChild(toast);

        // 새로고침 후에도 상태 화면에서 같은 주문을 추적할 수 있도록 주문 ID를 복구합니다.
        const savedOrderId = sessionStorage.getItem('currentOrderId');
        if (savedOrderId) store.currentOrderId = parseInt(savedOrderId);

        window.addEventListener('popstate', (event) => {
            if (event.state && event.state.route) {
                this.render(event.state.route, false);
            } else {
                this.render('login', false);
            }
        });

        const initialRoute = history.state ? history.state.route : 'main';
        await this.restoreAuth();
        this.navigate(initialRoute, true);
    },

    // 저장된 토큰으로 현재 로그인 사용자를 복구합니다.
    // 토큰이 만료되었거나 서버가 인증 실패를 반환하면 토큰을 지우고 비로그인 상태로 둡니다.
    async restoreAuth() {
        if (!getAuthToken()) {
            store.auth.user = null;
            return;
        }

        try {
            const { data } = await apiFetch('/api/auth/me', { method: 'GET' });
            store.auth.user = data && data.authenticated ? data.user : null;
            if (!store.auth.user) {
                setAuthToken('');
            }
        } catch (error) {
            console.error('restoreAuth error:', error);
            store.auth.user = null;
            setAuthToken('');
        }
    },

    // SPA route 이동 함수입니다.
    // 보호된 화면은 로그인 사용자가 없으면 login으로 돌려보냅니다.
    navigate(route, replace = false) {
        if (
            ['account', 'orderStep1', 'orderStep2', 'checkout', 'status'].includes(route) &&
            !store.auth.user
        ) {
            route = 'login';
        }
        if (replace) history.replaceState({ route: route }, '', '#' + route);
        else history.pushState({ route: route }, '', '#' + route);
        this.render(route, true);
    },

    // 브라우저 history를 이용한 뒤로가기입니다.
    goBack() { history.back(); },

    // 현재 route에 맞는 HTML을 #app에 렌더링합니다.
    // orderStep2 진입 시 Canvas를 초기화하고, status 진입 시 서버 polling을 시작합니다.
    render(route, shouldScroll = true) {
        if (route !== 'status' && store.statusPollTimer) {
            clearTimeout(store.statusPollTimer);
            store.statusPollTimer = null;
        }

        let content = '';
        switch (route) {
            case 'main': content = Views.Main(); break;
            case 'login': content = Views.Login(); break;
            case 'account': content = Views.Account(); break;
            case 'orderStep1': content = Views.OrderStep1(); break;
            case 'orderStep2': content = Views.OrderStep2(); break;
            case 'checkout': content = Views.Checkout(); break;
            case 'status': content = Views.Status(); break;
        }

        this.container.innerHTML = content;
        refreshIcons();

        if (shouldScroll) {
            window.scrollTo(0, 0);
        }

        if (route === 'orderStep2') this.canvasManager.init();
        if (route === 'status') this.simulateStatusProcess();
    },

    // 기종 선택 화면의 Apple/Samsung 아코디언을 열고 닫습니다.
    toggleAccordion(brand) {
        if (store.activeAccordion === brand) store.activeAccordion = null;
        else store.activeAccordion = brand;
        this.render('orderStep1', false);
    },

    // 로그인/회원가입 탭 전환입니다.
    setAuthMode(mode) {
        store.authMode = mode;
        this.render('login', false);
    },

    // 로그인 또는 회원가입 폼을 서버로 전송합니다.
    // 성공하면 받은 token/user를 저장하고 주문 옵션 선택 화면으로 이동합니다.
    async submitAuth() {
        const emailEl = document.getElementById('auth-email');
        const passwordEl = document.getElementById('auth-password');
        const nameEl = document.getElementById('auth-name');
        const phoneEl = document.getElementById('auth-phone');
        const postalCodeEl = document.getElementById('auth-postal-code');
        const address1El = document.getElementById('auth-address1');
        const address2El = document.getElementById('auth-address2');

        const email = emailEl ? emailEl.value.trim() : '';
        const password = passwordEl ? passwordEl.value : '';
        const name = nameEl ? nameEl.value.trim() : '';
        const phone = phoneEl ? phoneEl.value.trim() : '';
        const postalCode = postalCodeEl ? postalCodeEl.value.trim() : '';
        const address1 = address1El ? address1El.value.trim() : '';
        const address2 = address2El ? address2El.value.trim() : '';

        if (!email || !password) {
            this.showToast('이메일과 비밀번호를 입력해 주세요.');
            return;
        }

        if (store.authMode === 'signup' && (!name || !phone || !postalCode || !address1)) {
            this.showToast('회원가입 시 이름, 연락처, 우편번호, 주소를 입력해 주세요.');
            return;
        }

        const path = store.authMode === 'signup' ? '/api/auth/signup' : '/api/auth/login';
        const payload = store.authMode === 'signup'
            ? { email, password, name, phone, postalCode, address1, address2 }
            : { email, password };

        try {
            const { response, data } = await apiFetch(path, {
                method: 'POST',
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                this.showToast(data.error || '인증 요청에 실패했습니다.');
                return;
            }

            setAuthToken(data.token || '');
            store.auth.user = data.user || null;
            this.showToast(store.authMode === 'signup' ? '회원가입이 완료되었습니다.' : '로그인되었습니다.');
            this.navigate('orderStep1');
        } catch (error) {
            console.error('submitAuth error:', error);
            this.showToast('서버에 연결할 수 없습니다.');
        }
    },

    // 회원정보 수정 API를 호출합니다.
    // 상태 store.auth.user도 같이 갱신해서 이후 화면에 최신 회원정보가 반영되게 합니다.
    async saveProfile() {
        const name = (document.getElementById('profile-name')?.value || '').trim();
        const phone = (document.getElementById('profile-phone')?.value || '').trim();
        const postalCode = (document.getElementById('profile-postal-code')?.value || '').trim();
        const address1 = (document.getElementById('profile-address1')?.value || '').trim();
        const address2 = (document.getElementById('profile-address2')?.value || '').trim();

        if (!name || !phone || !postalCode || !address1) {
            this.showToast('이름, 연락처, 우편번호, 주소를 입력해 주세요.');
            return;
        }

        try {
            const { response, data } = await apiFetch('/api/auth/profile', {
                method: 'PUT',
                body: JSON.stringify({ name, phone, postalCode, address1, address2 })
            });

            if (!response.ok) {
                this.showToast(data.error || '회원정보 저장에 실패했습니다.');
                return;
            }

            store.auth.user = data.user || store.auth.user;
            this.showToast('회원정보가 저장되었습니다.');
            this.navigate('orderStep1');
        } catch (error) {
            console.error('saveProfile error:', error);
            this.showToast('서버에 연결할 수 없습니다.');
        }
    },

    // 로그아웃 처리입니다.
    // 서버 로그아웃은 실패해도 로컬 토큰을 지워 사용자를 확실히 로그아웃 상태로 만듭니다.
    async logout() {
        try {
            await apiFetch('/api/auth/logout', { method: 'POST' });
        } catch (error) {
            console.error('logout error:', error);
        }
        setAuthToken('');
        store.auth.user = null;
        this.showToast('로그아웃되었습니다.');
        this.navigate('login', true);
    },

    // 주문 옵션 하나를 저장하고 같은 화면을 다시 렌더링합니다.
    // 예: model, caseType, caseColor, totalPrice.
    setOrderData(key, value) {
        store.order[key] = value;
        this.render('orderStep1', false);
    },

    // 브러시 색상을 변경합니다.
    // UI swatch 선택 표시와 hidden input 값을 동시에 맞춰 startDraw에서 같은 색을 읽게 합니다.
    setBrushColor(color) {
        store.canvas.color = color;
        document.querySelectorAll('.color-swatch').forEach(el => {
            if (el.dataset.color === color) el.classList.add('selected');
            else el.classList.remove('selected');
        });
        const picker = document.getElementById('brush-color');
        if (picker) picker.value = color;
    },

    // 도안 화면에서 다음 단계로 넘어가기 전, 현재 canvas를 PNG dataURL로 저장합니다.
    // 이 값은 checkout 미리보기와 서버 imageBase64 전송에 사용됩니다.
    saveCanvasAndNext() {
        const canvas = document.getElementById('drawing-canvas');
        if (!canvas) {
            this.showToast("그림판을 찾을 수 없습니다.");
            return;
        }

        let previewDataUrl = null;
        try {
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = canvas.width;
            tempCanvas.height = canvas.height;
            const tCtx = tempCanvas.getContext('2d');
            if (!tCtx) {
                throw new Error("2D context is not available");
            }

            tCtx.fillStyle = "#ffffff";
            tCtx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);
            tCtx.drawImage(canvas, 0, 0);

            previewDataUrl = tempCanvas.toDataURL('image/png');
        } catch (error) {
            console.error("saveCanvasAndNext error:", error);
            // 캔버스 내보내기가 막혀도 주문 흐름이 완전히 끊기지 않도록 한 번 더 시도합니다.
            try {
                previewDataUrl = canvas.toDataURL('image/png');
            } catch (fallbackError) {
                console.error("saveCanvasAndNext fallback error:", fallbackError);
            }
        }

        if (!previewDataUrl && store.order.selectedTemplateSrc) {
            previewDataUrl = store.order.selectedTemplateSrc;
            this.showToast("캔버스 내보내기 제한으로 템플릿 미리보기를 사용합니다.");
        }

        if (!previewDataUrl) {
            // 마지막 fallback입니다. 최소한의 흰 PNG를 만들어 checkout 화면이 깨지지 않게 합니다.
            const fallbackCanvas = document.createElement('canvas');
            fallbackCanvas.width = Math.max(canvas.width, 1);
            fallbackCanvas.height = Math.max(canvas.height, 1);
            const fallbackCtx = fallbackCanvas.getContext('2d');
            if (fallbackCtx) {
                fallbackCtx.fillStyle = "#ffffff";
                fallbackCtx.fillRect(0, 0, fallbackCanvas.width, fallbackCanvas.height);
                previewDataUrl = fallbackCanvas.toDataURL('image/png');
            }
            this.showToast("미리보기 생성에 문제가 있어 기본 이미지로 이동합니다.");
        }

        store.order.designDataUrl = previewDataUrl;
        this.navigate('checkout');
    },

    // 결제 버튼 클릭 후 실제 주문 생성까지 처리합니다.
    // 최종 PNG imagePayload를 확보한 뒤 uploadOrderToServer()로 서버에 주문을 생성합니다.
    async processPayment() {
        const btn = document.getElementById('pay-btn');
        btn.innerHTML = '<i data-feather="loader" style="animation: spin 1s linear infinite;"></i> 결제 처리 중...';
        btn.disabled = true;
        refreshIcons();

        let imagePayload = store.order.designDataUrl;
        
        console.log("Payment process - designDataUrl exists:", !!imagePayload);
        
        // designDataUrl이 없으면 현재 화면의 canvas에서 직접 PNG dataURL을 생성합니다.
        if (!imagePayload) {
            try {
                console.log("Creating image from canvas");
                const canvas = document.getElementById('drawing-canvas');
                if (canvas) {
                    const tempCanvas = document.createElement('canvas');
                    tempCanvas.width = canvas.width;
                    tempCanvas.height = canvas.height;
                    const tCtx = tempCanvas.getContext('2d');
                    if (tCtx) {
                        tCtx.fillStyle = "#ffffff";
                        tCtx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);
                        tCtx.drawImage(canvas, 0, 0);
                        imagePayload = tempCanvas.toDataURL('image/png');
                        console.log("✓ Canvas converted to data URL, length:", imagePayload.length);
                    }
                }
            } catch (e) {
                console.error("✗ Canvas conversion error:", e.message);
            }
        }

        // 여전히 없으면 서버 요청 형식을 만족시키기 위해 흰 fallback 이미지를 만듭니다.
        if (!imagePayload) {
            console.warn("Creating fallback image");
            const fallbackCanvas = document.createElement('canvas');
            fallbackCanvas.width = 400;
            fallbackCanvas.height = 600;
            const fallbackCtx = fallbackCanvas.getContext('2d');
            if (fallbackCtx) {
                fallbackCtx.fillStyle = "#ffffff";
                fallbackCtx.fillRect(0, 0, 400, 600);
                imagePayload = fallbackCanvas.toDataURL('image/png');
                console.log("✓ Fallback image created");
            }
        }

        if (!imagePayload || !String(imagePayload).startsWith('data:image')) {
            console.error("Final check failed");
            this.showToast("이미지 데이터 생성에 실패했습니다. 다시 시도해 주세요.");
            btn.innerHTML = '결제하기';
            btn.disabled = false;
            return;
        }

        console.log("✓ Image payload ready, uploading...");
        const result = await uploadOrderToServer(imagePayload, store.order);

        console.log("processPayment - Server result:", result);

        if (result === true) {
            console.log("✓ Payment successful!");
            this.showToast("결제가 완료되었습니다.");
            this.navigate('status');
        } else {
            const errorMsg = typeof result === 'string' ? result : "서버 연결 오류";
            console.error("✗ Payment failed:", errorMsg);
            this.showToast(`오류: ${errorMsg}`);
            btn.innerHTML = '결제하기';
            btn.disabled = false;
        }
    },

    // 주문 취소 확인 모달을 엽니다.
    // 진행률이 기준을 넘으면 물리 작업 안정성을 위해 취소 요청을 막습니다.
    openCancelConfirm() {
        const currentProgress = Number(store.orderStatus?.progress ?? 0);
        if (currentProgress > CANCEL_PROGRESS_LIMIT) {
            this.showToast(`진행률 ${CANCEL_PROGRESS_LIMIT}% 이후에는 주문 취소가 불가능합니다.`);
            return;
        }

        if (document.querySelector('.confirm-modal')) return;

        const modal = document.createElement('div');
        modal.className = 'confirm-modal';
        modal.innerHTML = `
            <div class="confirm-modal-panel">
                <div class="confirm-modal-badge">주문 취소 확인</div>
                <h3>정말 취소하시겠습니까?</h3>
                <p>취소 요청 후에는 환불 처리가 불가능합니다. 현재 작업이 중단되고 케이스는 초기 위치로 복귀합니다.</p>
                <div class="confirm-modal-actions">
                    <button class="btn btn-secondary" data-action="close">아니오</button>
                    <button class="btn btn-primary confirm-danger" data-action="confirm">예, 취소할게요</button>
                </div>
            </div>
        `;

        modal.addEventListener('click', async (event) => {
            if (event.target === modal || event.target.closest('[data-action="close"]')) {
                modal.remove();
                return;
            }

            const confirmBtn = event.target.closest('[data-action="confirm"]');
            if (!confirmBtn) return;

            confirmBtn.disabled = true;
            confirmBtn.innerText = '취소 요청 중...';
            await this.cancelCurrentOrder();
            modal.remove();
        });

        document.body.appendChild(modal);
    },

    // 현재 주문에 cancel_requested 상태를 요청합니다.
    // 실제 로봇 정지/복구는 서버와 robot_drawer 쪽에서 처리하고, 앱은 요청 상태를 보여 줍니다.
    async cancelCurrentOrder() {
        if (!store.currentOrderId) {
            this.showToast('취소할 주문 정보를 찾을 수 없습니다.');
            return;
        }

        const currentProgress = Number(store.orderStatus?.progress ?? 0);
        if (currentProgress > CANCEL_PROGRESS_LIMIT) {
            this.showToast(`진행률 ${CANCEL_PROGRESS_LIMIT}% 이후에는 주문 취소가 불가능합니다.`);
            return;
        }

        const btn = document.getElementById('cancel-order-btn');
        if (btn) {
            btn.disabled = true;
            btn.innerText = '취소 요청 중...';
        }

        try {
            const { response, data } = await apiFetch(`/api/orders/${store.currentOrderId}/cancel`, {
                method: 'PATCH'
            });

            if (!response.ok) {
                const message = data.error || '주문 취소 요청에 실패했습니다.';
                this.showToast(message);
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = '주문 취소';
                }
                return;
            }

            document.getElementById('status-title').innerText = '주문 취소 요청 완료';
            document.getElementById('status-subtitle').innerText = '관리자 서버에 중단 요청을 보냈습니다.';
            document.getElementById('eta-text').innerText = '취소 처리 대기 중';
            if (btn) {
                btn.innerText = '취소 요청됨';
            }
            this.showToast('관리자 서버로 주문 취소 요청을 보냈습니다.');
        } catch (error) {
            console.error('cancelCurrentOrder error:', error);
            this.showToast('주문 취소 요청 중 서버 연결 오류가 발생했습니다.');
            if (btn) {
                btn.disabled = false;
                btn.innerText = '주문 취소';
            }
        }
    },

    // 화면 하단/상단 toast 메시지 표시용 공통 함수입니다.
    showToast(message) {
        const toast = document.getElementById('toast');
        toast.innerText = message;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 3000);
    },

    // 로봇이 외부 충격/보호 정지를 감지했을 때 사용자에게 별도 모달로 안내합니다.
    showImpactStopModal(message, reason = '') {
        if (document.querySelector('.confirm-modal.impact-stop-modal')) return;

        const modal = document.createElement('div');
        modal.className = 'confirm-modal impact-stop-modal';
        modal.innerHTML = `
            <div class="confirm-modal-panel">
                <div class="confirm-modal-badge">작업 중단 안내</div>
                <h3>외부 충격으로 작업이 중단되었습니다.</h3>
                <p>${message}</p>
                ${reason ? `<p style="margin-top:0.75rem; color:var(--text2); font-size:0.92rem;">중단 사유: ${reason}</p>` : ''}
                <div class="confirm-modal-actions">
                    <button class="btn btn-primary" data-action="close">확인</button>
                </div>
            </div>
        `;

        modal.addEventListener('click', (event) => {
            if (event.target === modal || event.target.closest('[data-action="close"]')) {
                modal.remove();
            }
        });

        document.body.appendChild(modal);
    },

    // 상태 화면 polling 루프입니다.
    // 이름은 simulate지만 실제로는 서버의 주문 목록과 로봇 상태 API를 1초마다 조회합니다.
    async simulateStatusProcess() {
        if (store.statusPollTimer) {
            clearTimeout(store.statusPollTimer);
            store.statusPollTimer = null;
        }
        document.getElementById('status-title').innerText = '로봇 대기 중...';

        // 서버에서 주문 상태와 로봇 상태를 함께 가져옵니다.
        // 주문 상태는 DB 기준 진행률/완료/취소를, 로봇 상태는 충격 정지 같은 실시간 이벤트를 알려 줍니다.
        const checkStatus = async () => {
            if (!store.currentOrderId) return;
            try {
                const [{ response, data }, robotStatusResult] = await Promise.all([
                    apiFetch('/api/my/orders', {
                        method: 'GET'
                    }),
                    apiFetch('/api/robot_status', {
                        method: 'GET'
                    }).catch(() => null)
                ]);
                if (!response.ok) {
                    throw new Error(data.error || `서버 응답 오류 (${response.status})`);
                }
                const orders = Array.isArray(data) ? data : [];
                const myOrder = orders.find(o => o.id === store.currentOrderId);

                if (robotStatusResult?.response?.ok) {
                    store.robotStatus = robotStatusResult.data || null;
                }

                if (myOrder) {
                    store.orderStatus = myOrder;
                    const progress = myOrder.progress || 0;
                    const eta = myOrder.estimatedTime || 0;
                    const robotStatus = store.robotStatus || {};
                    const robotOrderId = Number(robotStatus.orderId ?? 0);
                    const impactDetected =
                        myOrder.status === 'impact_stopped' ||
                        (
                            robotStatus.stage === 'IMPACT_STOP' &&
                            (!robotOrderId || robotOrderId === Number(store.currentOrderId))
                        );

                    // 서버에 저장된 최종 도안 이미지를 상태 화면 미리보기로 보여 줍니다.
                    const imgEl = document.getElementById('status-design-img');
                    if (imgEl && myOrder.image_path) {
                        imgEl.src = `${API_BASE_URL}/uploads/${myOrder.image_path}`;
                        imgEl.style.display = 'block';
                    }

                    document.getElementById('progress-bar').style.width = `${progress}%`;
                    document.getElementById('progress-text').innerText = `진행률 ${progress}%`;

                    const cancelBtn = document.getElementById('cancel-order-btn');
                    const cancelLockedByProgress = progress > CANCEL_PROGRESS_LIMIT;
                    if (cancelBtn) {
                        if (['done', 'cancel_requested', 'cancelled'].includes(myOrder.status)) {
                            cancelBtn.disabled = true;
                        } else if (cancelLockedByProgress) {
                            cancelBtn.disabled = true;
                            cancelBtn.innerText = '취소 불가';
                            cancelBtn.title = `진행률 ${CANCEL_PROGRESS_LIMIT}% 이후에는 주문 취소가 불가능합니다.`;
                        } else {
                            cancelBtn.disabled = false;
                            cancelBtn.innerText = '주문 취소';
                            cancelBtn.title = '';
                        }
                    }

                    if (eta > 0) {
                        const mins = Math.floor(eta / 60);
                        const secs = eta % 60;
                        const timeStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
                        document.getElementById('eta-text').innerText = `ETA: ${timeStr}`;
                    } else if (progress >= 100) {
                        document.getElementById('eta-text').innerText = '작업 완료';
                    }

                    if (impactDetected) {
                        const spinner = document.getElementById('robot-spinner');
                        const impactReason = robotStatus.stopReason || '';
                        spinner.className = 'status-icon-large';
                        spinner.innerHTML = '<i data-feather="alert-triangle"></i>';
                        document.getElementById('status-title').innerText = '외부 충격으로 작업이 중단되었습니다.';
                        document.getElementById('status-subtitle').innerText = impactReason
                            ? `관리자 확인이 필요합니다. (${impactReason})`
                            : '관리자 확인이 필요합니다. 잠시만 기다려 주세요.';
                        document.getElementById('eta-text').innerText = '작업 중단';
                        document.getElementById('step-3').classList.remove('active');
                        document.getElementById('step-4').classList.remove('active');
                        if (cancelBtn) {
                            cancelBtn.disabled = true;
                            cancelBtn.innerText = '작업 중단';
                            cancelBtn.title = '외부 충격 감지로 작업이 중단되었습니다.';
                        }
                        refreshIcons();

                        if (!store.impactStopPopupShown) {
                            store.impactStopPopupShown = true;
                            this.showImpactStopModal(
                                '로봇이 외부 충격을 감지해 작업을 멈췄습니다. 관리자 확인 후 다시 진행해 주세요.',
                                impactReason
                            );
                        }

                        if (myOrder.status === 'impact_stopped') {
                            return;
                        }
                    } else if (myOrder.status === 'processing') {
                        document.getElementById('status-title').innerText = '로봇이 그림을 그리고 있습니다.';
                        document.getElementById('status-subtitle').innerText = '잠시만 기다려 주세요.';
                        document.getElementById('step-2').classList.replace('active', 'completed');
                        document.getElementById('step-3').classList.add('active');
                        refreshIcons();
                    } else if (myOrder.status === 'cancel_requested') {
                        document.getElementById('status-title').innerText = '주문 취소 요청 완료';
                        document.getElementById('status-subtitle').innerText = '관리자 서버가 로봇 중단을 처리하고 있습니다.';
                        document.getElementById('eta-text').innerText = '취소 처리 중';
                        const spinner = document.getElementById('robot-spinner');
                        spinner.className = 'status-icon-large';
                        spinner.innerHTML = '<i data-feather="pause-circle"></i>';
                        if (cancelBtn) {
                            cancelBtn.innerText = '취소 요청됨';
                        }
                        refreshIcons();
                    } else if (myOrder.status === 'cancelled') {
                        document.getElementById('status-title').innerText = '주문 취소 완료';
                        document.getElementById('status-subtitle').innerText = '케이스가 초기 위치로 복귀했고 작업이 안전하게 중단되었습니다.';
                        document.getElementById('eta-text').innerText = '취소 완료';
                        document.getElementById('progress-text').innerText = `진행률 ${progress}%`;
                        const spinner = document.getElementById('robot-spinner');
                        spinner.className = 'status-icon-large success';
                        spinner.innerHTML = '<i data-feather="check-circle"></i>';
                        document.getElementById('step-2').classList.replace('active', 'completed');
                        document.getElementById('step-3').classList.remove('active');
                        document.getElementById('step-4').classList.remove('active');
                        if (cancelBtn) {
                            cancelBtn.innerText = '취소 완료';
                        }
                        refreshIcons();
                        return;
                    } else if (myOrder.status === 'done') {
                        document.getElementById('progress-bar').style.width = '100%';
                        document.getElementById('progress-text').innerText = '진행률 100%';
                        document.getElementById('eta-text').innerText = '작업 완료';

                        document.getElementById('status-title').innerText = '제작 완료!';
                        document.getElementById('status-subtitle').innerText = '케이스가 완성되었습니다.';
                        const spinner = document.getElementById('robot-spinner');
                        spinner.className = 'status-icon-large success';
                        spinner.innerHTML = '<i data-feather="check"></i>';
                        document.getElementById('step-3').classList.replace('active', 'completed');
                        document.getElementById('step-4').classList.add('completed');
                        refreshIcons();
                        this.showToast("모든 로봇 작업이 완료되었습니다.");
                        return; // 완료 상태에서는 더 이상 polling하지 않습니다.
                    }
                }
            } catch (e) {
                console.error("상태 확인 실패:", e);
            }

            // 완료/취소/충격 정지로 종료되지 않았으면 1초 뒤 다시 확인합니다.
            store.statusPollTimer = setTimeout(checkStatus, 1000);
        };

        checkStatus();
    },

    // Canvas 관리 객체입니다.
    // 실제 그림판 초기화, Undo 스냅샷, 이미지/템플릿 배치, stroke 좌표 저장, 확장 편집기를 담당합니다.
    canvasManager: {
        canvas: null,
        ctx: null,

        // 도안 화면이 렌더링될 때 Canvas를 실제 표시 크기의 2배 해상도로 초기화합니다.
        // 화면에는 CSS 크기로 보이지만 내부 좌표는 2배라서 더 선명하고, stroke도 canvas pixel 기준으로 저장됩니다.
        init() {
            this.canvas = document.getElementById('drawing-canvas');
            if (!this.canvas) return;

            this.ctx = this.canvas.getContext('2d', { willReadFrequently: true });
            const rect = this.canvas.parentElement.getBoundingClientRect();
            this.canvas.width = rect.width * 2;
            this.canvas.height = rect.height * 2;
            this.ctx.scale(2, 2);

            // 배경은 투명하게 유지합니다.
            // 케이스 프레임, 카메라홀, safe area는 CSS 레이어로 보이고 실제 canvas 이미지에는 사용자가 그린 도안만 남습니다.
            this.ctx.clearRect(0, 0, rect.width, rect.height);
            this.ctx.lineCap = 'round';
            this.ctx.lineJoin = 'round';

            store.canvas.history = [];
            this.saveState();

            this.bindEvents();
        },

        // Undo를 위해 현재 canvas 이미지를 dataURL 스냅샷으로 저장합니다.
        // 너무 많이 쌓이면 메모리가 커지므로 최근 20개까지만 유지합니다.
        saveState() {
            store.canvas.history.push(this.canvas.toDataURL('image/png'));
            if (store.canvas.history.length > 20) store.canvas.history.shift();
        },

        // 마지막 그리기 동작을 되돌립니다.
        // 화면 이미지는 history에서 복구하고, 로봇 전송용 strokes도 마지막 stroke를 함께 제거합니다.
        undo() {
            if (store.canvas.history.length <= 1) {
                app.showToast("더 이상 되돌릴 수 없습니다.");
                return;
            }
            store.canvas.history.pop();
            if (store.canvas.strokes.length > 0) {
                store.canvas.strokes.pop();
            }
            const previousState = store.canvas.history[store.canvas.history.length - 1];

            const img = new Image();
            img.src = previousState;
            img.onload = () => {
                this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
                this.ctx.drawImage(img, 0, 0, this.canvas.width / 2, this.canvas.height / 2);
            };
            app.showToast("실행 취소 완료");
        },

        // 사용자가 이미지 파일을 업로드했을 때 safe area 안에 자동 배치합니다.
        // 업로드 이미지는 좌표 stroke가 아니므로 기존 strokes를 비우고 containsRasterContent=true로 표시합니다.
        loadImage(event) {
            const file = event.target.files[0];
            if (!file) return;
            store.order.selectedTemplateSrc = null;
            store.canvas.strokes = [];
            store.canvas.currentStroke = null;
            store.canvas.containsRasterContent = true;
            const reader = new FileReader();
            reader.onload = (e) => {
                const img = new Image();
                img.onload = () => {
                    this.drawImageInSafeArea(img, true);

                    try {
                        const tempCanvas = document.createElement('canvas');
                        tempCanvas.width = this.canvas.width;
                        tempCanvas.height = this.canvas.height;
                        const tCtx = tempCanvas.getContext('2d');
                        if (tCtx) {
                            tCtx.fillStyle = "#ffffff";
                            tCtx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);
                            tCtx.drawImage(this.canvas, 0, 0);
                            store.order.designDataUrl = tempCanvas.toDataURL('image/png');
                        }
                    } catch (error) {
                        console.error("Uploaded image preview export failed:", error);
                        store.order.designDataUrl = e.target.result;
                    }

                    app.showToast("이미지가 드로잉 가능 영역에 맞춰 배치되었습니다.");
                    event.target.value = '';
                };
                img.src = e.target.result;
            };
            reader.readAsDataURL(file);
        },

        // 업로드/템플릿 이미지를 safe area 안에 비율 유지로 맞춰 그립니다.
        // 0.92 배율을 곱해 safe area 경계에 너무 붙지 않도록 약간의 내부 여백을 둡니다.
        drawImageInSafeArea(img, shouldClear = true) {
            const rect = this.canvas.parentElement.getBoundingClientRect();
            const safeArea = {
                x: rect.width * 0.11,
                y: rect.height * 0.18,
                width: rect.width * 0.78,
                height: rect.height * 0.66
            };
            const scale = Math.min((safeArea.width * 0.92) / img.width, (safeArea.height * 0.92) / img.height);
            const drawWidth = img.width * scale;
            const drawHeight = img.height * scale;
            const x = safeArea.x + (safeArea.width - drawWidth) / 2;
            const y = safeArea.y + (safeArea.height - drawHeight) / 2;

            if (shouldClear) {
                this.ctx.clearRect(0, 0, rect.width, rect.height);
            }

            this.ctx.drawImage(img, x, y, drawWidth, drawHeight);
            this.saveState();
        },

        // 기본 템플릿 이미지를 캔버스에 적용합니다.
        // 템플릿도 raster 이미지 취급이라 strokeData 대신 imageBase64 fallback 경로를 사용합니다.
        loadTemplateImage(src, label) {
            store.canvas.strokes = [];
            store.canvas.currentStroke = null;
            store.canvas.containsRasterContent = true;
            if (!this.canvas || !this.ctx) return;
            store.order.selectedTemplateSrc = src;

            const img = new Image();
            img.onload = () => {
                try {
                    console.log("Template image loaded successfully:", src);
                    this.drawImageInSafeArea(img, true);
                    
                    // 캔버스 내용을 designDataUrl로 즉시 저장해서 checkout 미리보기에 바로 사용할 수 있게 합니다.
                    const tempCanvas = document.createElement('canvas');
                    tempCanvas.width = this.canvas.width;
                    tempCanvas.height = this.canvas.height;
                    const tCtx = tempCanvas.getContext('2d');
                    if (tCtx) {
                        tCtx.fillStyle = "#ffffff";
                        tCtx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);
                        tCtx.drawImage(this.canvas, 0, 0);
                        const dataUrl = tempCanvas.toDataURL('image/png');
                        if (dataUrl && dataUrl.startsWith('data:image')) {
                            store.order.designDataUrl = dataUrl;
                            console.log("✓ Template image saved to designDataUrl, length:", dataUrl.length);
                        } else {
                            console.error("✗ toDataURL failed - returned invalid data");
                            throw new Error("Canvas toDataURL failed");
                        }
                    }
                } catch (e) {
                    console.error("✗ Template image processing error:", e);
                    app.showToast("템플릿 처리 중 오류가 발생했습니다: " + e.message);
                    return;
                }
                app.showToast(`${label} 템플릿이 적용되었습니다.`);
            };
            img.onerror = (err) => {
                console.error("✗ Template image load failed:", src, err);
                app.showToast("템플릿 이미지를 불러오지 못했습니다. 경로: " + src);
            };
            console.log("Loading template image:", src);
            img.src = src;
        },

        // 화면 CSS 좌표 기준 safe area를 계산합니다.
        // 이 값은 사용자가 확장 편집기를 열 수 있는 영역 판정과 이미지 배치에 사용됩니다.
        getSafeAreaRect() {
            const rect = this.canvas.parentElement.getBoundingClientRect();
            return {
                x: rect.width * 0.11,
                y: rect.height * 0.18,
                width: rect.width * 0.78,
                height: rect.height * 0.66
            };
        },

        // 포인터 위치가 safe area 안인지 검사합니다.
        // safe area 밖에서 확장 편집기를 여는 실수를 막기 위한 UX guard입니다.
        isPointInSafeArea(pos) {
            const safeArea = this.getSafeAreaRect();
            return (
                pos.x >= safeArea.x &&
                pos.x <= safeArea.x + safeArea.width &&
                pos.y >= safeArea.y &&
                pos.y <= safeArea.y + safeArea.height
            );
        },

        // safe area만 크게 보여 주는 확장 그림판 모달을 엽니다.
        // 사용자는 큰 화면에 그리지만, 저장되는 좌표는 메인 캔버스 safe area 내부 좌표로 다시 변환됩니다.
        openExpandedEditor() {
            if (!this.canvas || document.querySelector('.drawing-modal')) return;

            const safeArea = this.getSafeAreaRect();
            const modal = document.createElement('div');
            modal.className = 'drawing-modal';
            modal.innerHTML = `
                <div class="drawing-modal-panel">
                    <div class="drawing-modal-head">
                        <div>
                            <span>Expanded Canvas</span>
                            <strong>큰 그림판에서 편하게 그리세요</strong>
                        </div>
                        <button class="modal-icon-btn" data-action="close" title="닫기"><i data-feather="x"></i></button>
                    </div>
                    <div class="expanded-canvas-wrap">
                        <canvas id="expanded-drawing-canvas"></canvas>
                    </div>
                    <div class="drawing-modal-tools">
                        <div class="color-palette">
                            <div class="color-swatch ${store.canvas.color === '#111111' ? 'selected' : ''}" style="background:#111111" data-color="#111111"></div>
                            <div class="color-swatch ${store.canvas.color === '#ff0000' ? 'selected' : ''}" style="background:#ff0000" data-color="#ff0000"></div>
                            <div class="color-swatch ${store.canvas.color === '#0000ff' ? 'selected' : ''}" style="background:#0000ff" data-color="#0000ff"></div>
                        </div>
                        <input type="range" id="expanded-brush-size" min="2" max="34" value="${Math.max(8, store.canvas.size * 2)}">
                        <button class="btn btn-secondary modal-action-btn" data-action="clear">지우기</button>
                        <button class="btn btn-primary modal-action-btn" data-action="apply">완료</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            refreshIcons();

            const editorCanvas = modal.querySelector('#expanded-drawing-canvas');
            const editorCtx = editorCanvas.getContext('2d', { willReadFrequently: true });
            const wrap = modal.querySelector('.expanded-canvas-wrap');
            const wrapRect = wrap.getBoundingClientRect();

            editorCanvas.width = Math.floor(wrapRect.width * 2);
            editorCanvas.height = Math.floor(wrapRect.height * 2);
            editorCtx.scale(2, 2);
            editorCtx.fillStyle = '#fff';
            editorCtx.fillRect(0, 0, wrapRect.width, wrapRect.height);
            editorCtx.drawImage(
                this.canvas,
                safeArea.x * 2,
                safeArea.y * 2,
                safeArea.width * 2,
                safeArea.height * 2,
                0,
                0,
                wrapRect.width,
                wrapRect.height
            );
            editorCtx.lineCap = 'round';
            editorCtx.lineJoin = 'round';

            let isEditorDrawing = false;
            let editorCurrentStroke = null;
            // 확장 캔버스에서 그린 stroke 누적입니다.
            // 완료 버튼을 누를 때 메인 store.canvas.strokes에 합쳐집니다.
            let editorStrokes = [];

            const getEditorPos = (e) => {
                const bounds = editorCanvas.getBoundingClientRect();
                const clientX = e.touches ? e.touches[0].clientX : e.clientX;
                const clientY = e.touches ? e.touches[0].clientY : e.clientY;
                return { x: clientX - bounds.left, y: clientY - bounds.top };
            };

            // 확장 캔버스 좌표 -> 메인 캔버스 픽셀 좌표(safeArea 기준)로 변환합니다.
            // 로봇 전송 좌표는 메인 캔버스 기준이어야 하므로 여기서 좌표계를 맞춥니다.
            const editorPosToMainPixel = (pos) => {
                const scaleX = (safeArea.width * 2) / Math.max(wrapRect.width, 1);
                const scaleY = (safeArea.height * 2) / Math.max(wrapRect.height, 1);
                return {
                    x: safeArea.x * 2 + pos.x * scaleX,
                    y: safeArea.y * 2 + pos.y * scaleY
                };
            };

            const start = (e) => {
                e.preventDefault();
                isEditorDrawing = true;
                const pos = getEditorPos(e);
                const mainPixel = editorPosToMainPixel(pos);
                const color = store.canvas.color;
                const size = Number(modal.querySelector('#expanded-brush-size').value);
                editorCurrentStroke = {
                    color: color,
                    size: size,
                    points: [{ x: mainPixel.x, y: mainPixel.y }]
                };
                // 화면에 실제 선을 그리기 위해 Canvas path도 동시에 시작합니다.
                editorCtx.beginPath();
                editorCtx.moveTo(pos.x, pos.y);
            };
            const move = (e) => {
                e.preventDefault();
                if (!isEditorDrawing) return;
                const pos = getEditorPos(e);
                const mainPixel = editorPosToMainPixel(pos);
                editorCtx.lineWidth = Number(modal.querySelector('#expanded-brush-size').value);
                editorCtx.strokeStyle = store.canvas.color;
                editorCtx.lineTo(pos.x, pos.y);
                editorCtx.stroke();

                if (editorCurrentStroke) {
                    const points = editorCurrentStroke.points;
                    const last = points[points.length - 1];
                    // 마지막 저장 점과 1.5px 이상 떨어졌을 때만 좌표를 추가합니다.
                    // 너무 촘촘히 저장하면 JSON이 커지고 로봇 이동 명령도 과도하게 많아집니다.
                    if (Math.hypot(mainPixel.x - last.x, mainPixel.y - last.y) >= 1.5) {
                        points.push({ x: mainPixel.x, y: mainPixel.y });
                    }
                }
            };
            const stop = () => {
                if (!isEditorDrawing) return;
                isEditorDrawing = false;
                editorCtx.closePath();
                // stroke 좌표 누적 저장입니다.
                // 점이 2개 미만이면 실제 선이 아니므로 버립니다.
                if (editorCurrentStroke && editorCurrentStroke.points.length >= 2) {
                    editorStrokes.push(editorCurrentStroke);
                }
                editorCurrentStroke = null;
            };

            editorCanvas.addEventListener('mousedown', start);
            editorCanvas.addEventListener('mousemove', move);
            editorCanvas.addEventListener('mouseup', stop);
            editorCanvas.addEventListener('mouseout', stop);
            editorCanvas.addEventListener('touchstart', start, { passive: false });
            editorCanvas.addEventListener('touchmove', move, { passive: false });
            editorCanvas.addEventListener('touchend', stop);

            modal.querySelectorAll('.color-swatch').forEach(swatch => {
                swatch.addEventListener('click', () => {
                    store.canvas.color = swatch.dataset.color;
                    modal.querySelectorAll('.color-swatch').forEach(el => el.classList.toggle('selected', el === swatch));
                    app.setBrushColor(store.canvas.color);
                });
            });

            modal.addEventListener('click', (e) => {
                const actionTarget = e.target.closest('[data-action]');
                if (!actionTarget) return;
                const action = actionTarget.dataset.action;

                if (action === 'close') {
                    modal.remove();
                    return;
                }

                if (action === 'clear') {
                    editorCtx.fillStyle = '#fff';
                    editorCtx.fillRect(0, 0, wrapRect.width, wrapRect.height);
                    editorStrokes = [];
                    editorCurrentStroke = null;
                    isEditorDrawing = false;
                    return;
                }

                if (action === 'apply') {
                    this.ctx.clearRect(safeArea.x, safeArea.y, safeArea.width, safeArea.height);
                    this.ctx.drawImage(editorCanvas, safeArea.x, safeArea.y, safeArea.width, safeArea.height);

                    // 확장 캔버스에서 그린 stroke들을 메인 store에 병합합니다.
                    // 이렇게 해야 서버로 strokeData가 넘어가고 로봇이 contour 추출 없이 좌표를 따라갈 수 있습니다.
                    store.canvas.strokes.push(...editorStrokes);

                    store.canvas.containsRasterContent = false;
                    store.canvas.currentStroke = null;
                    store.order.designDataUrl = null;

                    this.saveState();
                    modal.remove();
                    app.showToast('큰 그림판의 도안이 핸드폰에 반영되었습니다.');
                }
            });
        },

        // 메인 도안 캔버스의 마우스/터치 이벤트를 연결합니다.
        // 화면에 선을 그리는 작업과 동시에 store.canvas.currentStroke에 좌표를 누적합니다.
        bindEvents() {
            const getPos = (e) => {
                // 브라우저 이벤트 좌표(clientX/clientY)를 canvas 화면 좌표로 변환합니다.
                // 이 좌표는 실제 화면에 선을 그릴 때 사용합니다.
                const rect = this.canvas.getBoundingClientRect();
                const clientX = e.touches ? e.touches[0].clientX : e.clientX;
                const clientY = e.touches ? e.touches[0].clientY : e.clientY;
                return { x: clientX - rect.left, y: clientY - rect.top };
            };

            const getCanvasPixelScale = () => {
                // canvas 내부 해상도와 CSS 표시 크기 차이를 계산합니다.
                // 내부 canvas는 2배 해상도이므로 stroke 저장 좌표도 이 scale을 곱해야 정확합니다.
                const rect = this.canvas.getBoundingClientRect();
                return {
                    x: this.canvas.width / Math.max(rect.width, 1),
                    y: this.canvas.height / Math.max(rect.height, 1)
                };
            };

            const toCanvasPixelPoint = (pos) => {
                // 화면 좌표를 서버 전송용 canvas pixel 좌표로 변환합니다.
                const scale = getCanvasPixelScale();
                return {
                    x: pos.x * scale.x,
                    y: pos.y * scale.y
                };
            };

            const startDraw = (e) => {
                e.preventDefault();
                store.canvas.isDrawing = true;

                const pos = getPos(e);
                const pixelPos = toCanvasPixelPoint(pos);
                const color = document.getElementById('brush-color').value;
                const size = Number(document.getElementById('brush-size').value || store.canvas.size);

                // 직접 그리기 상태일 때만 stroke를 구조화해서 저장합니다.
                // 이미지 업로드/템플릿이 섞인 raster 상태에서는 stroke만으로 최종 도안을 설명할 수 없어서 저장하지 않습니다.
                if (!store.canvas.containsRasterContent) {
                    store.canvas.currentStroke = {
                        color: color,
                        size: size,
                        points: [{ x: pixelPos.x, y: pixelPos.y }]
                    };
                } else {
                    store.canvas.currentStroke = null;
                }

                // 사용자가 보는 화면에 즉시 선이 나오도록 Canvas path를 시작합니다.
                this.ctx.beginPath();
                this.ctx.moveTo(pos.x, pos.y);
            };

            const draw = (e) => {
                e.preventDefault();
                if (!store.canvas.isDrawing) return;

                const pos = getPos(e);
                const pixelPos = toCanvasPixelPoint(pos);
                const color = document.getElementById('brush-color').value;
                const size = Number(document.getElementById('brush-size').value || store.canvas.size);

                this.ctx.lineWidth = size;
                this.ctx.strokeStyle = color;

                this.ctx.lineTo(pos.x, pos.y);
                this.ctx.stroke();

                if (store.canvas.currentStroke) {
                    const points = store.canvas.currentStroke.points;
                    const last = points[points.length - 1];
                    const dx = pixelPos.x - last.x;
                    const dy = pixelPos.y - last.y;

                    // 좌표는 매 이벤트마다 저장하지 않고 1.5px 이상 움직였을 때만 추가합니다.
                    // 느리게 그리면 이벤트는 많아질 수 있지만, 이 필터가 중복에 가까운 점을 줄여 줍니다.
                    if (Math.hypot(dx, dy) >= 1.5) {
                        points.push({ x: pixelPos.x, y: pixelPos.y });
                    }
                }
            };

            const stopDraw = () => {
                if (store.canvas.isDrawing) {
                    store.canvas.isDrawing = false;
                    this.ctx.closePath();

                    if (
                        store.canvas.currentStroke &&
                        store.canvas.currentStroke.points.length >= 2
                    ) {
                        // pointer down부터 pointer up까지 이어진 한 획을 strokes 배열에 확정 저장합니다.
                        // 이 배열이 결제 시 strokeData.strokes로 서버에 전송됩니다.
                        store.canvas.strokes.push(store.canvas.currentStroke);
                    }

                    store.canvas.currentStroke = null;
                    this.saveState();
                }
            };

            // 마우스 입력과 터치 입력을 모두 지원합니다.
            this.canvas.addEventListener('mousedown', startDraw);
            this.canvas.addEventListener('mousemove', draw);
            this.canvas.addEventListener('mouseup', stopDraw);
            this.canvas.addEventListener('mouseout', stopDraw);
            this.canvas.addEventListener('dblclick', (e) => {
                const pos = getPos(e);
                if (this.isPointInSafeArea(pos)) {
                    // safe area를 더 크게 편집하고 싶을 때 더블클릭으로 확장 편집기를 엽니다.
                    this.openExpandedEditor();
                } else {
                    app.showToast("분홍색 드로잉 가능 영역을 두 번 눌러 주세요.");
                }
            });

            this.canvas.addEventListener('touchstart', startDraw, { passive: false });
            this.canvas.addEventListener('touchmove', draw, { passive: false });
            this.canvas.addEventListener('touchend', stopDraw);
        },

        // 캔버스 전체를 초기화합니다.
        // 화면 이미지, strokeData, 템플릿 선택, 미리보기 dataURL을 모두 비웁니다.
        clear() {
            const rect = this.canvas.parentElement.getBoundingClientRect();
            this.ctx.clearRect(0, 0, rect.width, rect.height);

            store.canvas.strokes = [];
            store.canvas.currentStroke = null;
            store.canvas.containsRasterContent = false;
            store.order.selectedTemplateSrc = null;
            store.order.designDataUrl = null;

            this.saveState();
            app.showToast("캔버스가 초기화되었습니다.");
        }
    }
};

// DOM이 준비되면 앱을 시작합니다.
window.addEventListener('DOMContentLoaded', () => { app.init(); });
