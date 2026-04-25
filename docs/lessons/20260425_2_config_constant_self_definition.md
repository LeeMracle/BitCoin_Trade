# 모듈이 config.py 상수를 import하지 않고 자체 정의 — 동기화 누락 위험

- **발생일**: 2026-04-25 (감지 시점)
- **심각도**: HIGH (운영 변경 시 즉시 일치하지 않으면 일부 경로만 적용)
- **카테고리**: 코드 구조 / 동기화

## 증상

`MAX_POSITIONS 5 → 7` 변경 작업의 cto review 중 발견:

```
services/execution/config.py:56     MAX_POSITIONS = 7   # 변경 의도가 여기
services/execution/multi_trader.py:33 MAX_POSITIONS = 5   # 자체 상수, 미반영
services/execution/realtime_monitor.py:34 from services.execution.config import MAX_POSITIONS  # 정상
scripts/dryrun_vol_reversal.py:23   MAX_POSITIONS = 5   # DRY-RUN, 영향 작음
```

`config.py:56` 1줄만 변경하면 realtime_monitor 경로(메인 실시간 감시)는 7슬롯 적용되나, **multi_trader 경로(daily_check, 일일 스캔, --status, --scan)는 여전히 5슬롯**. 두 코드 경로가 같은 봇을 지칭하는 줄 알았으나 실제로는 슬롯 한도 진실 원천(source of truth)이 둘로 갈라져 있었음.

## 원인

1. multi_trader.py가 *역사적으로* 독립 단위로 작성됨 — 초기 단계엔 multi_trader가 단독 실행 스크립트였고 config.py가 미존재했을 가능성
2. config.py 도입 시 일부 모듈만 import 전환됨 (realtime_monitor.py 전환됨, multi_trader.py 미전환)
3. lint/검증 규칙이 "동일 상수명이 여러 파일에 정의됨"을 잡지 못함

근본 원인은 **단일 진실 원천(SoT) 위반** + **자동 검증 규칙 부재**.

## 수정

### 즉시 (이번 변경)
- [x] multi_trader.py:33 의 자체 상수도 7로 동기화
- [x] 동기화 의무를 인지시키는 주석 추가:
  ```
  # 설정 — config.py:MAX_POSITIONS 와 동기화 필수 (lessons/20260425_2 참조)
  # TODO: config.py 직접 import 통일 권장 (자체 상수 패턴은 동기화 누락 위험)
  ```
- [x] cto health 사후 검증으로 양쪽 일치 확인 (각 파일 grep 결과 7)

### 단기 (1주 내 권장)
- [ ] multi_trader.py:33 을 `from services.execution.config import MAX_POSITIONS` 로 전환
  - 위험: multi_trader.py 의 다른 동작이 영향받지 않는지 확인 필요
  - 장점: 향후 변경 시 1곳만 수정
- [ ] dryrun_vol_reversal.py:23 도 동일 패턴 점검·전환 (DRY-RUN이라 우선순위 낮음)

### 중기 (별도 plan)
- [ ] services/execution/ 전체에서 "config.py에 정의된 상수와 동일한 이름의 자체 상수" 일괄 grep → 모두 import로 전환
- [ ] pre_deploy_check.py에 검증규칙 추가 (아래)

## 검증규칙 (`scripts/pre_deploy_check.py` 추가 대상)

config.py에 정의된 운영 상수 목록(MAX_POSITIONS, POSITION_RATIO, MIN_VOLUME_KRW, DONCHIAN_PERIOD 등)에 대해:

1. services/execution/ 모든 .py 파일을 grep
2. 동일 상수명이 `from services.execution.config import` 가 아닌 자체 정의로 발견되면 **에러**
3. 단, 명시적 화이트리스트(예: 의도적 별도 정의)는 주석 마커 `# CONFIG_CONST_OVERRIDE` 로 면제

의사코드:
```python
SHARED_CONSTS = ["MAX_POSITIONS", "POSITION_RATIO", "MIN_VOLUME_KRW", ...]
for py in glob("services/execution/*.py"):
    if py.endswith("config.py"): continue
    text = py.read_text()
    for const in SHARED_CONSTS:
        if re.search(rf"^{const}\s*=", text, re.M):
            if "CONFIG_CONST_OVERRIDE" not in text:
                errors.append(f"{py}: {const} 자체 정의 (config.py와 동기화 누락 위험)")
```

## 교훈

1. **config 상수는 진실 원천이 1곳이어야 한다**. 자체 정의는 *반드시* 명시적 주석으로 의도를 표시하고, 그렇지 않은 경우 자동 검증으로 차단.
2. **운영 변경 권장 시 grep 1회 필수**: 변경 대상 상수명을 먼저 코드베이스 전체에서 grep해서 **모든 정의 위치**를 확인. config.py 1줄만 바꾸는 권장은 위험.
3. **cto review 같은 외부 시야가 패턴 발견에 유효**. 동일 세션이 코드를 작성·검토하면 자체 상수의 존재를 놓치기 쉽다 — 자기평가 금지 원칙(cross_review_policy)의 가치 입증 사례.
4. **import 통일은 "리팩토링"이 아니라 "운영 안전성 작업"**. 향후 변경 시 한 곳만 바꾸면 되도록 *지금* 정리해두는 비용이, 다음 변경 시 양쪽 동기화 누락의 사고 비용보다 작다.

## 관련 교훈

- 교훈 #4 (CLAUDE.md ↔ config.py ↔ 서버 동기화) — 본 건은 "config.py 내부 ↔ 모듈 자체 상수" 동기화 문제. 교훈 #4의 변종이라기보다 *상위 개념의 한 케이스*.
- 교훈 #18 (venv 디렉터리 리네임 시 crontab/systemd 동시 갱신) — 동일하게 "다중 위치에 있는 동일 정보"의 동기화 문제. 본 건도 같은 패턴.

## 참조

- `services/execution/config.py:56`
- `services/execution/multi_trader.py:32-34` (동기화 주석 + TODO 추가)
- `services/execution/realtime_monitor.py:34` (모범 사례 — config import)
- `workspace/reports/20260425_2_increase_trade_frequency.md` (cto review HIGH 이슈 발견 보고)
