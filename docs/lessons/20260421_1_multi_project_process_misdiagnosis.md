# 다중 프로젝트 공존 서버에서 타 프로젝트 프로세스를 좀비로 오판

- **발생일**: 2026-04-21
- **심각도**: HIGH (오판 그대로 kill 집행 시 타 프로젝트 무중단 서비스 중단)
- **카테고리**: 운영 / 진단 절차

## 증상

`cto health` 결과에서 t3.micro 메모리 80% 사용 확인 후 상세 진단 중 `ps -eo ... --sort=-rss`로 최상위 프로세스를 확인:

```
PID    USER    RSS     CMD
356167 ubuntu  299704  /usr/bin/python3 -m services.execution.runner
378658 ubuntu  118540  /home/ubuntu/BitCoin_Trade/.venv/bin/python -u scripts/daily_live.py --realtime
```

두 번째(378658)가 btc-trader.service의 MainPID임을 확인한 뒤, 첫 번째(356167)에 대해:
- BitCoin_Trade 리포에는 `services/execution/runner.py` 파일이 없음
- `docs/decisions`, `docs/lessons`, WBS에 `execution.runner` 언급 없음
- `systemctl list-units` 스캔에서 btc-trader만 체크함

→ "systemd 무소속 좀비 프로세스, kill 시 293MB 즉시 회수 가능"으로 **잘못 보고**했다.

## 원인

동일 EC2(t3.micro)에 BitCoin_Trade / Stock_Trade / Blog_Income 세 프로젝트가 공존 중이며, `/home/ubuntu/Stock_Trade/services/execution/runner.py`는 **Stock_Trade의 메인 매매 프로세스**였다. 근거:

1. `/proc/356167/cwd` → `/home/ubuntu/Stock_Trade`
2. `/etc/systemd/system/stock-trader.service`의 `ExecStart=/usr/bin/python3 -m services.execution.runner`
3. `systemctl list-units ... running`에 `stock-trader.service`가 **이미 보였으나** BitCoin_Trade 중심 시야에서 놓침
4. Stock_Trade cron에 `# 16:00 일일 매매 → systemd runner.py가 처리 (중복 방지로 cron 제거)` 주석 존재
5. `MEMORY.md`에도 "Stock_Trade 프로젝트 — 키움증권 REST API 한국 주식 자동매매, Phase 3까지 구현 완료" 기록

즉 "BitCoin_Trade 저장소에 파일이 없으니 좀비"라는 추론 자체가 잘못되었다 — 서버는 여러 저장소를 동시에 호스팅할 수 있다.

## 수정

- [x] 진단 정정: PID 356167은 Stock_Trade 정상 프로세스, kill 금지. 메모리 80%는 양 봇 동거의 정상 결과(가용 174MB는 교훈 #5 기준치 통과).
- [x] 본 lesson 기록.
- [x] CLAUDE.md 교훈 요약 테이블에 #17로 추가.

## 검증 규칙

서버 프로세스가 "어떤 서비스에도 속하지 않는다"고 판정하기 전에 다음을 모두 확인해야 한다:

1. `sudo readlink /proc/<PID>/cwd` — 작업 디렉터리로 소유 프로젝트 파악
2. `sudo readlink /proc/<PID>/exe` — 실행 바이너리(보통 python 인터프리터 경로)
3. `grep -rE '<module-or-script>' /etc/systemd/system/ /lib/systemd/system/` — 전체 systemd unit에서 ExecStart 역탐색
4. `systemctl list-units --state=running` **전체 결과를 종목별로** 검토 (btc-trader만 쳐다보지 말 것)
5. 서버 `/home/<user>/` 하위 모든 리포의 `docs/decisions`·`docs/lessons`·`crontab -l` 스캔

위 중 하나라도 "해당 프로세스와 연결되는 정황"이 있으면 좀비 판정 금지. kill 제안은 **위 5개 전수 조사 결과를 보고에 첨부한 뒤**에만 가능.

## 교훈

- 단일 프로젝트 정신모델로 서버를 바라보면 공존 중인 타 프로젝트의 핵심 서비스를 좀비로 오판할 수 있다.
- RSS 상위 프로세스는 "kill 후보"가 아니라 "점유 소유권 확인 후보"로 먼저 취급한다.
- 교차 검증은 "현재 리포의 파일 존재" 뿐 아니라 "서버 전체의 systemd unit / cron / proc cwd"까지 포함해야 한다.
- 앞선 `cto health` 출력에 `stock-trader.service loaded active running`이 이미 기록되어 있었음. **이전 단계의 출력을 다음 단계에서 재활용하지 않은 것**이 1차 원인.

## 연관 교훈

- 교훈 #5 (t3.micro 스왑 + 메모리 예산) — 본 건은 그 연장선의 진단 실패
- 교훈 #10 (state ↔ balance 정합성) — "소유권 확인" 사고방식의 공통 적용
