"""현행 채택 상태의 **단일 수집기** — "지금 뭐가 채택돼 있고, 성적이 얼마고, 어긋난 게 있나".

## 왜 모듈로 떼어냈나

이 로직은 원래 `scripts/make_canonical.py` 안에만 있었다. 그러면 소비자가 하나뿐일 때는
문제가 없지만, 대시보드 배너처럼 **같은 사실을 보여줘야 하는 두 번째 소비자**가 생기는
순간 갈라진다. 갈라지는 방식은 늘 같다 — 한쪽이 문서를 파싱하거나, 수치를 산문에 박거나,
로직을 복제한다. 셋 다 2026-08 에 실제로 겪었다:

- `docs/CANONICAL.md` 를 파싱 → 문서 서식이 바뀌면 조용히 깨진다.
- 수치를 화면 문자열에 박음 → 재산출하면 낡는데 아무도 모른다 (freeze_rebalance 의
  `strategy_version` 에 옛 태그의 'G1·G2 PASS, G5 FAIL' 이 박혀 있던 사고).
- 지표를 화면이 다시 계산 → 대시보드 CAGR 이 공식 수치와 1.86%p 어긋나고 판정 배지가
  뒤집혔다 (2026-08-14 발견).

그래서 **수집은 여기 한 곳**이고, `make_canonical` 은 이걸 문서로 찍고 대시보드는 이걸
화면에 그린다. 둘 다 `collect()` 의 소비자일 뿐 각자 읽지 않는다.

## 무엇을 하지 않는가

- 렌더링하지 않는다. 문서 서식은 `scripts/make_canonical.py`, 화면은 `dashboard/` 소관.
- 생성 시각·커밋 SHA 를 만들지 않는다. 재료가 가진 `run_at`/`generated_at` 만 전달한다
  (이유는 make_canonical 모듈 docstring).
- 없는 값을 지어내지 않는다. 산출물이 없으면 `None` 이고, 그 사실은 `check()` 가 문장으로
  돌려준다 — 조용한 기본값으로 메우지 않는다.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import yaml

from backtest.ablation import ABLATION_CONFIGS
from backtest.configs import constants as C

ROOT     = Path(__file__).resolve().parent.parent
ABL_DIR  = ROOT / 'experiments/ablation'
NAV_DIR  = ROOT / 'experiments/daily_nav'
ROB_DIR  = ROOT / 'experiments/robustness'
LIVE_DIR = ROOT / 'experiments/live'
DOCS     = ROOT / 'docs'
FREEZE   = ROOT / 'scripts/live/freeze_rebalance.py'
ISSUES   = DOCS / 'open_issues.yaml'


# ── 입력 읽기 ────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def _sha256(path: Path) -> str | None:
    """소스 파일 지문. **줄바꿈을 정규화한 뒤** 해시한다.

    원시 바이트를 그대로 해시하면 지문이 플랫폼에 종속된다 — git 이 텍스트 파일을
    개발 PC(CRLF)와 서버(LF)로 서로 다르게 체크아웃하기 때문이다. 그러면 같은 내용인데
    지문이 달라져, 한쪽에서 생성한 CANONICAL.md 를 다른 쪽에서 `--check` 하면 항상
    실패한다 (2026-08-12 서버 교차 검사에서 발견). 지문이 말해야 하는 것은 "내용이
    같은가"이지 "어느 OS 에서 체크아웃했는가"가 아니다.
    """
    if not path.exists():
        return None
    return hashlib.sha256(
        path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()[:16]


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


# ── 소비자 공용 파생 ─────────────────────────────────────────────────────────

def gate_verdicts(d: dict) -> dict[str, bool | None]:
    """게이트별 판정. 산출물이 없으면 **빈 dict** 가 아니라 값이 `None` 이다.

    "미산출"과 "FAIL"은 다른 사실이다. bool 로 뭉개면 화면에서 구분이 사라진다.
    """
    hg = (d['gates'] or {}).get('hard_gates', {})
    return {name: (hg.get(name) or {}).get('pass') for name in ('G1', 'G2', 'G5')}


def momentum_label(cfg: dict) -> str:
    """모멘텀 기준을 사람이 읽을 한 줄로. 문서·화면이 같은 문구를 써야 한다.

    `momentum_criterion` 이 없는데 `use_momentum` 이 켜져 있으면 레거시 MA 20/60 이다 —
    "모멘텀을 안 쓴다"고 읽으면 틀린다 (실제로 그렇게 잘못 보고한 적이 있다).
    """
    mc = cfg.get('momentum_criterion')
    if mc:
        params = ', '.join(f'{k}={v}' for k, v in sorted(mc.items())
                           if k not in ('type', 'tag'))
        return f'`{mc["type"]}`' + (f' ({params})' if params else '')
    return '레거시 `MomentumFilter` (MA 20/60)' if cfg.get('use_momentum') else '없음'


def material_stamps(d: dict) -> dict[str, str | None]:
    """재료별 산출 일자. 계보를 산문에 박지 않고 재료가 말하게 한다."""
    def _stamp(obj: dict | None) -> str | None:
        if not obj:
            return None
        return (obj.get('run_at') or obj.get('generated_at') or None)

    return {
        '구간 지표': _stamp(d['abl_tag']),
        '일별 지표': _stamp(d['nav_tag']),
        '게이트':   _stamp(d['gates']),
        '라이브 신호': (d['manifest'] or {}).get('signal_date'),
    }
