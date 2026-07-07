"""
SPEC_07 §7-5 — 플롯 + 마크다운 리포트.

플롯(지표별): 상단=지표+누적 rel_vs_large 이중축 / 하단=산점도 indicator_t vs
rel_vs_large_{t+1} + 회귀선. PRIMARY_SCENARIOS[0](D_rim_only)을 기준 시각화로 쓴다.

리포트: experiments/runs/YYYY.MM.DD._REGIME_PHASE_A.md
  - .md는 git 커밋(옵션 B, 기존 experiments/runs/*.md 관례와 동일)
  - 대용량 플롯 PNG는 .gitignore로 제외 (experiments/runs/*.png)

실행: venv/bin/python -m backtest.regime.dashboard --indicators-run-id <id> [--mtm-run-id mtm_v1]
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from backtest.regime.analyze import (
    HORIZONS,
    load_indicator_df,
    load_monthly_returns,
    run_analysis,
)
from backtest.regime.config_regime import ARCHIVE_SCENARIOS, PRIMARY_SCENARIOS
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/runs')
INDICATORS = ('value_spread', 'size_val_gap', 'illiq_discount',
              'size_mom_6m', 'breadth_ma200', 'mega_cap_concentration')


# ── 플롯 ────────────────────────────────────────────────────────────────────

def plot_indicator(indicator_df, monthly_df, indicator: str, scenario: str, out_path: Path) -> None:
    if indicator not in indicator_df.columns or monthly_df.empty:
        log.warning('%s: 데이터 없어 플롯 건너뜀', indicator)
        return

    series = indicator_df[indicator].dropna()
    rel = monthly_df['rel_vs_large'].dropna()
    cum_rel = (1 + rel).cumprod() - 1

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(9, 8))

    # 서버(Linux)에 한글 폰트가 없어 matplotlib 렌더링이 깨짐(Glyph missing) — 플롯 텍스트는
    # 영문으로만 구성한다. 한글 설명은 build_report()의 마크다운 리포트 쪽에서 담당.
    ax_top.plot(series.index, series.values, color='tab:blue', label=indicator)
    ax_top.set_ylabel(indicator, color='tab:blue')
    ax_top2 = ax_top.twinx()
    ax_top2.plot(cum_rel.index, cum_rel.values, color='tab:red', label=f'cumulative rel_vs_large ({scenario})')
    ax_top2.set_ylabel('cumulative rel_vs_large', color='tab:red')
    ax_top.set_title(f'{indicator} vs {scenario} cumulative relative return')

    merged = series.to_frame('x').join(rel.shift(-1).rename('y')).dropna()
    if len(merged) >= 5:
        ax_bot.scatter(merged['x'], merged['y'], alpha=0.6, s=20)
        coef = np.polyfit(merged['x'], merged['y'], 1)
        xs = np.linspace(merged['x'].min(), merged['x'].max(), 50)
        ax_bot.plot(xs, np.polyval(coef, xs), color='tab:orange')
    ax_bot.set_xlabel(f'{indicator}(t)')
    ax_bot.set_ylabel('rel_vs_large(t+1)')
    ax_bot.set_title('t -> t+1 scatter + regression line (reference only; gate uses HAC regression)')

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    log.info('플롯 저장: %s', out_path)


# ── 리포트 ───────────────────────────────────────────────────────────────────

def _fmt(x, pct: bool = False) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 'N/A'
    return f'{x * 100:.2f}%' if pct else f'{x:.4f}'


def _gate_row(label: str, results: dict) -> str:
    cells = []
    for scenario in PRIMARY_SCENARIOS:
        res = results.get(scenario, {})
        cells.append('PASS' if res.get(label) else ('FAIL' if res.get('ok') else 'N/A'))
    return ' | '.join(cells)


def build_report(analysis: dict, indicators_run_id: str, mtm_run_id: str) -> str:
    results = analysis['scenarios']
    lines = []
    lines.append('# Phase A 레짐 진단 리포트 (SPEC_07)')
    lines.append('')
    lines.append(f'> 실행일: {date.today().isoformat()}')
    lines.append(f'> indicators_run_id: `{indicators_run_id}` / mtm_run_id: `{mtm_run_id}`')
    lines.append('> 라벨링: `[검증된 사실]` = 코드/데이터 확인 · `[Claude 의견]` = 설계 판단')
    lines.append('')
    lines.append('## §9 Phase B 진입 게이트 판정')
    lines.append('')
    lines.append(f'| # | 조건 | {" | ".join(PRIMARY_SCENARIOS)} | 유형 |')
    lines.append('|---|---|' + '---|' * len(PRIMARY_SCENARIOS) + '---|')

    for sc in PRIMARY_SCENARIOS:
        res = results.get(sc, {})
        if not res.get('ok'):
            lines.append(f'> [확실하지 않은 사실] {sc}: 지표 또는 MTM 데이터 없음 — STEP A-1~A-3 먼저 실행 필요')

    lines.append(f'| G1 | value_spread 부호 일치(≥2/3 horizon) | ' +
                 ' | '.join(_signs_summary(results, sc) for sc in PRIMARY_SCENARIOS) + ' | 1차 구속 |')
    lines.append(f'| G1b | hot value_spread 중앙값 > cold | {_gate_row("g1b_pass", results)} | 1차 구속 |')
    _same_across = lambda text: ' | '.join([text] * len(PRIMARY_SCENARIOS))
    lines.append(f'| G1c | HAC t값/Spearman (참고, 미판정) | {_same_across("기록만")} | 참고 |')
    lines.append(f'| G2 | 21개 반기 앵커 부호 일치 | {_gate_row("g2_pass", results)} | 1차 구속 |')
    lines.append(f'| G2b | #22 제외해도 부호 유지 | {_gate_row("g2b_pass", results)} | 1차 구속 |')
    lines.append(f'| G3 | size_mom_6m hot/cold 구분 | {_gate_row("g3_pass", results)} | 필수 |')
    lines.append(f'| G4 | §8 민감도 강건성 | {_same_across("(STEP A-7 별도 실행 후 수기 기록)")} | 필수 |')
    lines.append('')
    lines.append('> #23(진행 중인 반기)은 위 게이트 판정 모집단에서 제외됨 (v0.3 확정, SPEC_07 §9)')
    lines.append('')

    lines.append('## G1 상세 — 리드-랙 HAC 회귀')
    lines.append('')
    for sc in PRIMARY_SCENARIOS:
        res = results.get(sc, {})
        if not res.get('ok'):
            continue
        lines.append(f'### {sc}')
        lines.append('')
        lines.append('| horizon | n | coef | HAC t(참고) | Spearman rho |')
        lines.append('|---|---|---|---|---|')
        for h in HORIZONS:
            r = res['g1_lead_lag'].get(h)
            if r is None:
                lines.append(f'| t+{h} | - | N/A | N/A | N/A |')
            else:
                lines.append(f"| t+{h} | {r['n']} | {_fmt(r['coef'])} | {_fmt(r['hac_tstat'])} | {_fmt(r['spearman_rho'])} |")
        lines.append('')

    lines.append('## 저평가 family 상관행렬 (value_spread vs size_val_gap, illiq_discount)')
    lines.append('')
    lines.append(_corr_matrix_markdown(analysis['family_corr']))
    lines.append('')
    lines.append('## ARCHIVE 시나리오 (참고, 판정 미포함)')
    lines.append('')
    lines.append(', '.join(ARCHIVE_SCENARIOS))
    lines.append('')
    return '\n'.join(lines)


def _corr_matrix_markdown(corr) -> str:
    if corr.empty:
        return 'N/A'
    cols = list(corr.columns)
    lines = ['| | ' + ' | '.join(cols) + ' |', '|---|' + '---|' * len(cols)]
    for row in corr.index:
        lines.append(f'| {row} | ' + ' | '.join(f'{corr.loc[row, c]:.3f}' for c in cols) + ' |')
    return '\n'.join(lines)


def _signs_summary(results: dict, scenario: str) -> str:
    res = results.get(scenario, {})
    if not res.get('ok'):
        return 'N/A'
    coefs = [res['g1_lead_lag'][h]['coef'] for h in HORIZONS if res['g1_lead_lag'].get(h)]
    n_pos = sum(1 for c in coefs if c > 0)
    verdict = 'PASS' if n_pos >= 2 else 'FAIL'
    return f'{verdict} ({n_pos}/{len(coefs)} 양의 부호)'


# ── 메인 ────────────────────────────────────────────────────────────────────

def run(indicators_run_id: str, mtm_run_id: str = 'mtm_v1') -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    analysis = run_analysis(indicators_run_id, mtm_run_id)

    conn = get_connection()
    try:
        indicator_df = load_indicator_df(conn, indicators_run_id)
        primary_scenario = PRIMARY_SCENARIOS[0]
        monthly_df = load_monthly_returns(conn, mtm_run_id, primary_scenario)
        for indicator in INDICATORS:
            out_path = OUT_DIR / f'{date.today().isoformat()}_{indicator}.png'
            plot_indicator(indicator_df, monthly_df, indicator, primary_scenario, out_path)
    finally:
        conn.close()

    report = build_report(analysis, indicators_run_id, mtm_run_id)
    report_path = OUT_DIR / f'{date.today().strftime("%Y.%m.%d")}._REGIME_PHASE_A.md'
    report_path.write_text(report, encoding='utf-8')
    log.info('리포트 저장: %s', report_path)
    return report_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--indicators-run-id', required=True)
    parser.add_argument('--mtm-run-id', default='mtm_v1')
    args = parser.parse_args()
    run(args.indicators_run_id, args.mtm_run_id)
