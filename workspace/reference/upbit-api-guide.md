# 업비트 Open API 가이드

> 조사일: 2026-03-25
> 출처: [업비트 개발자 센터](https://docs.upbit.com/kr), [Open API 안내](https://upbit.com/service_center/open_api_guide)

---

## 1. API 키 발급 절차

1. 업비트 회원가입 완료
2. KYC (고객 확인) 완료 — 신분증 (주민등록증 또는 운전면허증)
3. 2채널 인증(2FA) 완료 — 카카오톡 / 네이버 / 하나인증서
4. PC 웹 환경: 마이페이지 > Open API 관리
5. 권한 선택 → 공개 IP 등록 → 2FA 인증 → 키 발급
6. **Secret Key는 발급 화면에서만 확인 가능** — 반드시 즉시 저장

> 모바일 앱에서는 발급 불가. PC 웹 환경 전용.

---

## 2. API 권한 종류

| 권한 | 설명 | 자동매매 필요 여부 |
| --- | --- | --- |
| 자산 조회 | 잔고, 보유 코인 | 필수 |
| 주문 조회 | 주문 내역 | 필수 |
| 주문하기 | 매수/매도/취소 | 필수 |
| 입금 조회 | 입금 내역 | 선택 |
| 출금 조회 | 출금 내역 | 선택 |
| 출금하기 | 출금 실행 | **불필요 — 부여 금지** |

**권장**: 자동매매용 키에는 `출금하기` 권한을 절대 부여하지 말 것.

---

## 3. IP 허용 설정 (중요: 유동 IP 제약)

### 정책
- **공개(공인) IPv4만 등록 가능** — 사설 IP(`192.168.x.x` 등) 불가
- **키당 최대 10개 IP** 등록 가능
- IP 완전 해제(모든 IP 허용) 옵션 **없음** — 반드시 1개 이상 등록 필요
- IP가 변경되면 API 호출이 즉시 차단됨

### 유동 IP(동적 IP) 사용 시 문제
로컬 PC에 고정 IP가 없으면 ISP가 IP를 재할당할 때 API가 차단됨.

### 해결 방법 (권장 순서)

#### 방법 1: ISP 고정 IP 신청 (가장 안정적)
- 인터넷 서비스 제공사에 고정 IP 신청
- 소규모 추가 비용 발생 (월 몇 천 원 수준)
- 자동매매 운영 시 가장 안정적

#### 방법 2: DDNS + 스크립트 (비용 없음)
- ipTIME 공유기 DDNS 또는 No-IP, DuckDNS 사용
- IP 변경 시 업비트 API 허용 IP를 자동 업데이트하는 스크립트 작성
- 구현 난이도: 중간

```python
# IP 변경 감지 → 업비트 API 키 허용 IP 자동 갱신 스크립트 (구현 필요)
# 참고: 업비트는 Open API 관리 페이지에서 IP 수동 변경 필요
# (자동 갱신 공식 API 없음 → 브라우저 자동화 또는 수동 변경 필요)
```

#### 방법 3: 여러 IP 미리 등록 (임시 대응)
- ISP에서 자주 할당받는 IP 대역을 미리 최대 10개 등록
- IP가 그 범위 안에서 변경되면 영향 없음
- 예측 불가능한 IP 변경 시 차단될 수 있음

#### 방법 4: 클라우드 서버 경유 (장기 운영 권장)
- AWS/NCP 등 고정 IP를 가진 클라우드 서버에서 봇 실행
- 로컬 PC는 개발/모니터링용으로만 사용
- Phase 3~4 전환 시 권장 아키텍처

---

## 4. API 인증 방식

```
Authorization: Bearer {JWT_TOKEN}
```

JWT 생성 방법 (Python):
```python
import jwt
import uuid

payload = {
    'access_key': ACCESS_KEY,
    'nonce': str(uuid.uuid4()),
}
# 쿼리 파라미터가 있는 경우 query_hash 필드 추가 필요
token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')
headers = {'Authorization': f'Bearer {token}'}
```

ccxt를 사용하면 인증 자동 처리됨:
```python
import ccxt
upbit = ccxt.upbit({
    'apiKey': 'YOUR_ACCESS_KEY',
    'secret': 'YOUR_SECRET_KEY',
})
```

---

## 5. Rate Limit

| 그룹 | 한도 | 적용 범위 |
| --- | --- | --- |
| 기본 | 29 req/sec (1,799 req/min) | 시세 조회, 자산 조회 등 |
| 주문 | 4 req/sec (59 req/min) | 주문 생성/취소 |

- 초과 시: `429 Too Many Requests`
- 응답 헤더 `Remaining-Req`로 잔여 횟수 확인 가능

---

## 6. 이 프로젝트의 권장 설정

### 개발/백테스트 단계 (현재)
- API 키 불필요 — 공개 시세 API(인증 없음)로 OHLCV 조회 가능
- `ccxt.upbit()` 인증 없이 `fetch_ohlcv()` 호출 가능

### 페이퍼 트레이딩 단계 (Phase 3)
- **조회 전용 키** 발급 (자산조회 + 주문조회)
- 현재 IP 등록
- 유동 IP 대응: DDNS 또는 ISP 고정 IP 신청

### 실전 거래 단계 (Phase 4)
- **별도 거래 키** 발급 (주문하기 권한 추가)
- **클라우드 서버** 이전 권장 (고정 IP 확보)
- 출금하기 권한 **절대 포함 금지**

---

## 7. 참고 링크

- [업비트 개발자 센터](https://docs.upbit.com/kr)
- [Open API 관리 (마이페이지)](https://upbit.com/mypage/open_api_management)
- [Open API 안내](https://upbit.com/service_center/open_api_guide)
- [ccxt upbit 구현](https://github.com/ccxt/ccxt/blob/master/python/ccxt/upbit.py)
- [업비트 CCXT 연동 가이드](https://global-docs.upbit.com/docs/ccxt-library-integration-guide)
