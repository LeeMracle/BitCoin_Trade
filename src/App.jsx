const stack = {
  local: [
    "PM Orchestrator와만 소통",
    "전략 연구 및 백테스트",
    "코드 수정, Git 관리, 배포",
    "대시보드와 로그 확인",
  ],
  vpsCore: [
    "종목 스캐너",
    "신호 엔진",
    "주문 엔진",
    "포지션 관리자",
    "리스크 가드",
  ],
  vpsRuntime: [
    "WebSocket 리스너",
    "스케줄러",
    "환경변수 / API Key",
    "장애 감지",
  ],
  upbit: [
    "Quotation API",
    "Exchange API",
    "실시간 WebSocket",
  ],
  monitor: [
    "Telegram 즉시 알림",
    "체결 / 장애 / 하트비트",
    "일일 손익 요약",
  ],
};

function SectionCard({ tone, title, subtitle, items }) {
  return (
    <section className={`card card--${tone}`}>
      <div className="card__eyebrow">{subtitle}</div>
      <h2>{title}</h2>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function Flow({ label, detail }) {
  return (
    <div className="flow">
      <span className="flow__line" />
      <div className="flow__label">
        <strong>{label}</strong>
        <span>{detail}</span>
      </div>
      <span className="flow__line" />
    </div>
  );
}

export default function App() {
  return (
    <main className="page">
      <div className="hero">
        <div>
          <p className="hero__kicker">SYSTEM ARCHITECTURE</p>
          <h1>업비트 자동매매 운영 구조</h1>
          <p className="hero__body">
            MCP는 제외하고, 실제 운영에 필요한 사용자 흐름, 로컬 연구 환경, VPS 실행
            서버, 업비트 연동, 모니터링 경계를 한 화면에 정리했습니다.
          </p>
        </div>
        <div className="hero__badge">
          <span>운영 원칙</span>
          <strong>로컬은 연구, VPS는 실주문</strong>
        </div>
      </div>

      <section className="architecture">
        <div className="architecture__grid">
          <SectionCard
            tone="local"
            subtitle="USER & LOCAL"
            title="사용자 / 로컬 노트북"
            items={stack.local}
          />

          <div className="vps-cluster">
            <SectionCard
              tone="vps"
              subtitle="EXECUTION"
              title="VPS 실거래 서버"
              items={stack.vpsCore}
            />
            <SectionCard
              tone="runtime"
              subtitle="RUNTIME"
              title="운영 런타임"
              items={stack.vpsRuntime}
            />
          </div>

          <SectionCard
            tone="external"
            subtitle="MARKET ACCESS"
            title="업비트"
            items={stack.upbit}
          />
        </div>

        <div className="flows">
          <Flow label="배포 / 제어" detail="로컬에서 VPS로 코드 배포와 운영 제어" />
          <Flow label="시세 / 주문" detail="VPS가 업비트 시세를 받고 주문을 실행" />
          <Flow label="알림 / 보고" detail="이벤트와 손익 상태를 사용자에게 전달" />
        </div>

        <div className="monitor-wrap">
          <SectionCard
            tone="alert"
            subtitle="MONITORING"
            title="모니터링 / 알림"
            items={stack.monitor}
          />
        </div>
      </section>

      <section className="notes">
        <div className="note">
          <h3>왜 VPS가 필요한가</h3>
          <p>
            업비트 주문 API는 허용 IP 기반 운영이므로, 유동 IP 노트북보다 고정 공인
            IPv4를 가진 VPS가 안정적입니다.
          </p>
        </div>
        <div className="note">
          <h3>키 보관 원칙</h3>
          <p>
            실거래 API Key는 VPS에서만 사용하고, 로컬은 조회·개발·백테스트 중심으로
            유지합니다.
          </p>
        </div>
        <div className="note">
          <h3>리스크 기본값</h3>
          <p>1회 최대손실 1만원, 1일 최대손실 3만원, 동시 보유 종목 3개 기준입니다.</p>
        </div>
      </section>
    </main>
  );
}
