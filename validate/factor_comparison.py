"""
팩터 스크리닝 단독 비교.
HardFilter / StabilityFilter 없이 4개 팩터(GP/A, 매출YoY, 영업이익YoY, 1/PBR)만으로
상위 20종목을 추출해 competitor_screening.csv와 비교한다.
"""
import csv
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, '/opt/stock-backtest')

from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.data_access import get_market_cap, load_gate_passed_tickers, load_pit_series
from ingest.connection import get_connection

WEIGHTS = {'rev_yoy': 1/6, 'op_yoy': 1/6, 'gpa': 1/3, 'inv_pbr': 1/3}

PERIOD_MAP = {
    date(2015, 4, 3):  '2015-03', date(2015, 8, 19): '2015-08',
    date(2016, 4, 5):  '2016-03', date(2016, 8, 18): '2016-08',
    date(2017, 4, 5):  '2017-03', date(2017, 8, 18): '2017-08',
    date(2018, 4, 4):  '2018-03', date(2018, 8, 20): '2018-08',
    date(2019, 4, 3):  '2019-03', date(2019, 8, 20): '2019-08',
    date(2020, 4, 3):  '2020-03', date(2020, 8, 20): '2020-08',
    date(2021, 4, 5):  '2021-03', date(2021, 8, 19): '2021-08',
    date(2022, 4, 5):  '2022-03', date(2022, 8, 18): '2022-08',
    date(2023, 4, 5):  '2023-03', date(2023, 8, 18): '2023-08',
    date(2024, 4, 3):  '2024-03', date(2024, 8, 20): '2024-08',
    date(2025, 4, 3):  '2025-03', date(2025, 8, 20): '2025-08',
    date(2026, 4, 3):  '2026-03',
}


def _compute_factors(ticker, rebal_date, series, conn):
    pit      = series[0] if series else {}
    pit_prev = series[1] if len(series) > 1 else {}

    cur_rev  = pit.get('매출액')
    cur_op   = pit.get('영업이익')
    prev_rev = pit_prev.get('매출액')
    prev_op  = pit_prev.get('영업이익')
    assets   = pit.get('자산총계')
    gross    = pit.get('매출총이익')
    if gross is None and cur_rev is not None:
        cogs  = pit.get('매출원가')
        gross = (cur_rev - cogs) if cogs is not None else None

    mktcap = get_market_cap(conn, ticker, rebal_date)
    equity = pit.get('자본총계')
    pbr    = (mktcap / equity) if (mktcap and equity and equity > 0) else None

    return {
        'rev_yoy': (cur_rev / prev_rev - 1) if (prev_rev and cur_rev is not None and prev_rev > 0) else None,
        'op_yoy':  (cur_op  / prev_op  - 1) if (prev_op  and cur_op  is not None and prev_op  > 0) else None,
        'gpa':     (gross / assets)          if (assets and gross is not None and assets > 0)       else None,
        'inv_pbr': (1.0 / pbr)               if (pbr and pbr > 0)                                   else None,
    }


def _percentile_rank(vals: dict) -> dict:
    valid = [(t, v) for t, v in vals.items() if v is not None]
    valid.sort(key=lambda x: x[1])
    n = len(valid)
    if n == 0:
        return {t: 0.0 for t in vals}
    ranked = {t: 0.0 for t in vals}
    i = 0
    while i < n:
        j = i
        while j < n - 1 and valid[j][1] == valid[j + 1][1]:
            j += 1
        avg_rank = (i + j) / 2
        for k in range(i, j + 1):
            ranked[valid[k][0]] = avg_rank / (n - 1) if n > 1 else 1.0
        i = j + 1
    return ranked


def factor_top20(tickers, rebal_date, pit_series, conn):
    raw = {t: _compute_factors(t, rebal_date, pit_series.get(t, []), conn) for t in tickers}
    scores = {t: 0.0 for t in tickers}
    for factor in ('rev_yoy', 'op_yoy', 'gpa', 'inv_pbr'):
        ranked = _percentile_rank({t: raw[t].get(factor) for t in tickers})
        for t in tickers:
            scores[t] += WEIGHTS[factor] * ranked.get(t, 0.0)
    return sorted(scores, key=scores.__getitem__, reverse=True)[:20], scores


def load_stock_names(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, corp_name FROM stocks")
        return {r[0]: r[1] for r in cur.fetchall()}


def load_competitor(path):
    comp = defaultdict(list)
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            comp[r['period']].append(r['code'])
    return comp


def main():
    base = '/opt/stock-backtest'
    comp = load_competitor(f'{base}/validate/competitor_screening.csv')

    conn = get_connection()
    names = load_stock_names(conn)

    our_rows = []   # period, code, name, rank, score
    cmp_rows = []   # period, overlap, overlap_pct

    try:
        for rebal_date in REBALANCE_DATES:
            period = PERIOD_MAP.get(rebal_date)
            if not period:
                continue
            print(f'[{period}] {rebal_date} ...', flush=True)

            gate = load_gate_passed_tickers(conn, rebal_date)
            pit  = load_pit_series(conn, rebal_date, n_years=3)
            univ = [t for t in gate if t in pit and pit[t]]

            top20, scores = factor_top20(univ, rebal_date, pit, conn)
            comp_codes    = comp.get(period, [])
            overlap       = len(set(top20) & set(comp_codes))

            print(f'  universe={len(univ)}, overlap={overlap}/20', flush=True)

            for rank, code in enumerate(top20, 1):
                our_rows.append({
                    'period': period,
                    'code':   code,
                    'name':   names.get(code, ''),
                    'rank':   rank,
                    'score':  round(scores[code], 4),
                })

            cmp_rows.append({
                'period':      period,
                'rebal_date':  str(rebal_date),
                'our_top20':   ','.join(top20),
                'comp_top20':  ','.join(comp_codes),
                'overlap':     overlap,
                'overlap_pct': round(overlap / 20 * 100, 1),
            })

    finally:
        conn.close()

    # ── CSV 저장 ──────────────────────────────────────────────────────────────
    our_path = f'{base}/validate/our_factor_top20.csv'
    with open(our_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['period', 'code', 'name', 'rank', 'score'])
        w.writeheader(); w.writerows(our_rows)
    print(f'\nSaved: {our_path}')

    cmp_path = f'{base}/validate/comparison_summary.csv'
    with open(cmp_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['period','rebal_date','overlap','overlap_pct','our_top20','comp_top20'])
        w.writeheader(); w.writerows(cmp_rows)
    print(f'Saved: {cmp_path}')

    # ── 콘솔 요약 ─────────────────────────────────────────────────────────────
    print('\n' + '='*70)
    print(f'{"기간":<10} {"겹침":>7}  {"우리 TOP5":<45} {"경쟁사 TOP5"}')
    print('-'*70)
    for r in cmp_rows:
        our5  = r['our_top20'].split(',')[:5]
        comp5 = r['comp_top20'].split(',')[:5]
        our5n  = [names.get(c, c) for c in our5]
        comp5n = [names.get(c, c) for c in comp5]
        print(f'{r["period"]:<10} {r["overlap"]:>4}/20   '
              f'{",".join(our5n):<45} {",".join(comp5n)}')

    avg = sum(r['overlap'] for r in cmp_rows) / len(cmp_rows)
    print(f'\n평균 겹침: {avg:.1f}/20  ({avg/20*100:.1f}%)')


if __name__ == '__main__':
    main()
