"""CANONICAL **성적 표** 입력을 등재 산출물에서 조립한다 — 재계산하지 않는다.

`compose_gate_results.py` 의 형제다. 저쪽이 게이트 표를 조립하고 이쪽이 성적 표를
조립한다. 구조·계약·금기가 같다 — **새 패턴을 만들지 않는다.**

## 왜 있는가

`[검증된 사실]` CANONICAL 성적 표는 `run_ablation` 요약 JSON 에서 나온다. 그 파일들은
2026-08-15 산출분(계정 표준화 결함 **수정 전**)이고 `_poisoned_summaries` 에 등재돼 있다.
재생성하려면 `run_ablation` 을 돌려야 하는데, 그 스크립트는 등재 기준선 입력인
`{tag}_periods.csv` 를 항상 함께 덮어쓴다 — 등록부가 막으려고 만들어진 바로 그 사고다.

그래서 **값을 만드는 경로와 값을 옮기는 경로를 분리한다.** 판정과 성적은 이미 있다:
구간 지표는 `e2c2b8d`(절단 재슬라이스), 일별 지표는 `af529ff`(G5·G2 재산출)에.
이 스크립트는 그것을 읽어 소비처가 기대하는 모양으로 옮겨 담고, 옮기는 과정이 조용히
틀리지 않도록 fail-closed 로 검사한다.

## 무엇을 하지 않는가

- **CAGR·MDD·Sharpe 를 계산하지 않는다.** 계산하면 이 스크립트가 조용히 판정 세션이
  된다. 등재값과 다른 수가 나오면 그것은 새 판정이 아니라 **발견**이다 — 중단하고 보고.
- `run_ablation` 을 부르지 않는다. 이 세션의 목적이 그것을 입력에서 빼는 것이다.
- 이름·접미사로 종류를 판정하지 않는다. 종류는 **등재부 소속과 내용**으로 본다
  (`endswith('n13.json')` 이 추첨 풀 스냅샷을 요약 JSON 으로 오인한 적이 있다).

## 구간 집합

성적과 게이트가 **같은 `period_set`** 이어야 한다. 다르면 한 문서에 두 세대가 섞이고,
표만 인용될 때 그 사실이 사라진다. fail-closed 5 가 그것을 막는다.

`[검증된 사실]` T18 구간의 **일별 net CAGR 은 등재 산출물 어디에도 없다.** `af529ff`
의 G5 판정 블록은 일별 MDD·Sharpe 만 담는다 (2026-08-29 전수 확인). 없는 값을 n=20
진단값으로 채우면 그것이 세대 혼입이므로, `null` 로 두고 사유를 적는다.

이 스크립트는 **DB 에 접속하지 않는다.**

실행:
    python -m scripts.robustness.compose_canonical --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from scripts.analysis.baseline_registry import _load as _load_registry
from scripts.analysis.baseline_registry import (
    file_digest,
    poisoned_digests,
    poisoned_summaries,
)
from scripts.analysis.period_set_lib import identify_period_set, period_set_sha
from scripts.robustness import compose_gate_results as cg
from scripts.xsec._guard import assert_no_db_imported

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

#: 성적 표의 구간 지표 출처. **등재분**(shared_inputs).
TRUNC_SRC = Path('experiments/analysis/2026.08.24._period_truncation/'
                 'truncation_results_shadow_20260819.json')
#: 성적 표의 일별 지표 출처. 게이트 표와 **같은 산출물**을 쓴다 — 갈라지지 않게.
G52_SRC = cg.G52_SRC

#: 일별 tape 전 구간(n=20) 산출물. **섀도우 세대 등재분** — 저장소 루트의 동명 파일과
#: 다르다 (루트 쪽은 여전히 2026-08-15 세대다). 경로를 혼동하지 마라.
NAV_SRC_REL = 'experiments/daily_nav/summary.json'
NAV_SRC = Path('experiments/_baselines/shadow_20260819') / NAV_SRC_REL
BASELINE_BRANCH = 'shadow/fs-div-fallback'

ABL_OUT = Path('experiments/ablation/F_pbr_ma200_n13.json')
NAV_OUT = Path('experiments/daily_nav/summary.json')

TAG = cg.TAG
N_STOCKS = cg.N_STOCKS

_METHOD = ('등재 산출물에서 읽어 옮겼다. 이 스크립트는 지표를 계산하지 않는다 — '
           'run_ablation 을 돌리면 등재 기준선 입력인 {tag}_periods.csv 가 함께 '
           '덮어써진다.')


class ComposeRefused(SystemExit):
    """조립 거부. 조용히 통과시키지 않는다."""


# ── fail-closed ─────────────────────────────────────────────────────────────

def _registered_sha(rel: str, registry: dict) -> str | None:
    """등재부가 이 상대경로에 대해 아는 sha256. 공용 입력과 기준선 입력 **둘 다** 본다."""
    spec = (registry.get('shared_inputs') or {}).get(rel)
    if spec is not None:
        return spec['sha256']
    for bl in (registry.get('baselines') or {}).values():
        if bl.get('branch') != BASELINE_BRANCH:
            continue
        sha = (bl.get('inputs') or {}).get(rel)
        if sha is not None:
            return sha
    return None


def _check_registered(path: Path, registry: dict, rel: str | None = None) -> str:
    """fail-closed 1 — 입력이 **등재부에 있고 sha256 이 맞는가.**

    종류를 파일 이름으로 판정하지 않는다. 등재부 소속으로 판정한다.

    `rel` — 등재부에서 찾을 상대경로. 기준선 입력은 `local_root` 아래에 놓이므로
    디스크 경로와 등재 키가 다르다. 그때만 따로 넘긴다.
    """
    key = rel if rel is not None else path.as_posix()
    sha = _registered_sha(key, registry)
    if sha is None:
        raise ComposeRefused(
            f'{key}: 등재부에 없다 — 등재되지 않은 파일에서 공식 수치를 끌어오지 '
            f'않는다. BASELINES.json 에 등재하고 다시 실행하라.')
    if not path.exists():
        raise ComposeRefused(f'{key}: 파일 없음 — {path}')
    actual = file_digest(path)
    if actual != sha:
        raise ComposeRefused(
            f'{key}: sha256 불일치 — 등재 {sha[:16]}… 실제 {actual[:16]}…. '
            f'등재 이후 파일이 바뀌었다. 조립하지 않는다.')
    return sha


def _check_not_poisoned(inputs) -> None:
    """fail-closed 1-b — 출처가 **폐기 세대**를 입력으로 물고 있지 않은가.

    `[검증된 사실]` 종전에는 선언된 **경로**를 폐기 목록과 교집합했다. 경로는 정체성이
    아니므로(같은 경로에 다른 세대가 앉는다) 두 축으로 본다:

      ① 기록된 폐기 sha256 과 일치하는 입력이 있는가 — **양성 식별**
      ② 경로가 폐기 목록에 있는가 — 종전 검사. 참고로 남긴다

    ②만으로는 재오염을 못 잡고, ①만으로는 아직 sha 가 기록되지 않은 폐기본을 놓친다.
    **둘 다 본다** — 단일 판별자에 기대지 않는다.
    """
    dig = poisoned_digests()
    by_sha = sorted(
        f'{rel} (sha {(meta or {}).get("sha256", "?")[:16]}… = '
        f'{dig[(meta or {}).get("sha256")]["generation"]} 세대)'
        for rel, meta in (inputs or {}).items()
        if isinstance(meta, dict) and meta.get('sha256') in dig)
    by_path = sorted(set(inputs) & poisoned_summaries())
    if by_sha or by_path:
        raise ComposeRefused(
            f'출처가 폐기 세대를 입력으로 물고 있다 — sha 일치 {by_sha or "없음"}, '
            f'경로 일치 {by_path or "없음"}. 폐기 세대가 성적 표로 새는 경로다.')


def _check_period_set(block: dict | None, where: str) -> dict:
    """fail-closed 2 — 구간 집합을 **sha256 으로 역참조**한다. 이름을 믿지 않는다."""
    if not block:
        raise ComposeRefused(f'{where}: period_set 블록이 없다 — 어느 구간 집합인지 '
                             f'말할 수 없는 값은 성적 표에 싣지 않는다.')
    if period_set_sha(block['dates']) != block['sha256']:
        raise ComposeRefused(f'{where}: period_set 의 sha256 이 자기 dates 와 불일치 — 손상')
    back = identify_period_set(block['dates'])
    if back is None:
        raise ComposeRefused(f'{where}: 이 구간 집합은 A-0 등재부에 없다 — 이름을 '
                             f'지어내지 않는다. sha {block["sha256"][:16]}…')
    if block.get('id') not in (None, back['id']):
        raise ComposeRefused(
            f'{where}: period_set 이름이 내용과 어긋난다 — 산출물은 {block["id"]!r} 이라 '
            f'적었으나 sha 역참조는 {back["id"]!r} 다.')
    return back


def _check_span(metrics: dict, ps: dict, where: str) -> None:
    """fail-closed 3 — 구간 수·경계가 집합과 맞는가."""
    if metrics['n_periods'] != ps['n']:
        raise ComposeRefused(f'{where}: 구간 수 불일치 — 지표 {metrics["n_periods"]} vs '
                             f'집합 {ps["n"]}')
    lo, hi = min(ps['dates']), max(ps['dates'])
    if metrics['start'] != lo:
        raise ComposeRefused(f'{where}: 시작 경계 불일치 — 지표 {metrics["start"]} vs '
                             f'집합 {lo}')
    # 종료는 마지막 앵커가 아니라 그 다음 리밸런싱일이다. 앵커보다 뒤여야 한다.
    if not metrics['end'] > hi:
        raise ComposeRefused(f'{where}: 종료 경계가 마지막 앵커({hi}) 보다 뒤가 아니다 — '
                             f'{metrics["end"]}')


def _check_cross_value(a: float, b: float) -> None:
    """fail-closed 4 — 두 등재 산출물이 **같은 수**를 말하는가.

    서로 다른 세션이 독립 경로로 계산한 같은 양이다. 불일치는 둘 중 하나가 다른
    세대에서 왔다는 뜻이다.
    """
    if abs(a - b) > 1e-12:
        raise ComposeRefused(
            f'두 등재 산출물의 F net CAGR 불일치 — 성적측 {a!r} vs 게이트측 {b!r}. '
            f'세대가 갈렸을 수 있다. 조립하지 않는다.')


def _check_same_period_set(perf_sha: str, gate_sha: str) -> None:
    """fail-closed 5 (신설) — **성적과 게이트가 같은 구간 집합인가.**

    이 전환의 목적이다. 다르면 한 문서 안에 두 세대가 섞이고, 표만 인용될 때 그
    사실이 사라진다 (2026-08-12 오귀속과 같은 구조).
    """
    if perf_sha != gate_sha:
        raise ComposeRefused(
            f'성적·게이트의 구간 집합이 다르다 — 성적 {perf_sha[:16]}… vs '
            f'게이트 {gate_sha[:16]}…. 한 문서에 두 세대를 싣지 않는다.')


# ── 조립 ────────────────────────────────────────────────────────────────────

def compose(trunc_src: Path = TRUNC_SRC, g52_src: Path = G52_SRC,
            nav_src: Path = NAV_SRC, registry: dict | None = None,
            gate: dict | None = None) -> dict:
    reg = registry if registry is not None else _load_registry()
    _check_registered(trunc_src, reg)                                    # 1
    t = json.loads(trunc_src.read_bytes().decode('utf-8'))
    _check_not_poisoned(t['provenance']['inputs'])                       # 1-b
    if not g52_src.exists():
        raise ComposeRefused(f'등재 산출물이 없다: {g52_src}')
    g52 = json.loads(g52_src.read_bytes().decode('utf-8'))
    _check_not_poisoned(g52['provenance']['inputs'])                     # 1-b

    cut = t['cut18']
    ps = _check_period_set(cut.get('period_set'), '성적(truncation cut18)')    # 2
    _check_span(cut['metrics'], ps, '성적(truncation cut18)')                  # 3

    gate = gate if gate is not None else cg.compose()
    _check_cross_value(cut['metrics']['net_cagr'],
                       gate['hard_gates']['G2']['f_net_cagr'])           # 4
    _check_same_period_set(cut['period_set']['sha256'],
                           gate['period_set']['sha256'])                 # 5

    m = cut['metrics']
    ps_out = {'id': ps['id'], 'n': ps['n'], 'sha256': ps['sha256']}

    abl = {
        'tag': TAG,
        'generated_at': t['provenance']['verified_at'][:19],
        'composed_at': '2026-08-29',
        'composed_from': f'{trunc_src.as_posix()} (e2c2b8d)',
        'method': _METHOD,
        'period_set': ps_out,
        'n_stocks': N_STOCKS,
        # 아래는 전부 **읽은 값**이다. 하나도 계산하지 않는다.
        'n_periods': m['n_periods'], 'start': m['start'], 'end': m['end'],
        'years': m['years'],
        'cagr': m['cagr'], 'net_cagr': m['net_cagr'],
        'sharpe': m['sharpe'], 'net_sharpe': m['net_sharpe'],
        'mdd': m['mdd'], 'robustness': m['robustness'],
        'benchmark_cagr': m['benchmark_cagr'], 'kosdaq_cagr': m['kosdaq_cagr'],
        'alpha': m['alpha'], 'alpha_kosdaq': m['alpha_kosdaq'],
        'avg_turnover': m['avg_turnover'],
        'diagnostic_full20': {
            '_doc': ('전 구간(n=20) 진단값. **판정도 성적도 아니다** — 표에 싣지 마라. '
                     '절단 전후를 대조할 때만 쓴다.'),
            **{k: t['full20']['metrics'][k]
               for k in ('n_periods', 'start', 'end', 'cagr', 'net_cagr', 'mdd')},
        },
    }

    nav = _compose_nav(g52, ps, ps_out, nav_src, reg, t)
    return {'ablation_tag': abl, 'nav_tag': nav, 'period_set': ps_out, 'gate': gate}


def _compose_nav(g52: dict, ps: dict, ps_out: dict, nav_src: Path,
                 reg: dict, t: dict) -> dict:
    """일별 지표 블록.

    **두 층이 있고, 층을 합치지 않는다.**

    - 전 구간(n=20) 일별 tape 산출: gross/net MDD, 변동성, CVaR, 최악월 … 대시보드와
      용어사전이 읽는 것이 이쪽이다. 등재 섀도우 산출물에서 **그대로** 옮긴다.
    - 판정 구간(T18) 값: `af529ff` 의 G5 판정 블록. MDD·Sharpe **둘뿐이다.**

    T18 라벨을 붙인 채 전 구간 통계를 함께 실으면 그게 세대 혼입이다. 그래서
    `judgment_t18` 을 따로 둔다 — CANONICAL 성적 표는 이쪽만 읽는다.
    """
    _check_registered(nav_src, reg, rel=NAV_SRC_REL)                      # 1
    full = json.loads(nav_src.read_bytes().decode('utf-8'))
    entry = (full.get('tags') or {}).get(TAG)
    if entry is None:
        raise ComposeRefused(f'{NAV_SRC_REL}: 태그 {TAG} 가 없다')

    # 교차 확인 — 이 전 구간 블록이 게이트·성적과 **같은 세대**인가.
    diag = g52['G5']['diagnostic_full']
    for what, a, b in (
        ('일별 net MDD(n=20)', entry['net']['daily_mdd'], diag['daily_mdd_net']),
        ('일별 net Sharpe(n=20)', entry['net']['daily_sharpe'], diag['net_sharpe']),
        ('구간 gross CAGR(n=20)', entry['gross_cagr'], t['full20']['metrics']['cagr']),
    ):
        if abs(a - b) > 1e-9:
            raise ComposeRefused(
                f'{what} 가 등재 산출물끼리 다르다 — 일별측 {a!r} vs 판정측 {b!r}. '
                f'세대가 갈렸을 수 있다. 조립하지 않는다.')

    j = g52['G5']['judgment']
    return {
        **entry,
        '_layer': ('이 블록의 값은 **전 구간(n=20) 일별 tape** 산출이다. 판정 구간의 '
                   '값은 judgment_t18 에 있고, 두 층을 섞지 마라.'),
        'full_tape_n_periods': entry.get('n_closed_periods'),
        'composed_at': '2026-08-29',
        'composed_from': f'{nav_src.as_posix()} (등재 섀도우 세대, 그대로 옮김)',
        'method': _METHOD,
        'judgment_t18': {
            '_doc': ('판정 구간 값. **MDD·Sharpe 둘뿐이다** — 나머지 일별 통계는 이 '
                     '구간으로 산출된 적이 없다.'),
            'composed_from': f'{G52_SRC.as_posix()} (af529ff, G5 판정 블록)',
            'generated_at': g52['provenance']['verified_at'][:19],
            'period_set': ps_out,
            'n_closed_periods': ps['n'],
            # T18 일별 net CAGR 은 등재 산출물에 **없다.** n=20 값으로 채우면 세대
            # 혼입이므로 null 로 두고 사유를 남긴다. 계산해서 메우지 않는다.
            'net_cagr': None,
            # 표 칸에 그대로 실린다. 한 문장으로 끝낸다 — 긴 설명은 아래 `_why` 에.
            'net_cagr_unavailable': (
                f'n={ps["n"]} 구간의 일별 net CAGR 은 등재 산출물에 없다 (전 구간 값으로 '
                f'메우지 않았다).'),
            'net_cagr_unavailable_why': (
                f'af529ff 의 G5 판정 블록은 {ps["id"]} 구간에서 일별 MDD·Sharpe 만 담는다. '
                f'일별 NAV 에서 다시 계산하면 조립기가 판정기가 되고, 전 구간 값으로 '
                f'채우면 성적 표에 두 층이 섞인다. 그래서 비운다.'),
            'net': {
                'daily_mdd': j['daily_mdd_net'],
                'mdd_peak_date': j['mdd_peak'],
                'mdd_trough_date': j['mdd_trough'],
                'daily_sharpe': j['net_sharpe'],
                'n_days': j['n_days'],
            },
            'first_day': j['first_day'], 'last_day': j['last_day'],
        },
    }


def _write_nav(nav_tag: dict, out: Path = NAV_OUT) -> None:
    """`daily_nav/summary.json` 의 **해당 태그만** 갈아 끼운다.

    다른 태그는 2026-08-15 세대다. 전부 갈려면 `run_daily_nav` 를 전 태그로 재실행해야
    하고 그것은 이 세션의 범위가 아니다. **혼입을 숨기지 않고 라벨을 붙인다** —
    `_composed` 블록이 어느 키가 조립분인지 명시한다.
    """
    doc = json.loads(out.read_bytes().decode('utf-8')) if out.exists() else {'tags': {}}
    doc['tags'][TAG] = nav_tag
    doc['_composed'] = {
        '_doc': ['이 파일은 **두 세대가 섞여 있다.** 아래 키만 등재 산출물에서 조립된',
                 '현행 세대이고, 나머지 태그는 2026-08-15 산출분(계정 표준화 결함',
                 '수정 전)이다. 인용 전에 여기를 보라.'],
        'composed_at': '2026-08-29',
        'composed_keys': [TAG],
        'others_are': '2026-08-15 세대 (SUMMARY-JSON-STALE 잔여 범위)',
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> None:
    assert_no_db_imported('scripts.robustness.compose_canonical')
    ap = argparse.ArgumentParser(description='CANONICAL 성적 표 입력 조립 (재계산 아님)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    r = compose()
    abl, nav, ps = r['ablation_tag'], r['nav_tag'], r['period_set']
    log.info('== 성적 표 입력 조립 (등재 산출물에서 읽음, 재계산 아님) ==')
    log.info('  period_set %s  n=%d  sha256=%s', ps['id'], ps['n'], ps['sha256'][:16])
    log.info('  구간 gross %.4f%%  net %.4f%%  n=%d  (%s ~ %s)',
             abl['cagr'] * 100, abl['net_cagr'] * 100, abl['n_periods'],
             abl['start'], abl['end'])
    jt = nav['judgment_t18']
    log.info('  일별 판정(n=%d)  net MDD %.4f%%  Sharpe %.4f  n_days=%d',
             jt['n_closed_periods'], jt['net']['daily_mdd'] * 100,
             jt['net']['daily_sharpe'], jt['net']['n_days'])
    log.info('  일별 전구간(n=%s) net MDD %.4f%%  gross MDD %.4f%%  net CAGR %.4f%%',
             nav.get('full_tape_n_periods'), nav['net']['daily_mdd'] * 100,
             nav['daily_mdd_gross'] * 100, nav['net_cagr'] * 100)
    log.info('  일별 net CAGR(판정 구간): 없음 — %s', jt['net_cagr_unavailable'])
    log.info('  게이트와 동일 구간 집합: %s', r['gate']['period_set']['sha256'][:16])
    if args.dry_run:
        log.info('(dry-run — 쓰지 않음)')
        return
    ABL_OUT.parent.mkdir(parents=True, exist_ok=True)
    ABL_OUT.write_text(json.dumps(abl, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('→ %s', ABL_OUT)
    _write_nav(nav)
    log.info('→ %s (태그 %s 만 교체)', NAV_OUT, TAG)


if __name__ == '__main__':
    main()
