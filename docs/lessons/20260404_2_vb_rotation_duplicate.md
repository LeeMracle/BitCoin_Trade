# VB 일일 회전 중복 실행 (서비스 재시작 시)

- **발생일**: 2026-04-04
- **심각도**: HIGH
- **카테고리**: 코드 / 상태 관리

## 증상

4월 4일 하루에 VB 일일 회전이 4회 실행됨 (정상은 1회).
서비스 재시작(배포)마다 `_vb_daily_rotation()`이 재실행되어 기존 VB 포지션을 전량 청산 후 재매수 반복.

DRY-RUN 모드였기에 실제 손실은 없었으나, 실전 전환 시 수수료 낭비 + 의도치 않은 청산 발생했을 것.

## 원인

`_vb_daily_rotation()`에 날짜 체크가 없었음.
`_refresh_levels()` → `_vb_daily_rotation()` 호출 구조에서, `_refresh_levels`는 레벨 갱신 시마다 실행되므로 서비스 재시작마다 VB 회전이 반복됨.

```python
# 수정 전 — 날짜 체크 없음
if VB_ENABLED and not IS_DAYTRADING:
    await self._vb_daily_rotation()
```

## 수정

`_vb_daily_rotation()` 진입부에 날짜 체크 추가:
```python
today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
last_rotation = self.state.get("vb_last_rotation_date", "")
if last_rotation == today:
    print(f"  [VB] 오늘({today}) 이미 회전 완료 — 건너뜀")
    return
self.state["vb_last_rotation_date"] = today
save_state(self.state)
```

## 검증규칙

1. `_vb_daily_rotation` 함수 내에 `vb_last_rotation_date` 문자열이 존재하는지 확인
2. 서비스 재시작 후 VB 회전 로그가 1회만 나타나는지 확인
3. 상태 파일에 `vb_last_rotation_date` 필드가 저장되는지 확인

## 교훈

`_refresh_levels()`처럼 여러 번 호출될 수 있는 함수 안에서 "1일 1회" 작업을 실행할 때는 반드시 날짜 체크 + 상태 저장이 필요. 서비스 재시작, 배포, 에러 복구 등으로 예상보다 자주 호출될 수 있다.
