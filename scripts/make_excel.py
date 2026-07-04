"""
experiments/ablation/{tag}_holdings.json → 구간별 편입 종목 Excel 변환.

export_portfolios.py가 생성한 holdings JSON을 시나리오별 시트로 묶어
진입가·청산가·구간수익률·상장폐지 여부를 표로 정리한다.

실행:
  venv/bin/python -m scripts.make_excel
  venv/bin/python -m scripts.make_excel --out experiments/portfolio_holdings.xlsx
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from backtest.ablation import ABLATION_CONFIGS, RANDOM_TAGS

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

ABLATION_DIR = Path('experiments/ablation')
COLUMNS = ['구간', '시작일자', '종료일자', 'No.', '티커', '종목명', '진입가', '청산가', '구간수익률', '상장폐지']


def _rows_for_tag(tag: str) -> list[list]:
    path = ABLATION_DIR / f'{tag}_holdings.json'
    if not path.exists():
        log.warning(f'{path} 없음 — 스킵')
        return []

    periods = json.loads(path.read_text(encoding='utf-8'))
    rows = []
    for i, period in enumerate(periods, 1):
        for no, h in enumerate(period['holdings'], 1):
            ret_str = f"{h['ret'] * 100:.2f}%" if h.get('ret') is not None else ''
            rows.append([
                i,
                period['rebalance_date'],
                period['next_date'],
                no,
                h['ticker'],
                h['name'],
                h['entry'],
                h['exit'],
                ret_str,
                '상폐' if h.get('delisted') else '',
            ])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='experiments/portfolio_holdings.xlsx')
    args = parser.parse_args()

    det_tags = [t for t in ABLATION_CONFIGS if t not in RANDOM_TAGS]

    with pd.ExcelWriter(args.out, engine='openpyxl') as writer:
        for tag in det_tags:
            rows = _rows_for_tag(tag)
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=COLUMNS)
            df.to_excel(writer, sheet_name=tag[:31], index=False)
            log.info(f'[{tag}] {len(df)}행 → 시트 작성')

    log.info(f'완료: {args.out}')


if __name__ == '__main__':
    main()
