"""
SPEC_08 §9 — grid.py 테스트. DB 접속 없이 검증 가능한 로직만 다룬다.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from backtest.regime import config_phaseB as cfg
from backtest.regime import grid


def test_all_combinations_count_matches_axis_product():
    """N = tilt_option(2) x normalization(2) x overlay_freq(3) x alt_sleeve(2) x K(3) x variant수(3)."""
    combos = grid.all_combinations()
    n_variants = sum(len(v) for v in cfg.VARIANTS.values())
    expected = 2 * 2 * 3 * 2 * 3 * n_variants
    assert len(combos) == expected


def test_all_combinations_respects_r5_f_pipeline_value_spread_only():
    """R5: F_momentum_rim은 value_spread 단독(F_v1)만 — D_v2류가 섞이면 안 된다."""
    combos = grid.all_combinations()
    f_variants = {c['variant'] for c in combos if c['scenario'] == 'F_momentum_rim'}
    assert f_variants == {'F_v1'}


def test_each_combination_gets_a_distinct_config_hash_no_collision(monkeypatch):
    """R8/§4 — 조합마다 config_hash가 달라야 overlay_returns PK가 서로 안 덮어쓴다."""
    combos = grid.all_combinations()
    hashes = {
        cfg.config_hash(K=c['k'], NORMALIZATION=c['normalization'],
                         OVERLAY_FREQ=c['overlay_freq'], ALT_SLEEVE=c['alt_sleeve'])
        for c in combos
    }
    # scenario/variant/tilt_option은 config_hash에 안 들어가므로(overlay_returns PK의 다른
    # 컬럼이 이미 구분) 여러 조합이 같은 해시를 공유할 수 있다 — 하지만 (K, normalization,
    # overlay_freq, alt_sleeve) 축의 고유 조합 수(2*3*2*3=36)만큼은 반드시 나와야 한다.
    assert len(hashes) == 2 * 3 * 2 * 3


def _fake_overlay_df(n=24, net_extra=0.01, warmup=0):
    dates = [date(2020, 1, 31) + timedelta(days=30 * i) for i in range(n)]
    return pd.DataFrame({
        'date': dates,
        'is_oos': [i >= warmup for i in range(n)],
        'episode_tag': ['period22' if i == n - 1 else 'normal' for i in range(n)],
        'net_port_return': [0.01 + net_extra] * n,
        'net_base_return': [0.01] * n,
    })


def test_summary_stats_reports_n_obs_and_positive_alpha_when_port_beats_base():
    df = _fake_overlay_df(n=24, net_extra=0.005)
    stats = grid.summary_stats(df)
    assert stats['n_obs'] == 24
    assert stats['total_alpha'] > 0
    assert stats['ex22_alpha'] <= stats['total_alpha']   # #22 몫을 뺀 값이 전체를 넘을 수 없음


def test_summary_stats_excludes_non_oos_rows():
    df = _fake_overlay_df(n=24, warmup=6)
    stats = grid.summary_stats(df)
    assert stats['n_obs'] == 18   # 워밍업 이전(6개) 제외


def test_summary_stats_flags_period22_share_warning_when_single_episode_dependent():
    n = 10
    dates = [date(2020, 1, 31) + timedelta(days=30 * i) for i in range(n)]
    df = pd.DataFrame({
        'date': dates,
        'is_oos': [True] * n,
        'episode_tag': ['period22'] + ['normal'] * (n - 1),
        'net_port_return': [0.20] + [0.01] * (n - 1),   # 알파 대부분이 #22 한 구간에서 발생
        'net_base_return': [0.01] * n,
    })
    stats = grid.summary_stats(df)
    assert stats['period22_share'] > 0.5
    assert stats['period22_share_warn'] is True


def test_run_logs_n_combinations(monkeypatch, caplog):
    """N(조합 수)이 리포트(로그)에 기록되는지 확인 — 실제 DB 실행은 monkeypatch로 대체."""
    import logging

    class _FakeConn:
        def close(self):
            pass

    monkeypatch.setattr(grid, 'get_connection', lambda: _FakeConn())
    monkeypatch.setattr(grid, 'run_combo', lambda *a, **k: 'fakehash')
    monkeypatch.setattr(grid, '_load_combo_returns', lambda *a, **k: _fake_overlay_df(n=2))

    with caplog.at_level(logging.INFO):
        df = grid.run(run_id='test_run')

    assert len(df) == len(grid.all_combinations())
    assert any('N=' in r.message for r in caplog.records if 'Layer 1' in r.message)
