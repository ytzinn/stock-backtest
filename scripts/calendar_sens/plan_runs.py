"""
SPEC_14 실행 계획 — 어떤 산출물이 없는지 점검하고 **정확한 실행 명령**을 찍는다.

왜 필요한가: SPEC_14 §6 은 B단계 비용을 "결정론 8~9개 태그"로 적었지만, 판정은
**공통 기간 일별 net NAV** 로 하므로 반기 쪽에도 daily NAV 가 필요하다. 그런데
`experiments/ablation/` 에 `_holdings.json` 이 있는 태그는 8개뿐이고 `experiments/
daily_nav/` 도 마찬가지라, 판정 태그 10개 × 2캘린더 중 상당수가 미생성 상태다.
빠진 것을 먼저 세지 않으면 중간에 FileNotFoundError 로 멈춘다.

파이프라인 (태그·캘린더 하나당):
  1. run_ablation      → {tag}{suffix}.json + {tag}{suffix}_periods.csv
  2. export_portfolios → {tag}{suffix}_holdings.json
  3. run_daily_nav     → {tag}{suffix}_daily_nav.csv    (--tags 는 **접미사 포함** 이름)

**반드시 서버에서 실행할 것.** `_holdings.json`·`_daily_nav.csv`·`*_periods.csv` 는
git 미추적이라(experiments/README.md) 개발 PC 에서 돌리면 실제로는 있는 산출물까지
"없음"으로 세어 명령이 과다하게 나온다.

실행:
    venv/bin/python -m scripts.calendar_sens.plan_runs
    venv/bin/python -m scripts.calendar_sens.plan_runs --valuation-date 2026-07-28
"""
from __future__ import annotations

import argparse
import logging

from backtest.configs.schedule import tag_suffix
from scripts.calendar_sens.calsens_lib import (
    ABL_DIR,
    CALENDARS,
    NAV_DIR,
    PLUMBING_TAG,
    REQUIRED_TAGS,
)

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)


def missing(valuation_date: str) -> dict:
    need: dict[str, dict[str, list[str]]] = {}
    for calendar in CALENDARS:
        sfx = tag_suffix(calendar)
        stage = {'ablation': [], 'holdings': [], 'daily_nav': []}
        # 배관 양성 대조군(G-CAL-2)은 반기 ablation 만 있으면 된다
        tags = list(REQUIRED_TAGS) + ([PLUMBING_TAG] if calendar == 'SEMIANNUAL' else [])
        for tag in tags:
            out = f'{tag}{sfx}'
            if not (ABL_DIR / f'{out}.json').exists() or \
               not (ABL_DIR / f'{out}_periods.csv').exists():
                stage['ablation'].append(tag)
            if tag == PLUMBING_TAG:
                continue                      # 대조군은 CAGR 비교만 — tape/NAV 불필요
            if not (ABL_DIR / f'{out}_holdings.json').exists():
                stage['holdings'].append(tag)
            if not (NAV_DIR / f'{out}_daily_nav.csv').exists():
                stage['daily_nav'].append(out)
        need[calendar] = stage
    return need


def main() -> None:
    ap = argparse.ArgumentParser(description='SPEC_14 실행 계획 점검')
    ap.add_argument('--valuation-date', default='2026-07-28',
                    help='열린 구간 평가 기준일 — 동결 스냅샷 실행과 동일하게 고정할 것')
    args = ap.parse_args()

    need = missing(args.valuation_date)
    total = sum(len(v) for s in need.values() for v in s.values())

    log.info('=== SPEC_14 미생성 산출물 %d건 ===\n', total)
    for calendar, stage in need.items():
        sfx = tag_suffix(calendar) or '(무접미사)'
        log.info('── 캘린더 %s %s ──', calendar, sfx)
        for k, v in stage.items():
            log.info('  %-10s %d개  %s', k, len(v), ' '.join(v) if v else '(없음)')
        log.info('')

    if total == 0:
        log.info('전부 존재 — integrity_gates → stage_a → stage_b 순으로 진행 가능.')
        return

    log.info('=== 실행 명령 (동결 스냅샷 워크트리, 크론 시간대 밖) ===\n')
    for calendar, stage in need.items():
        cal_opt = f'--calendar {calendar}'
        if stage['ablation']:
            log.info('venv/bin/python -m scripts.run_ablation %s --det-only \\\n'
                     '    --valuation-date %s --tags %s\n',
                     cal_opt, args.valuation_date, ' '.join(stage['ablation']))
        if stage['holdings']:
            log.info('venv/bin/python -m scripts.export_portfolios %s --tags %s\n',
                     cal_opt, ' '.join(stage['holdings']))
        if stage['daily_nav']:
            log.info('venv/bin/python -m scripts.run_daily_nav %s --tags %s\n',
                     cal_opt, ' '.join(stage['daily_nav']))
    log.info('venv/bin/python -m scripts.calendar_sens.integrity_gates')
    log.info('venv/bin/python -m scripts.calendar_sens.stage_a')
    log.info('venv/bin/python -m scripts.calendar_sens.stage_b')


if __name__ == '__main__':
    main()
