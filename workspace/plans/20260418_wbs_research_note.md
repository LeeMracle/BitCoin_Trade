# main 세션 WBS 업데이트 지시 — P5-02/P5-03 리서치 문서 경로 반영

- **작성일(KST)**: 2026-04-18
- **작성자**: pdca-builder #D (자비스 PDCA Do 단계)
- **목적**: WBS.md 직접 수정은 main이 담당. 본 파일은 main 세션이 WBS 업데이트 시 참고할 경로 정보를 전달한다.

---

## WBS 반영 요청 내용

### P5-02 "VB 파라미터 최적화"

- **현재 상태**: 실행 대기 (하락장 지속)
- **비고란에 추가할 경로**: `docs/research/20260418_vb_param_optimization.md`
- **메모**: 착수 트리거 — BTC > EMA200 3일 연속 + VB 거래 15건 이상 누적

### P5-03 "알트 펌프 서핑 재검토"

- **현재 상태**: 실행 대기 (하락장 지속)
- **비고란에 추가할 경로**: `docs/research/20260418_alt_pump_review.md`
- **메모**: 착수 트리거 — BTC BULL 레짐 7일 유지 또는 F&G ≥ 50 3일

### P6-13 "lint_history.jsonl 누적 + 주간 통계"

- **현재 상태**: 구현 완료
- **비고란에 추가할 경로**: `scripts/lint_history.py`, `tests/scripts/test_lint_history.py`
- **첫 레코드 확인**: `workspace/lint_history.jsonl` 생성됨

---

## main 세션 액션

`docs/00.보고/WBS.md` 에서 P5-02, P5-03, P6-13 항목 비고란에 위 경로를 추가한다.
