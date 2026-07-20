"""
SPEC_11 §2-2 — 우선주 유니버스 점검 (읽기 전용).

편입 이력(전 holdings tape) + 통과 풀(robustness/pools.json)의 티커를
stocks 테이블과 대조해 우선주 의심 종목을 스캔한다.

판정 기준 (둘 다 보고, 사람 확인용):
  - 종목명 패턴: '우'/'우B'/'우C'로 끝남 (예: 삼성전자우, 미래에셋증권2우B)
    ⚠ '대우'처럼 회사명이 '우'로 끝나는 보통주 오탐 가능 → 티커 끝자리 교차 확인
  - 티커 끝자리: 보통주는 통상 '0', 우선주는 5/7/9/K/L/M — 끝자리 != '0'이면서
    이름 패턴도 맞으면 강한 의심

결과: 0건 → "DART 재무 매핑 부재로 HARD PIT 존재 조건에서 자연 탈락" 가설 확인.
      존재 → CONTRACT-UNIV-001 정책 결정 상신 (보통주 한정 규칙).

실행: venv/bin/python -m scripts.analysis.preferred_scan
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

NAME_PATTERN = re.compile(r'(우|[0-9]우B?|우B|우C)$')


def collect_tickers() -> tuple[set[str], set[str]]:
    """(편입 이력 티커, 통과 풀 티커)."""
    held: set[str] = set()
    for path in Path('experiments/ablation').glob('*_holdings.json'):
        for p in json.loads(path.read_text(encoding='utf-8')):
            held.update(h['ticker'] for h in p['holdings'])

    pool: set[str] = set()
    pools_path = Path('experiments/robustness/pools.json')
    if pools_path.exists():
        for tickers in json.loads(pools_path.read_text(encoding='utf-8')).values():
            pool.update(tickers)
    return held, pool


def scan(conn, tickers: set[str]) -> list[dict]:
    if not tickers:
        return []
    with conn.cursor() as cur:
        cur.execute('SELECT ticker, corp_name FROM stocks WHERE ticker = ANY(%s)',
                    (sorted(tickers),))
        rows = cur.fetchall()
    out = []
    for ticker, name in rows:
        name_hit   = bool(NAME_PATTERN.search(name or ''))
        suffix_hit = not ticker.endswith('0')
        if name_hit or suffix_hit:
            out.append({'ticker': ticker, 'corp_name': name,
                        'name_pattern': name_hit, 'ticker_suffix': suffix_hit,
                        'strong': name_hit and suffix_hit})
    return sorted(out, key=lambda r: (not r['strong'], r['ticker']))


def main() -> None:
    held, pool = collect_tickers()
    conn = get_connection()
    try:
        held_hits = scan(conn, held)
        pool_hits = scan(conn, pool - held)
    finally:
        conn.close()

    result = {
        'n_held_tickers': len(held), 'n_pool_tickers': len(pool),
        'held_suspects': held_hits, 'pool_only_suspects': pool_hits,
        'strong_suspects_held': [r for r in held_hits if r['strong']],
        'strong_suspects_pool': [r for r in pool_hits if r['strong']],
    }
    out_path = Path('experiments/analysis/preferred_scan.json')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

    log.info('편입 이력 %d티커 중 의심 %d건 (강한 의심 %d건)',
             len(held), len(held_hits), len(result['strong_suspects_held']))
    log.info('풀 전용 %d티커 중 의심 %d건 (강한 의심 %d건)',
             len(pool - held), len(pool_hits), len(result['strong_suspects_pool']))
    for r in result['strong_suspects_held'][:10]:
        log.info('  [편입-강한의심] %s %s', r['ticker'], r['corp_name'])
    log.info('저장: %s', out_path)


if __name__ == '__main__':
    main()
