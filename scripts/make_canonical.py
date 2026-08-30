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

## 예쁘게 찍기만 하지 않는다

2026-08-12 에 드러난 결함들은 전부 "재료끼리 안 맞는데 아무도 안 알려줘서" 생겼다.
그래서 수집과 함께 **정합성 검사**를 돌린다. 검사에 걸리면 경고를 문서에 박고
**종료 코드 1**로 끝낸다.

수집·검사 자체는 `backtest/canonical_state.py` 소유다 (`collect()` / `check()`).
이 파일은 그 결과를 마크다운으로 찍는 **렌더러**이고, 대시보드 배너는 같은 결과를
화면으로 그리는 또 다른 소비자다. 같은 사실을 두 곳에서 따로 읽으면 반드시 갈라진다.

실행:  venv/bin/python -m scripts.make_canonical
"""
from __future__ import annotations

import argparse
import logging
import re
import sys

# 수집·검사는 `backtest/canonical_state.py` 소유다. 이 파일은 **렌더러**다.
# 대시보드 배너도 같은 collect()/check() 를 소비한다 — 같은 사실을 두 곳에서 따로
# 읽으면 반드시 갈라진다 (그 모듈 docstring 에 갈라진 사례 3건).
# `_sha256`·`collect`·`check` 는 여기서도 import 가능해야 한다 (오라클 테스트 계약).
from backtest.canonical_state import (  # noqa: F401
    DOCS,
    _sha256,
    check,
    collect,
    judgment_view,
    momentum_label,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

OUT_PATH = DOCS / 'CANONICAL.md'

G5_LIMIT = -0.45   # SPEC_10 §5 사전등록. gate_analysis.G5_MDD_LIMIT 과 같은 값 —
                   # 게이트 산출물이 있으면 그쪽 값을 쓰고 이건 표시용 폴백이다.


# ── 렌더링 ──────────────────────────────────────────────────────────────────

#: 'YYYY-MM-DDTHH:MM:SS…' 모양인가. 자를 수 있는 값과 아닌 값을 가른다.
_ISO_TS = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}')


def _pct(v, digits=2):
    return '—' if v is None else f'{v * 100:.{digits}f}%'


def _stamp(obj: dict | None) -> str:
    """산출 일자. **ISO 타임스탬프일 때만** 19자로 자른다.

    무조건 자르면 조립 산출물의 사람이 읽는 문구가 문장 중간에서 끊긴다
    ("2026-08-29 (조립 — 재계"). 자를지 말지는 값의 **모양**으로 정한다.
    """
    if not obj:
        return '—'
    v = str(obj.get('run_at') or obj.get('generated_at') or '—')
    return v[:19] if _ISO_TS.match(v) else v


def _period_set_line(*objs) -> list[str]:
    """성적 표 머리의 구간 집합 줄. 여러 출처가 다른 집합을 말하면 **그대로 드러낸다.**

    조용히 하나만 고르면 두 세대가 한 표에 섞이고 표만 인용될 때 사라진다.
    (`canonical_state.check` 가 같은 사실을 종료 코드로도 잡는다.)
    """
    seen = {}
    for o in objs:
        ps = (o or {}).get('period_set')
        if ps:
            seen[ps.get('sha256')] = ps
    if not seen:
        return ['`period_set` 미기재 — 이 성적이 어느 구간 집합의 것인지 산출물이 '
                '말하지 않는다.', '']
    if len(seen) > 1:
        ids = ', '.join(f'`{p.get("id")}`(n={p.get("n")})' for p in seen.values())
        return [f'⚠ **구간 집합이 갈렸다** — {ids}. 한 표에 두 세대가 섞여 있다.', '']
    ps = next(iter(seen.values()))
    return [f'구간 `{ps.get("id")}` (n={ps.get("n")}, sha256 '
            f'`{str(ps.get("sha256"))[:16]}…`)', '']


def render(d: dict, problems: list[str]) -> str:
    key, n = d['key'], d['n_stocks']
    abl, cfg = d['abl_tag'], d['config']
    # 일별 tape 은 전 구간 산출이고 판정은 절단 구간에서 났다. 성적 표는 **판정 층**을
    # 읽는다 — 전 구간 통계를 판정 구간 라벨 아래 실으면 그게 층 혼입이다.
    nav = judgment_view(d['nav_tag'])
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
          f'| 모멘텀 기준 | {momentum_label(cfg)} |',
          f'| HardFilter | {"사용" if cfg.get("use_hard") else "미사용"} |',
          f'| 팩터 스크리너 | {"사용" if cfg.get("use_screener") else "미사용"} |',
          f'| RIM 밸류에이션 컷 | {"사용" if cfg.get("use_rim_filter") else "미사용"} |', '',
          '필터 스택·모멘텀 기준은 `backtest/ablation.py` 의 `ABLATION_CONFIGS` 에서 읽는다 '
          '(산문이 아니라 파생물).', '']

    L += ['## 성적', '']
    # 성적도 게이트와 같이 **어느 구간 집합의 성적인지**를 먼저 말한다. 없이 읽으면
    # 전 구간 성적으로 오독된다 — 2026-08 절단 이후 이 줄이 표의 일부다.
    L += _period_set_line(abl, nav)
    L += ['| 지표 | 값 | 출처 | 산출 일자 |', '|---|---|---|---|']
    if abl:
        L += [f'| 구간 CAGR (gross) | {_pct(abl.get("cagr"), 4)} | `ablation/{key}.json` | {_stamp(abl)} |',
              f'| 구간 CAGR (net) | {_pct(abl.get("net_cagr"), 4)} | `ablation/{key}.json` | {_stamp(abl)} |',
              f'| 완결 구간 수 | {abl.get("n_periods", "—")} | `ablation/{key}.json` | {_stamp(abl)} |']
    if nav:
        net = nav.get('net') or {}
        # 값이 **없는 것**과 0 인 것을 구분한다. 없는 값을 0 이나 다른 구간 집합의
        # 값으로 메우면 그것이 세대 혼입이다 — 사유를 표에 그대로 싣는다.
        why = nav.get('net_cagr_unavailable')
        cagr_cell = (f'**{_pct(nav.get("net_cagr"), 4)}**' if nav.get('net_cagr') is not None
                     else (f'— <br>⚠ {why}' if why else '—'))
        sharpe = net.get('daily_sharpe')
        L += [f'| **일별 net CAGR** | {cagr_cell} | `daily_nav/summary.json` | {_stamp(nav)} |',
              f'| 일별 net MDD | {_pct(net.get("daily_mdd"))} | `daily_nav/summary.json` | {_stamp(nav)} |',
              f'| 일별 net Sharpe | {"—" if sharpe is None else f"{sharpe:.3f}"} | `daily_nav/summary.json` | {_stamp(nav)} |']
    L += ['', 'Sharpe·MDD 의 SSOT 는 일별 NAV 다 (SPEC_13 §9-1). 구간 지표는 엔진 산술값이다.', '']

    L += ['## SPEC_10 하드 게이트', '']
    g = d['gates']
    if g is None:
        L += [f'**미산출** — `gate_results_{key}.json` 이 없다. 다른 태그의 게이트 결과를 '
              f'여기 옮겨 적지 않는다. 그게 2026-08-12 에 실제로 일어난 오귀속이다.', '']
    else:
        hg = g.get('hard_gates', {})
        # `period_set` 은 **판정이 무엇을 서술하는지**다. 없이 표를 읽으면 전 구간
        # 판정으로 오독된다 — 2026-08 절단 이후 이 필드가 표의 일부다.
        ps = g.get('period_set') or {}
        ps_line = (f' · 구간 `{ps.get("id")}` (n={ps.get("n")}, sha256 '
                   f'`{str(ps.get("sha256"))[:16]}…`)' if ps else '')
        L += [f'대상 `{g.get("tag")}` · 귀무분포 `{g.get("draws_tag")}` '
              f'({g.get("draws_n_stocks")}종목) · 산출 {_stamp(g)}'
              + ps_line, '',
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
            # 단서는 **판정 옆에 붙어야** 한다. 떨어뜨려 두면 표만 인용될 때 사라진다.
            if v.get('caveat'):
                note += f'<br>⚠ {v["caveat"]}'
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
