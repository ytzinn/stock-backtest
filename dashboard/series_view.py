"""시리즈 뷰모델 — 화면에 뿌릴 행을 만든다. **Streamlit 을 import 하지 않는다.**

페이지 스크립트 안에 있던 로직을 떼어냈다. 이유는 하나다: **화면 안에 있으면 검사할 수
없다.** 2026-08-14 에 대시보드가 CANONICAL 과 다른 CAGR 을 띄우고 판정 배지를 뒤집은
채로 오래 살아남은 것도, 그 계산이 페이지 스크립트 안에 있어 테스트가 닿지 않았기
때문이다.

여기 함수들은 순수하다 — 카탈로그와 등록 대장을 받아 dict 리스트를 돌려준다.
그래서 `tests/integrity/` 가 직접 호출해 불변식을 검사할 수 있다.
"""
from __future__ import annotations

import glob
import inspect
import json
import re
from functools import lru_cache
from pathlib import Path

from backtest.ablation import ABLATION_CONFIGS, build_ablation_pipeline
from dashboard.artifacts import ArtifactCatalog
from dashboard.series import Series, SeriesSpec

ROOT = Path(__file__).resolve().parent.parent

#: MDD·Sharpe 열 제목. **기준을 제목에 박는다** — 값만 보면 어느 정의인지 알 수 없고,
#: 구간 기준(−34%)과 일별 NAV 기준(−58%)은 같은 태그에서 24%p 차이가 난다.
MDD_COL = 'MDD (구간 기준)'
SHARPE_COL = 'Sharpe (구간 기준)'

#: 비교표가 **읽어도 되는** 지표의 출처. 일별 NAV 는 여기 없다 — 의도적이다.
METRIC_SOURCE = 'ablation_artifact'

#: 기본 종목 수. 재선언하지 않고 `build_ablation_pipeline` 시그니처에서 읽는다
#: (CLAUDE.md 상수 재선언 금지). `scripts/run_ablation.py` 도 같은 곳에서 읽으므로
#: 권위는 한 군데 — 파이프라인 시그니처 — 에만 있다.
DEFAULT_N_STOCKS: int = inspect.signature(
    build_ablation_pipeline).parameters['n_stocks'].default


def n_stocks_display(artifact, ref) -> tuple[str, bool]:
    """화면에 쓸 종목 수와 **그게 기록된 값인지 여부**.

    산출물 76개 중 `n_stocks` 를 기록한 것은 4개뿐이다. 필드가 2026-08-12 에야
    추가돼서(`run_ablation` 커밋 `0dfa0b1`), 그 전에 만든 72개는 필드가 없던 시절의
    파일이다. 값이 없는 게 아니라 **적히지 않았을 뿐**이고, 실제 n 은 이름 규약으로
    정해진다 — `_n{K}` 접미사가 있으면 K, 없으면 파이프라인 기본값이다.

    그래서 빈칸('미기록')으로 두면 "모른다"로 읽혀 과하고, 그냥 `20` 으로 적으면
    기록된 13 과 구별이 사라져 위험하다(그 구별이 사라져서 2026-08-12 사고가 났다).
    **값과 출처를 함께** 돌려준다.
    """
    if artifact.n_stocks is not None:
        return str(artifact.n_stocks), True
    inferred = ref.params.get('n_stocks', DEFAULT_N_STOCKS)
    return f'{inferred} (규약)', False


def _pct(v, digits: int = 2):
    return None if v is None else round(v * 100, digits)


def comparison_rows(series: Series, catalog: ArtifactCatalog) -> list[dict]:
    """A형 비교표 행.

    **MDD·Sharpe 는 전 행이 구간 기준이다.** 일별 NAV 를 가진 태그는 76개 중 14개뿐이라,
    있는 행만 일별 값으로 채우면 한 열에 두 정의가 섞인다. 라벨을 붙여도 사람 눈은
    숫자 크기를 먼저 보므로 정렬하는 순간 순위가 뒤집힌다. 그래서 **행이 아니라 열 단위로
    기준을 고정**한다. 일별 값은 현행 채택 배너에서만 노출한다.
    """
    baseline = series.spec.baseline
    rows = []
    for ref in series.members:
        a = catalog.require(ref.artifact_key)
        m = a.metrics
        rows.append({
            '시나리오': ref.display + (' ⟵ 기준' if ref.artifact_key == baseline else ''),
            'CAGR': _pct(m.get('cagr') if m.get('cagr') is not None else m.get('median_cagr')),
            'net CAGR': _pct(m.get('net_cagr')),
            'Alpha': _pct(m.get('alpha')),
            MDD_COL: _pct(m.get('mdd')),
            SHARPE_COL: None if m.get('sharpe') is None else round(m['sharpe'], 2),
            'Robustness': _pct(m.get('robustness'), 0),
            '회전율': _pct(m.get('avg_turnover'), 0),
            # 레거시 산출물은 n_stocks·calendar 를 기록하지 않는다. "기록된 13"과
            # "이름으로 간주한 20"을 화면에서 구별할 수 있게 표기한다. 한 열에 숫자와
            # '—' 를 섞으면 Arrow 직렬화가 터지므로 열 단위로 타입을 통일한다.
            '구간': str(a.n_periods) if a.n_periods is not None else '—',
            'n': n_stocks_display(a, ref)[0],
            '캘린더': (m.get('calendar') or {}).get('id', '미기록'),
            '산출': (a.generated_at or '')[:10],
            '출처': '분포집계' if a.source == 'summary' else '단일실행',
        })
    return rows


def compound_curve(returns) -> list[float]:
    """구간 수익률을 누적 수익률로 접는다 — Π(1+r) − 1.

    **공식 지표가 아니다. 그림용이다.** 화면이 지표를 다시 계산하지 않는다는 규칙은
    CAGR·MDD·Sharpe 같은 **판정에 쓰이는 값**에 대한 것이고, 이 함수는 이미 기록된
    구간 수익률을 눈으로 따라가게 이어 붙일 뿐이다.

    그래도 위험은 남는다: 곡선의 끝값을 연율화하면 공식 CAGR 과 어긋난다. 이 탭이
    게이트 통과 구간(21)을 쓰는데 공식 수치는 완결 구간(20)만 세기 때문이다. 그래서
    화면은 축 제목과 캡션에 **기준을 박아** 두고, 끝값을 크게 띄우지 않는다.
    """
    out, acc = [], 1.0
    for r in returns:
        acc *= 1.0 + float(r)
        out.append(acc - 1.0)
    return out


def excess_curve(strategy, benchmark) -> list[float]:
    """누적 초과 = 전략 누적 − 벤치마크 누적.

    구간별 알파를 그냥 더하지 않는다. 수익률은 곱으로 쌓이므로 합으로 재면 기간이
    길수록 어긋난다. 두 누적 곡선의 차이가 "같은 기간 동안 얼마나 앞섰나"의 정직한 답이다.
    """
    s, b = compound_curve(strategy), compound_curve(benchmark)
    return [x - y for x, y in zip(s, b)]


#: 세트 사이 빈 줄에 쓰는 문자. 눈에 안 보이지만 **길이가 달라 서로 다른 축 범주**가
#: 된다 — 같은 라벨을 두 번 쓰면 Plotly 가 한 칸으로 합쳐 버려 빈 줄이 하나만 생긴다.
_SPACER = ' '


def grouped_chart_rows(series: Series, rows: list[dict]) -> list[dict]:
    """가로 막대용 행 — **세트 사이에 빈 줄을 끼운다.**

    R6 on/off 처럼 "같은 구성에서 하나만 바꾼 쌍"이 축의 요점일 때, 열 개 막대를
    붙여 그리면 어느 둘이 짝인지 눈으로 못 찾는다. 쌍을 붙이고 세트끼리 떼어 놓으면
    비교가 그림 자체에서 읽힌다.

    세트가 정의되지 않은 축은 그대로 통과시킨다(빈 줄 없음).
    `rows` 는 `comparison_rows` 의 결과이고 `series.members` 와 **같은 순서**다.
    """
    if not series.spec.groups:
        return [{'label': r['시나리오'], 'value': r['CAGR'], 'spacer': False} for r in rows]

    by_key = {m.artifact_key: r for m, r in zip(series.members, rows)}
    out: list[dict] = []
    for i, group in enumerate(series.spec.groups):
        picked = [by_key[k] for k in group if k in by_key]
        if not picked:
            continue
        if out:
            out.append({'label': _SPACER * (i + 1), 'value': None, 'spacer': True})
        out += [{'label': r['시나리오'], 'value': r['CAGR'], 'spacer': False} for r in picked]

    # 세트에 안 적힌 멤버도 반드시 그린다. 빠뜨리면 화면에서 조용히 사라진다.
    listed = {k for g in series.spec.groups for k in g}
    rest = [r for m, r in zip(series.members, rows) if m.artifact_key not in listed]
    if rest:
        out.append({'label': _SPACER * (len(series.spec.groups) + 1),
                    'value': None, 'spacer': True})
        out += [{'label': r['시나리오'], 'value': r['CAGR'], 'spacer': False} for r in rest]
    return out


#: 분포 CSV 중앙값과 `summary.json` 중앙값이 이만큼(%p) 넘게 벌어지면 **다른 실행**으로 본다.
#: 같은 실행이면 부동소수 오차 수준이어야 한다.
DIST_VINTAGE_TOL = 0.02


def dist_vintage_gap(artifact, dist_median: float) -> float | None:
    """분포 CSV 의 중앙값과 산출물이 기록한 중앙값의 차이 (%p). 없으면 None.

    **둘은 같아야 한다.** 다르면 히스토그램과 비교표가 서로 다른 실행에서 온 것이다.
    2026-08-15 에 실제로 그랬다 — 분포 CSV 는 2026-07-18 배치이고 `summary.json` 의
    중앙값은 07-30 재발행(CORR-TTM-001 수정 후)이라 최대 0.48%p 벌어져 있었다.
    화면은 히스토그램에 p95 선을 긋고 "무작위로도 나올 성적인가"를 판정하게 하므로,
    이 어긋남을 말하지 않으면 **폐기된 실행의 합격선**으로 현행 값을 재게 된다.
    """
    rec = artifact.metrics.get('median_cagr')
    if rec is None:
        return None
    return (dist_median - rec) * 100


#: 조립된 파이프라인 클래스 → 사람이 읽는 랭킹 신호 이름.
_RANK_NAMES = {
    'BacktestPipeline':            'RIM 상승여력',
    '_PBRRankPipeline':            '1/PBR',
    '_FactorCompositeRankPipeline': '팩터 복합',
    '_RandomSelectPipeline':       '무작위 추첨',
    '_AllEqualWeightPipeline':     '동일가중 전체',
}

#: 이 경로들은 `score_and_rank` 를 오버라이드해 `passes_rim_cut` 을 호출하지 않는다.
#: `rim_threshold` 가 설정돼 있어도 컷이 걸리지 않으므로 `n/a` 로 적는다.
_CUT_INERT_SIGNALS = ('무작위 추첨', '동일가중 전체', '팩터 복합')

#: 종목 수 탐침. **기본값(20)과 겹치지 않는 아무 수**면 된다.
_N_PROBE = 7

#: 이동평균 계열의 기본 파라미터. 기본값이면 라벨에서 생략해 눈이 미끄러지지 않게 한다.
_MA_DEFAULTS = {'confirm_days': 5, 'slope_lookback': 20}


def _ma_label(a: dict) -> str:
    """이중 이동평균 라벨. **파라미터가 같으면 같은 이름이 나와야 한다.**

    레거시 `MomentumFilter` 와 `MADoubleAdapterCriterion` 은 클래스가 다르지만 같은
    `_momentum_filter()` 를 부른다 (`momentum_criteria.py` 모듈 docstring — "산식
    재작성 금지"). 실제로 `F_pbr_no_r3r4` 와 `F_pbr_ma_double_adapter` 는 gross·net 이
    소수점 6자리까지 같다. 클래스 이름으로 가르면 같은 필터를 둘로 세게 된다.
    """
    out = f"MA {a['ma_short']}/{a['ma_long']}"
    for k, short in (('confirm_days', 'cd'), ('slope_lookback', 'sl')):
        if a.get(k) != _MA_DEFAULTS[k]:
            out += f' {short}{a[k]}'
    return out


def _screener_label(pipeline, names: list[str]) -> str:
    """스크리너를 **무슨 가중으로** 돌리나. `✓` 하나로 뭉개면 안 된다.

    `screener_weights` 는 설정 키인데 어떤 열도 반응하지 않아, 단일 팩터 변형 넷
    (`E_gpa_only`·`E_op_only`·`E_pbr_only`·`E_rev_only`)이 매트릭스에서 **11개 열이
    전부 같게** 보였다. 서로 다른 네 실험이 표에서 구별되지 않으면 짝 대조군도 넷이
    같은 답을 받는다. 돌연변이 탐침(`dashboard/claims.blind_spots`)이 찾아낸 사각지대다.
    """
    scr = next((f for f in pipeline.filters if type(f).__name__ == 'FactorScreener'), None)
    if scr is None:
        return '—'
    w = getattr(scr, 'weights', None) or {}
    hot = sorted(k for k, v in w.items() if v)
    # 단일 팩터면 그 이름을, 복합이면 `✓` (기본 복합 가중은 축 설명이 답한다).
    return f'✓ {hot[0]}' if len(hot) == 1 else '✓'


def momentum_rule(pipeline) -> str:
    """**무엇으로 추세를 판정하나.** `✓` 하나로 뭉개면 안 된다.

    뭉갰을 때 두 가지가 조용히 깨진다. ① 짝 대조군 판정이 `C_pbr_path_random`
    (레거시 MA 20/60)을 MA200·52주·절대수익 태그의 짝이라고 부른다 — **유니버스가
    다른데** 관문을 물어도 된다고 말하는 셈이다. ② 모멘텀 그리드 축이 화면에서
    "달라지는 조건 0개" 라고 말한다. 그 축의 유일한 변수가 모멘텀인데도.

    모르는 기준이 새로 생기면 `name` 과 파라미터를 그대로 붙여 **절대 다른 것과 같아
    보이지 않게** 한다. 조용히 뭉쳐지는 쪽이 훨씬 위험하다.
    """
    for f in pipeline.filters:
        n = type(f).__name__
        if n == 'MomentumFilter':
            return _ma_label({k: getattr(f, k) for k in
                              ('ma_short', 'ma_long', 'confirm_days', 'slope_lookback')})
        if n != 'MomentumCriterionFilter':
            continue
        c = f.criterion
        a = {k: v for k, v in vars(c).items() if not k.startswith('_')}
        name = getattr(c, 'name', type(c).__name__)
        if name == 'ma_double_adapter':
            return _ma_label(a)
        if name == 'ma200':
            return f"MA {a['ma_window']}"          # 단일 이동평균 (창 하나)
        if name == '52w_high':
            return f"52주 고가 {a['threshold']:.0%}"
        if name == 'abs_return':
            return f"절대수익 {a['formation_days']}d"
        if name == 'market_residual_blitz_subset':
            return f"시장초과 {a['formation_days']}d"
        if name == 'sign_count':
            return f"부호수 {a['formation_days']}d"
        return f'{name} {sorted(a.items())}'       # 미등록 기준 — 뭉개지 않는다
    return '—'


def n_stocks_rule(base_tag: str, cfg: dict) -> str:
    """**태그가 종목 수를 박아 두는가**, 아니면 실행 때 정해지는가.

    숫자 하나로 적으면 안 된다. `n_stocks` 는 `build_ablation_pipeline` 의 인자이고
    `run_ablation --n-stocks` 로 정해지는 **실행 파라미터**라, 태그 대부분에 대해
    "20" 이라고 쓰면 거짓이 된다 — 현행 채택안 `F_pbr_ma200` 의 운영 설정은 **13** 이고
    산출물 키도 `F_pbr_ma200_n13` 이다. 태그와 산출물 키를 뭉갠 것이 2026-08-12 에
    회전율을 92.31% 대신 95.00% 로 적게 만든 바로 그 사고다.

    그래서 **조립기에 물어본다.** 기본으로 한 번, 기본값과 겹치지 않는 값으로 한 번
    조립해서 결과가 따라 움직이면 실행 인자이고, 안 움직이면 태그가 박아 둔 것이다.
    플래그(`random_n`)를 다시 해석하는 대신 함수의 행동을 재는 것이라, "해석 규칙의
    단일 정의는 `build_ablation_pipeline`" 이라는 이 파일의 규칙과 같은 방식이다.
    """
    default = build_ablation_pipeline(base_tag, cfg).n_stocks
    probed = build_ablation_pipeline(base_tag, cfg, n_stocks=_N_PROBE).n_stocks
    if default is None:
        return '상한 없음 (전 종목)'
    if probed == default:
        return f'{default} 고정 (추첨)'
    # "실행 인자" 라고만 적으면 **"n 을 안 건드렸다"** 로 읽힌다. 실제로는 그 반대다 —
    # n 은 독립변수로 쓸어 본 축이고(`F_pbr_ma200` → `_n10`·`_n12`·`_n13`·`_n20`),
    # 그 값이 태그가 아니라 **산출물 키**에 붙어 있을 뿐이다. 어디를 봐야 하는지를 적는다.
    return f'산출물 키 참조 (기본 {default})'


@lru_cache(maxsize=None)
def pipeline_facts(base_tag: str) -> dict:
    """등록된 설정의 조건표. 없는 태그면 빈 dict."""
    cfg = ABLATION_CONFIGS.get(base_tag)
    return _facts_of(base_tag, cfg) if cfg is not None else {}


def _facts_of(base_tag: str, cfg: dict) -> dict:
    """설정을 **실제로 조립해** 읽는다. 플래그를 화면이 다시 해석하지 않는다.

    `stability_r6`·`stability_rules`·`rank_mode`·`rim_cut` 의 조합 규칙은
    `build_ablation_pipeline` 이 단일 정의다. 화면이 그 분기를 베껴 쓰면 둘이 어긋나는
    날 화면만 조용히 틀린다. 그래서 같은 함수로 조립한 뒤 결과물을 들여다본다.

    **설정을 인자로 받는다** — 등록된 것뿐 아니라 임의 설정으로도 불러야 하기 때문이다.
    사각지대 탐침(`dashboard/claims.blind_spots`)이 설정 키를 하나씩 흔들어 이 표가
    반응하는지 재는데, 캐시된 태그 단위 함수로는 그걸 할 수 없다.
    """
    p = build_ablation_pipeline(base_tag, cfg)
    names = [type(f).__name__ for f in p.filters]
    stability = next((f for f in p.filters if type(f).__name__ == 'StabilityFilter'), None)
    rules = sorted(getattr(stability, 'active_rules', None) or ())

    rank = _RANK_NAMES.get(type(p).__name__, type(p).__name__)
    if type(p).__name__ == '_PBRRankPipeline' and getattr(p, 'equity_mode', '') == 'parent':
        rank = '1/PBR (지배지분)'

    # 밸류에이션 컷이 **실제로 거는가**. `rim_threshold` 값만 보면 안 된다 —
    # 무작위·동일가중·팩터 경로는 `score_and_rank` 를 통째로 오버라이드해서
    # `passes_rim_cut` 을 아예 호출하지 않는다. 값은 남아 있는데 걸리지 않으므로
    # `✓` 로 적으면 "고평가 종목을 뺐다"는 없는 사실이 생긴다.
    cut = 'n/a' if rank in _CUT_INERT_SIGNALS else (
        '✓' if getattr(p, 'rim_threshold', None) is not None else '—')

    return {
        '랭킹 신호': rank,
        'Hard 필터': '✓' if 'HardFilter' in names else '—',
        '안정성 룰': '·'.join(rules) if rules else '—',
        '스크리너': _screener_label(p, names),
        # `✓` 가 아니라 **판정 기준**이다. 짝 대조군 매칭이 이 값을 쓰므로, 뭉개면
        # 레거시 MA 20/60 풀을 MA200 전략의 귀무분포라고 부르게 된다.
        '모멘텀': momentum_rule(p),
        '밸류에이션 컷': cut,
        '종목 수': n_stocks_rule(base_tag, cfg),
    }


#: 설정 매트릭스에서 시나리오를 가리키는 열 이름.
CONFIG_KEY_COL = '시나리오'


def config_matrix(series: Series) -> list[dict]:
    """멤버별 설정 표 — **무엇이 켜지고 꺼졌는지**를 한 줄에 편다.

    태그 이름을 사람 말로 옮긴 라벨만으로는 두 행이 무엇에서 갈리는지 알 수 없다.
    실제로 이 표를 만들자마자 잡힌 것이 있다: `D_rim_only` 와 `D_pbr_only` 는 랭킹만
    다른 게 아니라 **R6 와 밸류에이션 컷까지 함께** 다르다. 이름만 보고 "랭킹 신호의
    차이"로 읽으면 3중 교란을 단일 효과로 인용하게 된다.
    """
    rows = []
    for ref in series.members:
        facts = pipeline_facts(ref.base_tag)
        if not facts:
            continue
        # **화면은 태그가 아니라 산출물을 그린다.** `pipeline_facts` 는 태그 단위라
        # 종목 수를 "실행 인자" 로밖에 못 적는데, 산출물 키에 `_n{K}` 가 있으면 그
        # 실행이 무엇을 골랐는지 이미 안다. 태그 답을 그대로 두면 종목 수 축이
        # "달라지는 조건 0개" 라고 말하고(그 축의 유일한 변수가 n 인데도),
        # 채택 산출물 옆에 `기본 20` 이 뜬다 — 태그와 산출물 키를 뭉개는 그 사고다.
        n = ref.params.get('n_stocks')
        if n is not None:
            facts = {**facts, '종목 수': f'{n} (산출물 키)'}
        rows.append({CONFIG_KEY_COL: ref.display, **facts})
    return rows


def varying_columns(rows: list[dict]) -> list[str]:
    """행마다 값이 다른 열만. 나머지는 이 축에서 **고정**된 조건이다."""
    if not rows:
        return []
    return [c for c in rows[0] if c != CONFIG_KEY_COL
            and len({r.get(c) for r in rows}) > 1]


def _cagr(artifact) -> float | None:
    """비교표와 **같은 규칙**으로 CAGR 을 고른다.

    분포 집계(`source='summary'`)는 단일 실행 값이 없어 중앙값을 쓴다. 여기서 다른
    규칙을 쓰면 표의 숫자와 증분이 안 맞아, 사람이 뺄셈을 검산하다 틀렸다고 여긴다.
    """
    m = artifact.metrics
    v = m.get('cagr')
    return m.get('median_cagr') if v is None else v


def delta_rows_for(spec: SeriesSpec, catalog: ArtifactCatalog) -> list[dict]:
    """왜-지도의 증분 표 — 등록 대장이 지정한 두 시나리오의 CAGR 차이.

    **지표를 새로 계산하지 않는다.** 산출물이 기록한 값 두 개를 뺄 뿐이고, 그 뺄셈은
    어차피 사람이 표를 보며 머릿속으로 하던 것이다. 다만 **어느 둘을 빼야 하는지**를
    등록 대장이 정해 주므로, 세트가 다른 행을 잘못 빼는 사고가 사라진다.
    """
    if spec.why is None or not spec.why.deltas:
        return []
    rows = []
    for d in spec.why.deltas:
        base, variant = catalog.get(d.base), catalog.get(d.variant)
        if base is None or variant is None:
            continue
        b, v = _cagr(base), _cagr(variant)
        rows.append({
            '무엇을 바꿨나': d.label,
            '기준': d.base,
            '바꾼 뒤': d.variant,
            'Δ CAGR (%p)': None if b is None or v is None else round((v - b) * 100, 2),
            # 세트를 넘는 비교는 화면에서 표가 나야 한다. 조건이 여럿 달라진 값이라
            # 같은 굵기로 읽으면 안 된다.
            '세트 넘음': '⚠' if d.crosses_sets else '—',
            '메모': d.note or '—',
        })
    return rows


def provenance_rows(series: Series, catalog: ArtifactCatalog) -> list[dict]:
    """산출물 계보 — "왜 이 태그는 그래프가 없나"를 화면에서 답하게 한다.

    카탈로그가 이미 들고 있던 정보인데 화면에 안 뿌리고 있었다. 없으면 사람이 서버에
    ssh 로 붙어 파일을 세야 한다 (2026-08-14 에 실제로 그랬다 — 개발 PC 에만 구간 CSV 가
    10개뿐인 걸 몰라 로컬/서버 차이를 한참 뒤졌다).
    """
    rows = []
    for ref in series.members:
        a = catalog.require(ref.artifact_key)
        rows.append({
            '산출물 키': a.key,
            '존재 방식': '파일' if a.source == 'file' else 'summary 전용',
            'git 추적': {True: '추적', False: '미추적', None: '판정 불가'}[a.git_tracked],
            '구간 CSV': '있음' if 'periods' in a.sidecars else '없음',
            'holdings': '있음' if 'holdings' in a.sidecars else '없음',
            '분포 CSV': '있음' if 'dist' in a.sidecars else '없음',
            '산출 시각': a.generated_at or '—',
        })
    return rows


def elsewhere_rows(spec: SeriesSpec) -> list[dict]:
    """이 축에 속하지만 ablation 산출물이 없는 태그 — **어디에 값이 있나.**

    화면이 이 표를 띄우지 않으면 그 태그는 존재하지 않는 것과 같다. 실제로 넷이
    그 상태였다 — `C_pbr_path_random` 은 채택안 G1 관문의 귀무분포 그 자체인데
    비교표에도 계보표에도 안 나왔다 (2026-08-15).

    **개발 PC 에 파일이 있는지도 함께 적는다.** 산출물 상당수가 git 미추적이라
    "서버엔 있는데 여기 없다"가 정상이고, 그 사실을 말하지 않으면 사람이 경로를
    의심하며 ssh 로 파일을 세러 간다 (계보표를 만든 것과 같은 이유).
    """
    rows = []
    for e in spec.elsewhere:
        here = [p for p in e.where if (ROOT / p).exists()]
        rows.append({
            '전략': e.tag,
            '값이 있는 곳': ' · '.join(f'`{p}`' for p in e.where),
            '개발 PC': f'{len(here)}/{len(e.where)}',
            '왜 비교표에 없나': e.why,
            '읽을 때': e.note or '—',
        })
    return rows


def b_type_files(spec: SeriesSpec) -> list[dict]:
    """B형 원본 파일 목록 (전용 뷰가 없을 때의 raw fallback).

    전용 뷰를 아직 안 만든 축에서도 **자료가 화면에서 사라지지 않아야** 한다.
    경로가 아무 것도 가리키지 않으면 빈 리스트를 돌려주고, 화면이 그 사실을 말한다 —
    조용히 빈 화면을 보여주면 "자료가 없다"와 "경로가 죽었다"를 구별할 수 없다.
    """
    found = []
    for pattern in spec.paths:
        for p in sorted(glob.glob(str(ROOT / pattern))):
            path = Path(p)
            if not path.is_file():
                continue
            found.append({
                '파일': str(path.relative_to(ROOT)).replace('\\', '/'),
                '크기': f'{path.stat().st_size / 1024:,.0f} KB',
                '수정': path.stat().st_mtime,
            })
    return found


def n_curve(path: Path | None = None) -> dict | None:
    """종목 수 곡선 산출물. 없으면 None (화면이 생성 방법을 안내한다)."""
    path = path or ROOT / 'experiments/analysis/n_stocks_curve.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


# ── B형: 시간분할 과적합 (SPEC_14 §14-3) ────────────────────────────────────

def time_split(path: Path | None = None) -> dict | None:
    """시간분할 검정 산출물. 없으면 None."""
    path = path or ROOT / 'experiments/calendar_sens/time_split.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def verdict_rule_rows(d: dict) -> list[dict]:
    """**사전등록 규칙을 실제 값과 나란히** 놓는다. 판정의 근거를 화면에서 재확인한다.

    이 축의 전용 뷰가 원본 파일 목록보다 나은 진짜 이유가 여기다. 판정 문자열만 띄우면
    `TIME_OVERFIT_CONFIRMED` 가 어떤 문턱을 어떻게 넘어서 나온 말인지 알 수 없고, 그러면
    사람은 옆에 있는 다른 숫자(ρ, CI)로 사후 설명을 만든다. 규칙은 **수치 산출 전에**
    커밋됐고(`pre_registered.note`), 그 사실이 판정의 힘 전부다.
    """
    pr, f = d['pre_registered'], d['focal']
    return [
        {'사전등록 조건': f'앞 절반 순위 ≤ {pr["front_top"]}위',
         '실제': f'{f["front_rank"]}위', '충족': f['front_rank'] <= pr['front_top']},
        {'사전등록 조건': f'뒤 절반 순위 > {pr["back_floor"]}위',
         '실제': f'{f["back_rank"]}위', '충족': f['back_rank'] > pr['back_floor']},
    ]


def bootstrap_excludes_zero(d: dict) -> bool:
    """bootstrap CI 가 0 을 배제하는가.

    **배제하지 못한다** (CI 상한이 +0.005). 그러니 판정은 ρ 의 유의성이 아니라 초점
    태그의 사전등록 문턱에서 나온 것이다. 화면이 ρ 와 판정을 나란히 띄우기만 하면
    "상관이 유의해서 과적합"이라는 없는 주장이 읽힌다 — 그래서 이 사실을 따로 계산해
    화면에 명시한다.
    """
    b = d['bootstrap']
    return b['ci_low'] > 0 or b['ci_high'] < 0


def stage_b(path: Path | None = None) -> dict | None:
    """캘린더 민감도 B단계(block-bootstrap) 산출물. 없으면 None."""
    path = path or ROOT / 'experiments/calendar_sens/stage_b.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def contrast_rows(d: dict) -> list[dict]:
    """룰 contrast 표. **단일축과 다축을 한 열에서 구분한다.**

    다축 contrast(`C_R3R4`·`C_STAB`)는 룰을 여러 개 동시에 건드리므로 "어느 룰의
    효과인가"를 말할 수 없다. 같은 표에 섞어 놓고 축 이름만 다르게 적으면 사람은
    전부 단일 룰 효과로 읽는다 — 산출물이 `single_axis` 를 따로 기록하는 이유다.
    """
    rows = []
    for c in d['contrasts_single_axis'] + d['contrasts_multi_axis']:
        rows.append({
            'contrast': c['contrast_id'],
            '축': c['axis'],
            '단일축': c['single_axis'],
            '축 수': c['n_axes'],
            '반기 e (net)': c['e_semiannual_net'],
            '안C e (net)': c['e_altC_net'],
            'δ (안C−반기)': c['delta_point'],
            'δ CI95 하한': c['delta_ci95'][0],
            'δ CI95 상한': c['delta_ci95'][1],
            'δ 0 배제': c['delta_ci95_excludes_zero'],
            '방향': c['direction_class'],
        })
    return rows


def direction_hold_is_undefined(d: dict) -> bool:
    """방향 일치율이 **정의되지 않는가** (분모 0).

    `J1_direction_hold_rate` 가 `null` 인데 화면이 이를 0% 로 그리면 "방향이 하나도
    유지되지 않았다"는 강한 주장이 된다. 실제로는 **잴 수 있는 contrast 가 하나도
    없었다** — 7개 전부 `neutral_or_inconclusive` 라 분모가 0이다. "0%"와 "잴 수 없음"은
    다른 사실이고, 전자는 캘린더가 룰 결론을 뒤집는다는 뜻으로 읽힌다.
    """
    m = d['summary_metrics']
    return m.get('J1_direction_hold_rate') is None or m.get('J1_denominator', 0) == 0


def block_sensitivity_rows(d: dict) -> list[dict]:
    """블록 길이별 "CI 가 0을 배제하는가"의 변화.

    사전등록 블록은 21일이다. 10일·63일에서 결과가 뒤집히는 contrast 가 있다면
    그 결론은 블록 길이 선택에 얹혀 있다는 뜻이다 — 판정에는 쓰지 않지만(§10-1)
    화면에는 병기해야 "0을 배제했다"를 단단한 사실로 읽지 않는다.
    """
    bls = d.get('block_length_sensitivity') or {}
    flips = bls.get('flips_vs_block21') or {}
    rows = []
    for block in sorted((k for k in bls if k.isdigit()), key=int):
        b = bls[block]
        excl = b.get('delta_excludes_zero') or {}
        rows.append({
            '블록 (일)': int(block),
            '사전등록': int(block) == (d['pre_registered'].get('block_days') or 0),
            'δ_ew CI95 하한': b['delta_ew_ci95'][0],
            'δ_ew CI95 상한': b['delta_ew_ci95'][1],
            '0 배제한 contrast': ', '.join(k for k, v in excl.items() if v) or '없음',
            '21일 대비 뒤집힘': ', '.join(flips.get(block, [])) or '—',
        })
    return rows


# ── B형: 레짐/타이밍 오버레이 (Phase B) ─────────────────────────────────────

RUNS_DIR = ROOT / 'experiments/runs'

#: 재실행 날짜별 산출물. 최신이 정본이고 **이전 것은 철회된 수치를 담고 있다.**
_PHASE_B_RE = re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2})_phaseB_(?P<kind>grid|layer2)$')


def phase_b_runs(runs_dir: Path | None = None) -> list[dict]:
    """phaseB 실행을 날짜별로 묶는다. **최신 하나만 정본이다.**

    이 축에는 같은 그리드가 두 날짜로 있다. 2026-07-10 최초 실행은 always-on 비교군의
    구간 불일치 버그로 `68/144 통과` 라는 결론을 냈고 **그 수치는 철회됐다**
    (`2026.07.11._REGIME_PHASE_B.md` §3). 2026-07-11 재실행이 `0/144` 다.

    glob 으로 훑어 아무거나 집으면 철회된 68 을 화면에 띄우게 된다. 그래서 날짜로
    묶고 최신을 정본으로 표시하되, **이전 것을 숨기지 않는다** — 숨기면 왜 두 벌이
    있는지 모르는 사람이 원본 목록에서 옛 파일을 열어 인용한다.
    """
    runs_dir = runs_dir or RUNS_DIR
    by_date: dict[str, dict] = {}
    for path in sorted(runs_dir.glob('*_phaseB_*.csv')):
        m = _PHASE_B_RE.match(path.stem)
        if not m:
            continue
        by_date.setdefault(m['date'], {'date': m['date']})[m['kind']] = path

    runs = sorted(by_date.values(), key=lambda r: r['date'], reverse=True)
    for i, r in enumerate(runs):
        r['canonical'] = (i == 0)
    return runs


def layer2_frame(path: Path):
    """Layer2 CSV. 지표를 계산하지 않고 기록된 열만 읽는다."""
    import pandas as pd
    return pd.read_csv(path)


def layer2_gate_rows(df) -> list[dict]:
    """C1~C4 게이트별 통과 수. **전부 통과(=후보)와 개별 통과는 다르다.**

    개별 게이트는 꽤 통과한다(C3 62/144). 그런데 넷을 동시에 넘는 조합이 0이다.
    개별 숫자만 띄우면 "절반쯤은 되는구나"로 읽히므로 결합 결과를 같은 표에 넣는다.
    """
    rows = [{'게이트': c, '통과': int(df[c].sum()), '전체': len(df)}
            for c in ('C1', 'C2', 'C3', 'C4') if c in df.columns]
    if 'is_candidate' in df.columns:
        rows.append({'게이트': '전부 통과 (후보)', '통과': int(df['is_candidate'].sum()),
                     '전체': len(df)})
    return rows


def alpha_survives_episode_22(df) -> dict:
    """에피소드 #22 를 빼도 알파가 남는가.

    `total_alpha` 만 보면 절반이 양(+)이라 그럴듯해 보인다. 그런데 #22 를 제외한
    `ex22_alpha` 가 양인 행은 극소수다 — 알파가 **한 에피소드에 몰려 있다**는 뜻이고,
    이 축의 결론(부가가치 없음)이 나온 실질적 이유다. 두 숫자를 나란히 두지 않으면
    화면은 "절반은 알파가 있다"고 말하는 셈이 된다.
    """
    return {
        'n': len(df),
        'total_positive': int((df['total_alpha'] > 0).sum()),
        'ex22_positive': int((df['ex22_alpha'] > 0).sum()),
        'share_warn': int(df['period22_share_warn'].sum())
        if 'period22_share_warn' in df.columns else 0,
    }


# ── B형: 성과 분해 / 라이브 전환 ────────────────────────────────────────────

ANALYSIS_DIR = ROOT / 'experiments/analysis'


def _analysis(name: str) -> dict | None:
    path = ANALYSIS_DIR / f'{name}.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def decomposition() -> dict | None:
    """모멘텀 성과 분해 산출물."""
    return _analysis('momentum_decomposition')


def rule_membership() -> dict | None:
    """룰 멤버십(R2 제거 가능성·R4 추가 영향) 산출물."""
    return _analysis('rule_membership')


def preferred_scan() -> dict | None:
    """우선주 혼입 스캔 산출물."""
    return _analysis('preferred_scan')


#: 이 축의 산출물이 **갖고 있지 않은** 것. 화면이 명시해야 하는 부재다.
DECOMPOSITION_MISSING_FIELDS = ('pre_registered', 'disclaimer', 'spec')


def missing_provenance(d: dict | None) -> tuple[str, ...]:
    """산출물에 없는 계보 필드. **없음을 화면이 말하게 하려고** 계산한다.

    `calendar_sens/` 산출물은 `pre_registered`·`disclaimer` 를 갖고 있어서 전용 뷰가
    결과와 사전등록 조건을 나란히 띄울 수 있었다. **분해 산출물은 셋 다 없다.**
    그 차이를 화면이 말하지 않으면, 사전등록된 검정과 탐색적 진단이 같은 무게로
    읽힌다 — 이 축의 상태가 `EXPLORING` 인 이유가 바로 그것이다.
    """
    if d is None:
        return DECOMPOSITION_MISSING_FIELDS
    return tuple(f for f in DECOMPOSITION_MISSING_FIELDS if f not in d)


def victim_rows(d: dict) -> list[dict]:
    """구간별 모멘텀 희생자. **희생자가 이긴 구간을 표시한다.**

    "모멘텀이 걸러낸 종목이 실제로 못했다"는 요약만 보면 필터가 늘 옳은 것처럼 읽힌다.
    구간 단위로 보면 희생자가 F 를 이긴 구간이 섞여 있고, 그 개수가 필터를 얼마나
    믿을지를 정한다.
    """
    return [{
        '구간 시작': r['rebalance_date'],
        '희생자 수': r['n_victims'],
        '희생자 평균 수익': r['victim_mean_ret'],
        'F 구간 수익 (gross)': r['f_period_gross'],
        '희생자가 이겼나': r['victim_mean_ret'] > r['f_period_gross'],
    } for r in d['momentum_victims']['rows']]


def paired_rows(d: dict) -> list[dict]:
    """F(모멘텀) vs D 구간별 페어 비교."""
    return [{
        '구간 시작': r['rebalance_date'],
        'F net': r['f_net'],
        'D net': r['d_net'],
        '차이 (F−D)': r['diff_net'],
        'F 회전율': r['f_turnover'],
        'D 회전율': r['d_turnover'],
        'F 종목': r['f_n'],
        'D 종목': r['d_n'],
    } for r in d['paired']['rows']]


def membership_verdict_rows(d: dict) -> list[dict]:
    """룰 멤버십 판정. **어긋난 날짜를 함께 싣는다.**

    `false`/`true` 만 보면 "R2 는 못 뺀다"가 전 구간의 성질처럼 읽힌다. 실제로는
    20구간 중 **한 날짜**에서만 갈렸다 — 그 사실이 있어야 판정의 강도를 안다.
    """
    v = d['verdict']
    return [
        {'질문': 'R2 를 결정적으로 뺄 수 있나',
         '판정': '아니오' if not v['r2_deterministically_removable'] else '예',
         '어긋난 날짜': ', '.join(v['r2_diff_dates']) or '없음',
         '해당 구간 수': len(v['r2_diff_dates'])},
        {'질문': 'R4 를 넣으면 상위 편입이 바뀌나',
         '판정': '예' if v['r4_addition_changes_top20'] else '아니오',
         '어긋난 날짜': ', '.join(v['r4_diff_dates']) or '없음',
         '해당 구간 수': len(v['r4_diff_dates'])},
    ]


def rank_shift_rows(d: dict) -> list[dict]:
    """앞→뒤 순위 이동. 초점 태그를 표시한다 (판정의 대상이 그것 하나이므로)."""
    focal = d['pre_registered']['focal_tag']
    return [{
        # 화면 용어는 **전략**이다. 코드·산출물의 `tag` 와 같은 것을 가리키지만,
        # 처음 보는 사람에게 "태그"는 분류 라벨처럼 읽힌다 (용어사전 `strategy_vs_tag`).
        '전략': r['tag'],
        '초점': r['tag'] == focal,
        'horizon': r['horizon'],
        '앞 순위': r['front_rank'],
        '뒤 순위': r['back_rank'],
        '이동': r['back_rank'] - r['front_rank'],
        '전체 순위': r['full_rank'],
        '앞 배수': round(r['front_mult'], 3),
        '뒤 배수': round(r['back_mult'], 3),
    } for r in sorted(d['rows'], key=lambda r: r['front_rank'])]
