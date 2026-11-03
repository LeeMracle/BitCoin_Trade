"""이벤트 필터 연구(plan 20261005_6)의 정직성 검증.

1) flagged 타임라인이 **인과적**이다 — 공지가 올라오기 전에는 flagged 가 아니다
2) 공지 해석: 지정/해제 구분, 거래지원 종료는 영구, 심볼 추출, 촉구·기타는 무시
3) ET→UTC 변환이 서머타임 경계에서 맞고, 차단 구간이 [시작, 끝) 이다
4) 정책 훅은 후보를 건너뛸 뿐 원본을 바꾸지 않는다
5) 고정한 데이터 파일이 약속한 모양이다

재현: py -m pytest tests/scripts/test_study_event_filters.py -q
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import study_event_filters as E  # noqa: E402

H = E.HOUR_MS


def ms(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() * 1000


def notice(iso, title):
    return {"listed_at": iso, "title": title}


# ─────────────────────────── 1~2) 타임라인 ───────────────────────────

def test_title_classification():
    c = E.classify_title
    assert c("블라스트(BLAST) 거래 유의 종목 지정 안내") == "FLAG"
    assert c("샌드박스(SAND) 거래 유의 종목 지정 해제 안내") == "UNFLAG"   # '해제'가 FLAG 로 오분류되면 안 된다
    assert c("아이콘(ICX) 거래지원 종료 안내 (10/19 15:00)") == "DELIST"
    assert c("무슨코인(XYZ) 투자 유의 촉구 안내") is None
    assert c("무슨코인(XYZ) 신규 상장 안내") is None


def test_symbol_is_first_alnum_paren_not_date_or_market():
    n = [notice("2026-09-18T16:30:00+09:00", "아이콘(ICX) 거래지원 종료 안내 (10/19 15:00)"),
         notice("2026-08-27T18:19:00+09:00", "ThunderCore 네트워크 종료에 따른 썬더코어(TT) 출금 중단 및 거래지원 종료 예정일 변경 안내 (09/01 09:00 ~)")]
    ev = E.parse_notices(n)
    assert set(ev) == {"ICX", "TT"}


def test_flag_then_unflag_interval_is_half_open_and_causal():
    n = [notice("2026-05-01T12:00:00+09:00", "가나다(AAA) 거래 유의 종목 지정 안내"),
         notice("2026-05-10T12:00:00+09:00", "가나다(AAA) 거래 유의 종목 지정 해제 안내")]
    iv = E.build_intervals(E.parse_notices(n)["AAA"])
    flag_t, unflag_t = E._ms("2026-05-01T12:00:00+09:00"), E._ms("2026-05-10T12:00:00+09:00")
    assert not E.is_flagged(iv, flag_t - 1)                  # 공지 직전: 아직 모른다 (look-ahead 금지)
    assert E.is_flagged(iv, flag_t)                          # 공지 시각부터
    assert E.is_flagged(iv, unflag_t - 1)
    assert not E.is_flagged(iv, unflag_t)                    # 해제 시각부터 해제
    assert not E.is_flagged(iv, unflag_t + 10 * H)


def test_open_flag_stays_flagged_and_delist_is_permanent():
    iv = E.build_intervals(E.parse_notices(
        [notice("2026-05-01T12:00:00+09:00", "가나다(AAA) 거래 유의 종목 지정 안내")])["AAA"])
    assert E.is_flagged(iv, ms(2030, 1, 1))
    iv2 = E.build_intervals(E.parse_notices(
        [notice("2026-05-01T12:00:00+09:00", "가나다(BBB) 거래 유의 종목 지정 안내"),
         notice("2026-05-05T12:00:00+09:00", "가나다(BBB) 거래 유의 종목 지정 해제 안내"),
         notice("2026-06-01T12:00:00+09:00", "가나다(BBB) 거래지원 종료 안내 (7/1 15:00)")])["BBB"])
    assert not E.is_flagged(iv2, ms(2026, 5, 20))            # 해제 후 정상
    assert E.is_flagged(iv2, ms(2026, 6, 1, 4))              # 종료 공지 이후
    assert E.is_flagged(iv2, ms(2040, 1, 1))                 # 영구


def test_delist_without_prior_flag_starts_at_notice():
    iv = E.build_intervals(E.parse_notices(
        [notice("2026-06-01T12:00:00+09:00", "가나다(CCC) 거래지원 종료 안내")])["CCC"])
    assert not E.is_flagged(iv, E._ms("2026-06-01T12:00:00+09:00") - 1)
    assert E.is_flagged(iv, E._ms("2026-06-01T12:00:00+09:00"))


def test_unknown_symbol_is_never_flagged():
    assert E.is_flagged([], ms(2026, 1, 1)) is False


def test_extension_notice_does_not_split_or_end_the_flag():
    """'지정 기간 연장 안내'는 FLAG 로 분류되지만 이미 flagged 이므로 구간을 쪼개지 않는다 (실데이터 7건)."""
    n = [notice("2026-05-01T12:00:00+09:00", "가나다(DDD) 거래 유의 종목 지정 안내"),
         notice("2026-05-20T12:00:00+09:00", "가나다(DDD) 거래 유의 종목 지정 기간 연장 안내"),
         notice("2026-06-10T12:00:00+09:00", "가나다(DDD) 거래 유의 종목 지정 해제 안내")]
    iv = E.build_intervals(E.parse_notices(n)["DDD"])
    assert len(iv) == 1
    assert E.is_flagged(iv, E._ms("2026-05-25T00:00:00+09:00"))
    assert not E.is_flagged(iv, E._ms("2026-06-10T12:00:00+09:00"))


# ─────────────────────────── 3) 매크로 창 ───────────────────────────

def test_et_to_utc_respects_daylight_saving():
    f = lambda d, hm: E.event_instants([d], hm)[0]               # noqa: E731
    assert f("2025-03-19", (14, 0)) == ms(2025, 3, 19, 18, 0)     # EDT(UTC-4): 서머타임 시작(3/9) 이후
    assert f("2024-11-07", (14, 0)) == ms(2024, 11, 7, 19, 0)     # EST(UTC-5): 서머타임 종료(11/3) 이후
    assert f("2024-03-12", (8, 30)) == ms(2024, 3, 12, 12, 30)    # 서머타임 시작(3/10) 직후
    assert f("2024-03-08", (8, 30)) == ms(2024, 3, 8, 13, 30)     # 서머타임 시작 직전(EST)


def test_window_is_half_open_around_event():
    ev = E.event_instants(["2025-03-19"], (14, 0))
    w = E.build_windows(ev)
    t = ev[0]
    assert E.in_windows(w, t - 12 * H)                        # 시작 포함
    assert not E.in_windows(w, t - 12 * H - 1)
    assert E.in_windows(w, t + 12 * H - 1)
    assert not E.in_windows(w, t + 12 * H)                    # 끝 제외
    assert E.in_windows(w, t)


def test_event_total_window_length_is_24h():
    s, e = E.build_windows(E.event_instants(["2025-03-19"], (14, 0)))[0]
    assert (e - s) == 24 * H


# ─────────────────────────── 4) 정책 훅 ───────────────────────────

def test_block_removes_matches_without_mutating_input():
    cands = [{"si": i, "t": i} for i in range(6)]
    snapshot = [dict(c) for c in cands]
    out = E.make_block(lambda c: c["si"] % 2 == 0)(cands, np.random.default_rng(0))
    assert sorted(c["si"] for c in out) == [1, 3, 5]
    assert cands == snapshot                                  # 원본 후보 리스트 불변


def test_block_nothing_keeps_all_candidates():
    cands = [{"si": i, "t": i} for i in range(5)]
    out = E.make_block(lambda c: False)(cands, np.random.default_rng(0))
    assert sorted(c["si"] for c in out) == list(range(5))


# ─────────────────────────── 5) 고정 데이터 ───────────────────────────

def test_frozen_data_files_have_promised_shape():
    notices = json.loads(E.NOTICES.read_text(encoding="utf-8"))
    assert len(notices) == 783 and all("listed_at" in n and "title" in n for n in notices)
    macro = json.loads(E.EVENTS.read_text(encoding="utf-8"))
    assert len(macro["fomc_decision_dates"]) == 24 and len(macro["cpi_release_dates"]) == 35
    assert "2025-08-22" not in macro["fomc_decision_dates"]   # 서면 표결은 회의가 아니다
    assert "_provenance" in macro


def test_real_notices_parse_into_known_current_flags():
    """실제 데이터 스모크: 2026-10-03 BLAST 유의 지정이 반영돼 있고, SAND 는 10-02 에 해제됐다."""
    iv = {s: E.build_intervals(v) for s, v in E.parse_notices(
        json.loads(E.NOTICES.read_text(encoding="utf-8"))).items()}
    assert E.is_flagged(iv["BLAST"], ms(2026, 10, 4))
    assert not E.is_flagged(iv["BLAST"], ms(2026, 9, 1))
    assert not E.is_flagged(iv["SAND"], ms(2026, 10, 4))
    assert E.is_flagged(iv["SAND"], ms(2026, 9, 10))
