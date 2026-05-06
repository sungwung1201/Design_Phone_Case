// 자체 서버 웹 API 설정 (로컬 테스트 시 localhost 사용 권장)
const API_BASE_URL = "http://192.168.10.92:5000";

async function uploadOrderToServer(dataUrl, orderInfo) {
    app.showToast("서버에 주문 정보를 전송 중입니다...");

    try {
        const payload = {
            model: orderInfo.model,
            caseType: orderInfo.caseType,
            caseColor: orderInfo.caseColor,
            totalPrice: orderInfo.totalPrice,
            imageBase64: dataUrl
        };

        const response = await fetch(`${API_BASE_URL}/api/orders`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            return errorData.error || `서버 응답 오류 (${response.status})`;
        }
        const data = await response.json();
        store.currentOrderId = data.order_id; // 백엔드 필드명(order_id)과 일치시킴
        sessionStorage.setItem('currentOrderId', data.order_id);
        return true;
    } catch (error) {
        console.error("서버 전송 에러:", error);
        return error.message === 'Failed to fetch' ? "서버가 꺼져있거나 연결할 수 없습니다." : error.message;
    }
}

// 기종 데이터
const MODELS = {
    apple: ["iPhone 15 Pro Max", "iPhone 15 Pro", "iPhone 15 Plus", "iPhone 15", "iPhone 14 Pro Max", "iPhone 14 Pro", "iPhone 14 Plus", "iPhone 14", "iPhone 13 Pro", "iPhone 13", "iPhone 12", "iPhone 11", "iPhone X"],
    samsung: ["Galaxy S24 Ultra", "Galaxy S24+", "Galaxy S24", "Galaxy S23 Ultra", "Galaxy S23+", "Galaxy S23", "Galaxy S22 Ultra", "Galaxy S22", "Galaxy S21 Ultra", "Galaxy S21"]
};

// 재질 데이터
const CASE_TYPES = [
    { id: "clear", label: "투명 (Clear)", price: 0 },
    { id: "opaque", label: "불투명 (Matte)", price: 1000 },
    { id: "translucent", label: "반투명 (Translucent)", price: 2000 }
];

// 범퍼 색상 데이터
const BUMPER_COLORS = [
    { id: "black", label: "블랙" },
    { id: "white", label: "화이트" },
    { id: "pink", label: "핑크" }
];

// 전역 상태
const store = {
    activeAccordion: null,
    currentOrderId: null, // 현재 주문 ID (sessionStorage에 자동 저장)
    order: {
        model: null,
        caseType: "clear",
        caseColor: "black",
        totalPrice: 35000,
        designDataUrl: null,
    },
    canvas: {
        color: '#111111',
        size: 5,
        isDrawing: false,
        history: [],
    }
};

const TopNav = (showBack = true) => `
    <header class="top-nav">
        ${showBack ? '<button class="back-btn" onclick="app.goBack()"><i data-feather="chevron-left"></i> 뒤로</button>' : '<div class="nav-placeholder"></div>'}
        <div class="logo">ROBOCASE</div>
        <div class="nav-placeholder"></div>
    </header>
`;

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
            ">🤖</div>
            <div class="logo" style="font-size: 2.8rem; margin-bottom: 0.75rem;">ROBOCASE</div>
            <p style="color: var(--text2); font-size: 1rem; line-height: 1.7; max-width: 280px; margin-bottom: 0.5rem;">
                나만의 커스텀 폰케이스를<br><strong style="color:var(--text);">두산 로봇 팔</strong>이 직접 그려드립니다.
            </p>
            <div style="display:flex; gap:1rem; margin: 1.5rem 0 2.5rem; flex-wrap:wrap; justify-content:center;">
                <span style="font-size:0.82rem; color:var(--text2); font-weight:600;">✏️ 직접 도안</span>
                <span style="font-size:0.82rem; color:var(--text2); font-weight:600;">🦾 로봇 드로잉</span>
                <span style="font-size:0.82rem; color:var(--text2); font-weight:600;">📦 즉시 수령</span>
            </div>
            <button class="btn btn-primary" onclick="app.navigate('login')" style="
                width: 85%; max-width: 320px;
                background: linear-gradient(135deg, #111827, #374151);
                padding: 1.1rem; font-size: 1rem;
            ">
                🚀 &nbsp;주문 시작하기
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
                <p class="view-subtitle">간단히 로그인 후 주문을 진행해 주세요.</p>
            </div>
            <div style="background:var(--surface); border:1.5px solid var(--border); border-radius:var(--radius-xl); padding:1.75rem; box-shadow:var(--shadow-md);">
                <div class="form-group">
                    <label>이메일</label>
                    <input type="email" class="form-input" placeholder="admin@example.com" value="admin@example.com">
                </div>
                <div class="form-group">
                    <label>비밀번호</label>
                    <input type="password" class="form-input" placeholder="••••••••" value="123456">
                </div>
                <button class="btn btn-primary" style="margin-top:1rem;" onclick="app.navigate('orderStep1')">
                    시작하기 →
                </button>
            </div>
        </div>
    `,

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
                <h3 class="section-title">케이스 재질</h3>
                <div class="chip-group">
                    ${CASE_TYPES.map(type => `
                        <div class="chip ${store.order.caseType === type.id ? 'selected' : ''}" onclick="app.setOrderData('caseType', '${type.id}')">
                            ${type.label}
                            <span class="price">+ ₩${type.price.toLocaleString()}</span>
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
                <span class="price-amount">₩ ${calculatePrice()}</span>
            </div>
            <button class="btn btn-primary" onclick="app.navigate('orderStep2')" ${!canProceed ? 'disabled' : ''}>
                다음 단계
            </button>
        </div>
        `;
    },

    OrderStep2: () => `
        ${TopNav(true)}
        <div id="view-container" class="fade-in">
            <h2 class="view-title">✏️ 디자인 그리기</h2>
            <p class="view-subtitle">자유롭게 도안을 그려주세요.<br>결제 전 케이스에 합성된 결과물을 미리 확인할 수 있습니다.</p>
            <div class="canvas-wrapper">
                <div class="canvas-container">
                    <!-- 카메라 구멍 가이드 -->
                    <div class="camera-hole-guide"></div>
                    <!-- 로봇 드로잉 가능 영역 가이드 -->
                    <div class="robot-safe-area"></div>
                    <!-- 실제 드로잉 캔버스 (투명 배경) -->
                    <canvas id="drawing-canvas"></canvas>
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
                    <button class="tool-btn" onclick="document.getElementById('image-upload').click()" title="이미지 첨부"><i data-feather="image"></i></button>
                    <button class="tool-btn" onclick="app.canvasManager.undo()" title="실행 취소"><i data-feather="corner-up-left"></i></button>
                    <button class="tool-btn" onclick="app.canvasManager.clear()" title="전체 지우기"><i data-feather="trash-2"></i></button>
                </div>
            </div>
        </div>
        <div class="bottom-bar fade-in">
            <div class="price-info">
                <span class="price-label">총 결제 금액</span>
                <span class="price-amount">₩ ${store.order.totalPrice.toLocaleString()}</span>
            </div>
            <button class="btn btn-primary" onclick="app.saveCanvasAndNext()">
                다음 단계 →
            </button>
        </div>
    `,

    Checkout: () => {
        const modelName = store.order.model || "iPhone 15 Plus";
        return `
        ${TopNav(true)}
        <div id="view-container" class="fade-in">
            <h2 class="view-title">주문 확인</h2>
            <p class="view-subtitle">선택하신 내역을 확인하고 결제를 진행합니다.</p>

            <div class="case-preview-wrap">
                <span class="case-preview-label">📱 예상 결과물 미리보기</span>
                <div class="case-preview-frame">
                    <div class="preview-camera"></div>
                    <img class="design-overlay" src="${store.order.designDataUrl}" alt="나의 도안">
                </div>
            </div>

            <div class="receipt-card">
                <div class="receipt-row">
                    <span>기종</span>
                    <strong>${modelName}</strong>
                </div>
                <div class="receipt-row">
                    <span>케이스 재질</span>
                    <strong>${CASE_TYPES.find(t => t.id === store.order.caseType).label}</strong>
                </div>
                <div class="receipt-row">
                    <span>범퍼 색상</span>
                    <strong>${BUMPER_COLORS.find(c => c.id === store.order.caseColor).label}</strong>
                </div>
                <div class="receipt-row total">
                    <span>총 결제 금액</span>
                    <span>₩ ${store.order.totalPrice.toLocaleString()}</span>
                </div>
            </div>
        </div>

        <div class="bottom-bar fade-in" style="justify-content:center;">
            <button id="pay-btn" class="btn btn-primary" style="width:100%; background:var(--accent-grad);" onclick="app.processPayment()">
                💳 &nbsp;결제하기
            </button>
        </div>
    `;
    },


    Status: () => `
        ${TopNav(false)}
        <div id="view-container" class="fade-in">
            <div class="status-header">
                <div class="status-icon-large spin" id="robot-spinner">
                    <i data-feather="loader"></i>
                </div>
                <h2 class="view-title" id="status-title">제작 준비 중</h2>
                <p class="view-subtitle" id="status-subtitle">로봇 팔이 작업을 준비하고 있습니다.</p>
            </div>
            
            <!-- 제작 중인 케이스 미리보기 복원 -->
            <div class="case-preview-wrap" style="margin: 1.5rem 0;">
                <div class="case-preview-frame" style="width: 140px; border-width: 6px; border-radius: 20px;">
                    <div class="preview-camera" style="width: 34px; height: 34px; top: 8px; left: 8px; border-radius: 8px;"></div>
                    <img id="status-design-img" class="design-overlay" src="" alt="나의 폰케이스 도안" style="display: none;">
                </div>
            </div>
            
            <div class="progress-container" style="margin: 1.5rem 0 0.5rem 0; background: var(--border); border-radius: 10px; overflow: hidden; height: 20px;">
                <div id="progress-bar" style="width: 0%; height: 100%; background: var(--accent); transition: width 0.5s ease;"></div>
            </div>
            <div style="display: flex; justify-content: space-between; margin-bottom: 2rem; color: var(--text-secondary); font-size: 0.9rem; font-weight: bold;">
                <span id="progress-text">진행률: 0%</span>
                <span id="eta-text">예상 남은 시간: 계산 중...</span>
            </div>
            
            <div class="receipt-card">
                <ul class="status-list">
                    <li class="status-item completed">
                        <div class="status-dot"></div>
                        <span>주문 접수 및 도안 전송 완료</span>
                    </li>
                    <li class="status-item active" id="step-2">
                        <div class="status-dot"></div>
                        <span>로봇 팔 원점 복귀 및 그리기 준비</span>
                    </li>
                    <li class="status-item" id="step-3">
                        <div class="status-dot"></div>
                        <span>Doosan M0609 스케치 진행 중</span>
                    </li>
                    <li class="status-item" id="step-4">
                        <div class="status-dot"></div>
                        <span>건조 및 최종 완성</span>
                    </li>
                </ul>
            </div>
            
            <button class="btn btn-secondary" style="margin-top: 2rem;" onclick="app.navigate('login')">
                홈으로 돌아가기
            </button>
        </div>
    `
};

const app = {
    container: document.getElementById('app'),

    init() {
        // file:// 프로토콜로 열면 fetch가 실패하므로 경고
        if (location.protocol === 'file:') {
            alert('⚠️ 주의: index.html을 직접 더블클릭하셨습니다.\n\n서버에 연결하려면 다음 명령어로 웹서버를 실행해주세요:\n\npython -m http.server 8080\n\n그 다음 브라우저에서 http://localhost:8080 으로 접속하세요.');
        }

        // 토스트 요소 생성
        const toast = document.createElement('div');
        toast.id = 'toast';
        toast.className = 'toast';
        document.body.appendChild(toast);

        // 새로고침 후에도 주문 ID 복구
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
        this.navigate(initialRoute, true);
    },

    navigate(route, replace = false) {
        if (replace) history.replaceState({ route: route }, '', '#' + route);
        else history.pushState({ route: route }, '', '#' + route);
        this.render(route, true);
    },

    goBack() { history.back(); },

    render(route, shouldScroll = true) {
        let content = '';
        switch (route) {
            case 'main': content = Views.Main(); break;
            case 'login': content = Views.Login(); break;
            case 'orderStep1': content = Views.OrderStep1(); break;
            case 'orderStep2': content = Views.OrderStep2(); break;
            case 'checkout': content = Views.Checkout(); break;
            case 'status': content = Views.Status(); break;
        }

        this.container.innerHTML = content;
        feather.replace();

        // 페이지 이동 시에만 맨 위로 스크롤
        if (shouldScroll) {
            window.scrollTo(0, 0);
        }

        if (route === 'orderStep2') this.canvasManager.init();
        if (route === 'status') this.simulateStatusProcess();
    },

    toggleAccordion(brand) {
        if (store.activeAccordion === brand) store.activeAccordion = null; // 닫기
        else store.activeAccordion = brand; // 열기
        this.render('orderStep1', false); // 스크롤 방지
    },

    setOrderData(key, value) {
        store.order[key] = value;
        this.render('orderStep1', false); // 스크롤 방지
    },

    setBrushColor(color) {
        store.canvas.color = color;
        // 팔레트 아이콘 선택 상태 변경
        document.querySelectorAll('.color-swatch').forEach(el => {
            if (el.dataset.color === color) el.classList.add('selected');
            else el.classList.remove('selected');
        });
        // 커스텀 색상 선택기 값 동기화
        const picker = document.getElementById('brush-color');
        if (picker) picker.value = color;
    },

    saveCanvasAndNext() {
        const canvas = document.getElementById('drawing-canvas');
        
        // 흰색 배경을 포함한 이미지 생성을 위해 임시 캔버스 사용
        const tempCanvas = document.createElement('canvas');
        tempCanvas.width = canvas.width;
        tempCanvas.height = canvas.height;
        const tCtx = tempCanvas.getContext('2d');
        
        // 배경을 흰색으로 채움
        tCtx.fillStyle = "#ffffff";
        tCtx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);
        
        // 그 위에 사용자가 그린 내용 그리기
        tCtx.drawImage(canvas, 0, 0);
        
        // 저장 (JPEG 또는 PNG)
        store.order.designDataUrl = tempCanvas.toDataURL('image/jpeg', 0.9);
        this.navigate('checkout');
    },

    async processPayment() {
        const btn = document.getElementById('pay-btn');
        btn.innerHTML = '<i data-feather="loader" style="animation: spin 1s linear infinite;"></i> 결제 처리 중...';
        btn.disabled = true;
        feather.replace();

        const result = await uploadOrderToServer(store.order.designDataUrl, store.order);

        if (result === true) {
            this.showToast("결제가 완료되었습니다!");
            this.navigate('status');
        } else {
            // result가 문자열(에러 메시지)인 경우 포함
            const errorMsg = typeof result === 'string' ? result : "서버 연결 오류";
            this.showToast(`오류: ${errorMsg}`);
            btn.innerHTML = '결제하기';
            btn.disabled = false;
        }
    },

    showToast(message) {
        const toast = document.getElementById('toast');
        toast.innerText = message;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 3000);
    },

    async simulateStatusProcess() {
        document.getElementById('status-title').innerText = '로봇 대기 중...';

        // 서버의 실제 로봇 상태를 폴링(1초 주기)
        const checkStatus = async () => {
            if (!store.currentOrderId) return;
            try {
                const res = await fetch(`${API_BASE_URL}/api/orders`);
                const orders = await res.json();
                const myOrder = orders.find(o => o.id === store.currentOrderId);

                if (myOrder) {
                    const progress = myOrder.progress || 0;
                    const eta = myOrder.estimatedTime || 0;

                    // 그려진 도안 이미지 표시 (백엔드 경로에 맞춰 /uploads/ 추가)
                    const imgEl = document.getElementById('status-design-img');
                    if (imgEl && myOrder.image_path) {
                        imgEl.src = `${API_BASE_URL}/uploads/${myOrder.image_path}`;
                        imgEl.style.display = 'block';
                    }

                    document.getElementById('progress-bar').style.width = `${progress}%`;
                    document.getElementById('progress-text').innerText = `진행률: ${progress}%`;

                    if (eta > 0) {
                        const mins = Math.floor(eta / 60);
                        const secs = eta % 60;
                        const timeStr = mins > 0 ? `${mins}분 ${secs}초` : `${secs}초`;
                        document.getElementById('eta-text').innerText = `예상 남은 시간: 약 ${timeStr}`;
                    } else if (progress >= 100) {
                        document.getElementById('eta-text').innerText = '작업 완료';
                    }

                    if (myOrder.status === 'processing') {
                        document.getElementById('status-title').innerText = '로봇이 그림 그리는 중... ✍️';
                        document.getElementById('status-subtitle').innerText = '조금만 기다려주세요.';
                        document.getElementById('step-2').classList.replace('active', 'completed');
                        document.getElementById('step-3').classList.add('active');
                        feather.replace();
                    } else if (myOrder.status === 'done') {
                        document.getElementById('progress-bar').style.width = '100%';
                        document.getElementById('progress-text').innerText = '진행률: 100%';
                        document.getElementById('eta-text').innerText = '작업 완료';

                        document.getElementById('status-title').innerText = '제작 완료! 🎉';
                        document.getElementById('status-subtitle').innerText = '폰케이스가 완성되었습니다.';
                        const spinner = document.getElementById('robot-spinner');
                        spinner.className = 'status-icon-large success';
                        spinner.innerHTML = '<i data-feather="check"></i>';
                        document.getElementById('step-3').classList.replace('active', 'completed');
                        document.getElementById('step-4').classList.add('completed');
                        feather.replace();
                        this.showToast("모든 로봇 팔 작업이 완료되었습니다!");
                        return; // 상태 확인 종료
                    }
                }
            } catch (e) {
                console.error("상태 확인 실패:", e);
            }

            // 아직 done이 아니면 1초 뒤에 다시 확인
            setTimeout(checkStatus, 1000);
        };

        checkStatus();
    },

    // Canvas 관리 객체 (Undo 기능 추가)
    canvasManager: {
        canvas: null,
        ctx: null,

        init() {
            this.canvas = document.getElementById('drawing-canvas');
            if (!this.canvas) return;

            this.ctx = this.canvas.getContext('2d', { willReadFrequently: true });
            const rect = this.canvas.parentElement.getBoundingClientRect();
            this.canvas.width = rect.width * 2;
            this.canvas.height = rect.height * 2;
            this.ctx.scale(2, 2);

            // 투명 배경 유지 (카메라 구멍, 드로잉 가이드가 보여야 함)
            this.ctx.clearRect(0, 0, rect.width, rect.height);
            this.ctx.lineCap = 'round';
            this.ctx.lineJoin = 'round';

            // 초기 상태 저장
            store.canvas.history = [];
            this.saveState();

            this.bindEvents();
        },

        saveState() {
            // 현재 캔버스 데이터를 배열에 저장 (Undo용)
            store.canvas.history.push(this.canvas.toDataURL('image/png'));
            // 최대 히스토리 개수 제한
            if (store.canvas.history.length > 20) store.canvas.history.shift();
        },

        undo() {
            if (store.canvas.history.length <= 1) {
                app.showToast("더 이상 되돌릴 수 없습니다.");
                return;
            }
            // 현재 상태 버리고 이전 상태 가져오기
            store.canvas.history.pop();
            const previousState = store.canvas.history[store.canvas.history.length - 1];

            const img = new Image();
            img.src = previousState;
            img.onload = () => {
                this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
                this.ctx.drawImage(img, 0, 0, this.canvas.width / 2, this.canvas.height / 2);
            };
            app.showToast("실행 취소 완료");
        },

        loadImage(event) {
            const file = event.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = (e) => {
                const img = new Image();
                img.onload = () => {
                    // 이미지를 캔버스 크기에 맞게 스케일링 (여백 10% 남김)
                    const rect = this.canvas.parentElement.getBoundingClientRect();
                    const scale = Math.min((rect.width * 0.8) / img.width, (rect.height * 0.8) / img.height);
                    const drawWidth = img.width * scale;
                    const drawHeight = img.height * scale;

                    // 캔버스 정중앙에 배치
                    const x = (rect.width - drawWidth) / 2;
                    const y = (rect.height - drawHeight) / 2;

                    this.ctx.drawImage(img, x, y, drawWidth, drawHeight);
                    this.saveState(); // 히스토리에 저장
                    app.showToast("이미지가 캔버스에 추가되었습니다!");
                    event.target.value = ''; // 동일한 파일 다시 선택 가능하도록 초기화
                };
                img.src = e.target.result;
            };
            reader.readAsDataURL(file);
        },

        bindEvents() {
            const getPos = (e) => {
                const rect = this.canvas.getBoundingClientRect();
                const clientX = e.touches ? e.touches[0].clientX : e.clientX;
                const clientY = e.touches ? e.touches[0].clientY : e.clientY;
                return { x: clientX - rect.left, y: clientY - rect.top };
            };

            const startDraw = (e) => {
                e.preventDefault();
                store.canvas.isDrawing = true;
                const pos = getPos(e);
                this.ctx.beginPath();
                this.ctx.moveTo(pos.x, pos.y);
            };

            const draw = (e) => {
                e.preventDefault();
                if (!store.canvas.isDrawing) return;
                const pos = getPos(e);

                this.ctx.lineWidth = document.getElementById('brush-size').value;
                this.ctx.strokeStyle = document.getElementById('brush-color').value;

                this.ctx.lineTo(pos.x, pos.y);
                this.ctx.stroke();
            };

            const stopDraw = () => {
                if (store.canvas.isDrawing) {
                    store.canvas.isDrawing = false;
                    this.ctx.closePath();
                    this.saveState(); // 그리기가 끝날 때마다 상태 저장
                }
            };

            this.canvas.addEventListener('mousedown', startDraw);
            this.canvas.addEventListener('mousemove', draw);
            this.canvas.addEventListener('mouseup', stopDraw);
            this.canvas.addEventListener('mouseout', stopDraw);

            this.canvas.addEventListener('touchstart', startDraw, { passive: false });
            this.canvas.addEventListener('touchmove', draw, { passive: false });
            this.canvas.addEventListener('touchend', stopDraw);
        },

        clear() {
            const rect = this.canvas.parentElement.getBoundingClientRect();
            this.ctx.clearRect(0, 0, rect.width, rect.height);
            this.saveState();
            app.showToast("캔버스가 초기화되었습니다.");
        }
    }
};

window.addEventListener('DOMContentLoaded', () => { app.init(); });
