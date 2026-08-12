"""
docs/CANONICAL.md 생성 — "지금 채택된 설정이 무엇이고, 성적이 얼마고, 그 값을 언제 뽑았나".

## 왜 있는가

수치가 산문에 박혀 있으면 반드시 낡는다. 2026-08-12 세션에서 두 번 데였다 —
① SPEC_14 의 순위를 인용해 논리를 세웠는데 이미 바뀐 값이었다(산출 일자가 없어 알 방법이
없었다) ② 지표와 tape 이 5시간 동안 서로 다른 종목 집합을 담고 둘 다 최신처럼 보였다.

그래서 이 파일은 **손으로 쓰지 않는다.** 산출물에서 생성한다.

## 무엇을 하지 않는가

- SPEC 의 거울이 아니다. 판정 **논리·근거**는 SPEC 이 SSOT 이고 여기 담지 않는다.
- **생성 커밋 SHA 를 찍지 않는다.** 자기참조라서다 — 파일이 HEAD 를 적는데 그 파일을
  커밋하면 HEAD 가 바뀐다. 커밋할 때마다 멱등성 검사가 영구히 빨간불이 된다
  (2026-08-12 실제로 발생, 서버 교차 검사에서 발견). 이 문서의 출처는 아래 소스 지문이
  전부 규정하며, HEAD 는 재료의 속성이 아니다.
- **생성 시각과 mtime 을 찍지 않는다.** 재생성 시 바이트가 달라져 멱등성 검사가 매번
  오작동하고, 그러면 진짜 변경을 못 잡는다. 신선도는 재료가 가진 `run_at`/`generated_at`
  이 이미 말해준다 — "요약본을 몇 시에 인쇄했나"는 정보가 아니다. mtime 은 git 이 보존
  하지 않아 클론만 해도 달라진다.

## 렌더러가 아니라 정합성 검사기다

2026-08-12 에 드러난 결함들은 전부 "재료끼리 안 맞는데 아무도 안 알려줘서" 생겼다.
예쁘게 찍기만 하면 또 난다. 검사에 걸리면 경고를 문서에 박고 **종료 코드 1**로 끝낸다.

실행:  venv/bin/python -m scripts.make_canonical
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

import yaml

from backtest.ablation import ABLATION_CONFIGS
from backtest.configs import constants as C

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

ROOT     = Path(__file__).resolve().parent.parent
ABL_DIR  = ROOT / 'experiments/ablation'
NAV_DIR  = ROOT / 'experiments/daily_nav'
ROB_DIR  = ROOT / 'experiments/robustness'
LIVE_DIR = ROOT / 'experiments/live'
DOCS     = ROOT / 'docs'
OUT_PATH = DOCS / 'CANONICAL.md'
FREEZE   = ROOT / 'scripts/live/freeze_rebalance.py'
ISSUES   = DOCS / 'open_issues.yaml'

G5_LIMIT = -0.45   # SPEC_10 §5 사전등록. gate_analysis.G5_MDD_LIMIT 과 같은 값 —
                   # 게이트 산출물이 있으면 그쪽 값을 쓰고 이건 표시용 폴백이다.


# ── 입력 읽기 ────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _freeze_constants() -> tuple[str, int]:
    """freeze_rebalance.py 에서 DEFAULT_TAG·N_STOCKS 를 **ast 로** 읽는다.

    import 하지 않는 이유: 그 모듈은 backtest.ablation·ingest.connection 까지 끌고
    온다. 정규식을 안 쓰는 이유: `N_STOCKS    = 13` 처럼 정렬 공백이 들어가 있다.
    """
    tree = ast.parse(FREEZE.read_text(encoding='utf-8'))
    found: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id in ('DEFAULT_TAG', 'N_STOCKS'):
                found[t.id] = ast.literal_eval(node.value)
    missing = {'DEFAULT_TAG', 'N_STOCKS'} - found.keys()
    if missing:
        raise SystemExit(f'{FREEZE} 에서 {sorted(missing)} 를 찾지 못했다.')
    return str(found['DEFAULT_TAG']), int(found['N_STOCKS'])


def _load_manifest() -> dict | None:
    path = LIVE_DIR / 'dryrun/manifest.yaml'
    if not path.exists():
        return None
    m = yaml.safe_load(path.read_text(encoding='utf-8'))
    # 티커가 파일 안에서 `'000210'`(문자열)과 `001940`(→ 정수 1940)로 섞여 있다.
    # 0 을 잃은 채로 비교하면 조용히 안 맞는다.
    m['selected_tickers'] = [str(t).zfill(6) for t in m.get('selected_tickers') or []]
    return m


def _tape_cap(tag: str) -> int | None:
    """holdings tape 의 종목 수 상한. 없으면 None."""
    path = ABL_DIR / f'{tag}_holdings.json'
    if not path.exists():
        return None
    tape = json.loads(path.read_text(encoding='utf-8'))
    return max((p['n_portfolio'] for p in tape), default=0) or None


# ── 수집 ────────────────────────────────────────────────────────────────────

def collect() -> dict:
    tag, n_stocks = _freeze_constants()
    # 종목 수는 산출물에 기록되지 않고 태그 이름 문자열에만 있었다 (2026-08-12 발견).
    # 그래서 조회 키를 **조립**해야 한다. 조립한 키가 없으면 조용히 기본 태그로
    # 폴백하지 않는다 — 그게 n=20 성적(14.52%)을 운영(18.55%) 수치로 발행하던 함정이다.
    key = f'{tag}_n{n_stocks}'

    abl_summary = _read_json(ABL_DIR / 'summary.json') or {}
    nav_summary = _read_json(NAV_DIR / 'summary.json') or {}
    abl_tag     = _read_json(ABL_DIR / f'{key}.json')
    gates       = _read_json(ROB_DIR / f'gate_results_{key}.json')
    manifest    = _load_manifest()
    issues      = yaml.safe_load(ISSUES.read_text(encoding='utf-8')) if ISSUES.exists() else {}

    nav_tag = (nav_summary.get('tags') or {}).get(key)
    cfg     = ABLATION_CONFIGS.get(tag, {})

    sources = {
        'experiments/ablation/summary.json':        ABL_DIR / 'summary.json',
        f'experiments/ablation/{key}.json':         ABL_DIR / f'{key}.json',
        'experiments/daily_nav/summary.json':       NAV_DIR / 'summary.json',
        f'experiments/robustness/gate_results_{key}.json': ROB_DIR / f'gate_results_{key}.json',
        'experiments/live/dryrun/manifest.yaml':    LIVE_DIR / 'dryrun/manifest.yaml',
        'docs/open_issues.yaml':                    ISSUES,
    }

    return {
        'tag': tag, 'n_stocks': n_stocks, 'key': key,
        'config': cfg,
        'abl_tag': abl_tag, 'nav_tag': nav_tag,
        'abl_summary_has_key': key in (abl_summary.get('scenarios') or {}),
        'gates': gates, 'manifest': manifest,
        'tape_cap': _tape_cap(key),
        'issues': list(issues.get('issues') or []),
        'sources': {name: _sha256(p) for name, p in sources.items()},
        'constants': {'RF': C.RF, 'RK': C.RK, 'OMEGA': C.OMEGA, 'VB_CAP': C.VB_CAP},
    }


# ── 정합성 검사 ──────────────────────────────────────────────────────────────

def check(d: dict) -> list[str]:
    """어긋난 것들을 사람이 읽을 문장으로 돌려준다. 비어 있으면 정상."""
    p: list[str] = []
    key, n = d['key'], d['n_stocks']

    if d['abl_tag'] is None:
        p.append(f'`{key}.json` 이 없다 — 운영 설정의 구간 지표를 인용할 수 없다.')
    if d['nav_tag'] is None:
        p.append(f'`daily_nav/summary.json` 에 `{key}` 키가 없다 — 일별 지표를 인용할 수 없다.')
    if not d['abl_summary_has_key']:
        p.append(f'`ablation/summary.json` 에 `{key}` 가 병합돼 있지 않다 — summary 만 읽는 '
                 f'소비자는 운영 설정을 볼 수 없다.')

    if d['gates'] is None:
        p.append(f'`gate_results_{key}.json` 이 없다 — SPEC_10 게이트가 **현행 채택안으로 '
                 f'산출된 적이 없다.** 다른 태그의 성적표를 대신 쓰지 않는다.')
    else:
        g_tag = d['gates'].get('tag')
        if g_tag != key:
            p.append(f'게이트 산출물의 대상이 `{g_tag}` 인데 운영은 `{key}` 다 — 오귀속.')
        if d['gates'].get('draws_n_stocks') not in (None, n):
            p.append(f'G1 귀무분포가 {d["gates"]["draws_n_stocks"]}종목 추첨인데 운영은 '
                     f'{n}종목이다 — 합격선 자체가 달라 판정이 성립하지 않는다.')

    if d['tape_cap'] is None:
        p.append(f'`{key}_holdings.json` (tape) 이 없다 — 종목 단위 분석·진단이 불가하다.')
    elif d['tape_cap'] != n:
        p.append(f'tape 의 종목 수 상한이 {d["tape_cap"]} 인데 운영은 {n} 이다. '
                 f'`config_hash` 는 n 을 해시에 넣지만 **산출물 경로는 태그 이름만** 쓰므로 '
                 f'이 어긋남을 잡지 못한다.')

    for label, obj in (('구간 지표', d['abl_tag']), ('일별 지표', d['nav_tag'])):
        if obj is not None and not (obj.get('run_at') or obj.get('generated_at')):
            p.append(f'{label}(`{key}`)에 산출 일자가 없다 — 신선도를 판정할 수 없다.')

    # 근거 문서 경로가 살아 있는가. 죽은 링크는 "근거가 있다"는 인상만 주고 확인은
    # 막는다 — 2026-08-12 에 실제로 2건을 죽은 채로 커밋했고 사용자가 발견했다.
    for i in d['issues']:
        r = i.get('ref')
        if r and not (ROOT / r).exists():
            p.append(f'미해결 과제 `{i.get("id")}` 의 근거 경로가 없다: `{r}`')
        # 해소된 항목은 지우는 것이 규약이다 — 파일 이름이 곧 계약(open_issues)이다.
        if i.get('status') not in ('open', 'blocked'):
            p.append(f'미해결 과제 `{i.get("id")}` 의 status 가 `{i.get("status")}` 다. '
                     f'open|blocked 만 허용 — 해소됐으면 항목을 지워라.')

    m = d['manifest']
    if m is not None:
        if len(m['selected_tickers']) != n:
            p.append(f'라이브 manifest 의 편입 종목이 {len(m["selected_tickers"])}개인데 '
                     f'운영은 {n}개다.')
        if isinstance(m.get('strategy_version'), str) and 'PASS' in m['strategy_version'] \
                and d['gates'] is None:
            p.append('라이브 manifest 의 `strategy_version` 이 게이트 산출물 없이 PASS 를 '
                     '주장한다 — 문자열에 박힌 옛 성적이다.')

    return p


# ── 렌더링 ──────────────────────────────────────────────────────────────────

def _pct(v, digits=2):
    return '—' if v is None else f'{v * 100:.{digits}f}%'


def _stamp(obj: dict | None) -> str:
    if not obj:
        return '—'
    return (obj.get('run_at') or obj.get('generated_at') or '—')[:19]


def _momentum_desc(cfg: dict) -> str:
    mc = cfg.get('momentum_criterion')
    if mc:
        params = ', '.join(f'{k}={v}' for k, v in sorted(mc.items())
                           if k not in ('type', 'tag'))
        return f'`{mc["type"]}`' + (f' ({params})' if params else '')
    return '레거시 `MomentumFilter` (MA 20/60)' if cfg.get('use_momentum') else '없음'


def render(d: dict, problems: list[str]) -> str:
    key, n = d['key'], d['n_stocks']
    abl, nav, cfg = d['abl_tag'], d['nav_tag'], d['config']
    L: list[str] = []

    L += [f'# CANONICAL — 현행 채택 설정과 성적', '',
          '> ⚠️ **이 파일은 `scripts/make_canonical.py` 가 산출물에서 생성한다. 손으로 고치지 마라.**',
          '> 고쳐야 할 값이 있으면 그 값을 만든 산출물이나 `docs/open_issues.yaml` 을 고쳐라.',
          '> 판정 **논리·근거**는 여기 없다 — SPEC 이 SSOT 다.', '']

    if problems:
        L += ['## ⚠️ 정합성 경고', '',
              '아래가 해소되기 전에는 이 문서의 값을 인용할 때 반드시 함께 인용하라.', '']
        L += [f'{i}. {t}' for i, t in enumerate(problems, 1)]
        L += ['']

    L += ['## 채택 설정', '',
          '| 항목 | 값 |', '|---|---|',
          f'| 태그 | `{d["tag"]}` |',
          f'| 종목 수 | **{n}** |',
          f'| 산출물 키 | `{key}` |',
          f'| 랭킹 | `{cfg.get("rank_mode", "—")}` |',
          f'| 안정성 규칙 | {", ".join(sorted(cfg.get("stability_rules") or [])) or "—"} |',
          f'| 모멘텀 기준 | {_momentum_desc(cfg)} |',
          f'| HardFilter | {"사용" if cfg.get("use_hard") else "미사용"} |',
          f'| 팩터 스크리너 | {"사용" if cfg.get("use_screener") else "미사용"} |',
          f'| RIM 밸류에이션 컷 | {"사용" if cfg.get("use_rim_filter") else "미사용"} |', '',
          '필터 스택·모멘텀 기준은 `backtest/ablation.py` 의 `ABLATION_CONFIGS` 에서 읽는다 '
          '(산문이 아니라 파생물).', '']

    L += ['## 성적', '',
          '| 지표 | 값 | 출처 | 산출 일자 |', '|---|---|---|---|']
    if abl:
        L += [f'| 구간 CAGR (gross) | {_pct(abl.get("cagr"), 4)} | `ablation/{key}.json` | {_stamp(abl)} |',
              f'| 구간 CAGR (net) | {_pct(abl.get("net_cagr"), 4)} | `ablation/{key}.json` | {_stamp(abl)} |',
              f'| 완결 구간 수 | {abl.get("n_periods", "—")} | `ablation/{key}.json` | {_stamp(abl)} |']
    if nav:
        net = nav.get('net') or {}
        L += [f'| **일별 net CAGR** | **{_pct(nav.get("net_cagr"), 4)}** | `daily_nav/summary.json` | {_stamp(nav)} |',
              f'| 일별 net MDD | {_pct(net.get("daily_mdd"))} | `daily_nav/summary.json` | {_stamp(nav)} |',
              f'| 일별 net Sharpe | {net.get("daily_sharpe", 0):.3f} | `daily_nav/summary.json` | {_stamp(nav)} |']
    L += ['', 'Sharpe·MDD 의 SSOT 는 일별 NAV 다 (SPEC_13 §9-1). 구간 지표는 엔진 산술값이다.', '']

    L += ['## SPEC_10 하드 게이트', '']
    g = d['gates']
    if g is None:
        L += [f'**미산출** — `gate_results_{key}.json` 이 없다. 다른 태그의 게이트 결과를 '
              f'여기 옮겨 적지 않는다. 그게 2026-08-12 에 실제로 일어난 오귀속이다.', '']
    else:
        hg = g.get('hard_gates', {})
        L += [f'대상 `{g.get("tag")}` · 귀무분포 `{g.get("draws_tag")}` '
              f'({g.get("draws_n_stocks")}종목) · 산출 {str(g.get("generated_at"))[:19]}', '',
              '| 게이트 | 판정 | 근거 |', '|---|---|---|']
        for name, fmt in (
            ('G1', lambda v: f'CAGR {_pct(v.get("f_cagr"))} vs 귀무 p95 {_pct(v.get("random_p95"))}'),
            ('G2', lambda v: f'net {_pct(v.get("f_net_cagr"))} vs U {_pct(v.get("u_ew_net_cagr"))}'),
            ('G5', lambda v: f'일별 net MDD {_pct(v.get("f_daily_mdd_net"))} vs 한계 '
                             f'{_pct(v.get("limit", G5_LIMIT))}'),
        ):
            v = hg.get(name) or {}
            verdict = ('미산출' if v.get('pass') is None
                       else ('PASS' if v['pass'] else '**FAIL**'))
            note = v.get('not_computed_reason') or fmt(v)
            L += [f'| {name} | {verdict} | {note} |']
        L += ['']

    L += ['## 라이브 신호 (dry-run)', '']
    m = d['manifest']
    if m is None:
        L += ['없음.', '']
    else:
        L += ['| 항목 | 값 |', '|---|---|',
              f'| 신호일 | {m.get("signal_date")} |',
              f'| config_hash | `{m.get("config_hash")}` |',
              f'| git_commit_sha | `{str(m.get("git_commit_sha"))[:12]}` |',
              f'| 편입 종목 수 | {len(m["selected_tickers"])} |',
              f'| 예상 회전율 | {_pct(m.get("expected_turnover"))} |', '']

    L += ['## 상수', '', '| 이름 | 값 |', '|---|---|']
    L += [f'| `{k}` | {v} |' for k, v in d['constants'].items()]
    L += ['', '`backtest/configs/constants.py` 에서 import — 재선언 금지.', '']

    L += ['## 미해결 과제', '']
    if not d['issues']:
        L += ['없음.', '']
    else:
        L += ['| id | 심각도 | 내용 | 근거 |', '|---|---|---|---|']
        for i in d['issues']:
            summary = ' '.join(str(i.get('summary', '')).split())
            ref = f'`{i["ref"]}`' if i.get('ref') else '—'
            L += [f'| `{i.get("id")}` | {i.get("severity", "—")} | {summary} | {ref} |']
        L += ['', '이 표의 원본은 `docs/open_issues.yaml` 이다. 거기를 고쳐라.', '']

    L += ['## 소스 지문', '',
          '재생성 시 이 해시가 그대로면 내용도 그대로다. 생성 시각·mtime 은 일부러 찍지 '
          '않는다 — 매번 달라져 멱등성 검사를 무력화한다.', '',
          '| 파일 | sha256(앞 16) |', '|---|---|']
    L += [f'| `{name}` | {sha or "**없음**"} |' for name, sha in d['sources'].items()]
    L += ['']
    return '\n'.join(L)


# ── 진입점 ──────────────────────────────────────────────────────────────────

def build() -> tuple[str, list[str]]:
    d = collect()
    problems = check(d)
    return render(d, problems), problems


def main() -> None:
    ap = argparse.ArgumentParser(description='docs/CANONICAL.md 생성')
    ap.add_argument('--check', action='store_true',
                    help='파일을 쓰지 않고 현재 내용과 다른지만 확인 (CI·테스트용)')
    args = ap.parse_args()

    text, problems = build()

    if args.check:
        current = OUT_PATH.read_text(encoding='utf-8') if OUT_PATH.exists() else None
        if current != text:
            log.error('%s 가 산출물과 어긋난다 — `python -m scripts.make_canonical` 실행 필요',
                      OUT_PATH)
            sys.exit(2)
        log.info('%s 최신 상태', OUT_PATH)
    else:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(text, encoding='utf-8')
        log.info('생성: %s', OUT_PATH)

    for t in problems:
        log.warning('정합성: %s', t)
    if problems:
        log.error('정합성 경고 %d건 — 해소 전까지 이 문서의 값을 단독 인용하지 마라.',
                  len(problems))
        sys.exit(1)


if __name__ == '__main__':
    main()
