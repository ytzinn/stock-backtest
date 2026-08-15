"""용어사전 (sub2) — 이 저장소에서 **같은 이름이 다른 것을 가리키는 자리**의 목록.

## 왜 산문이 아니라 매니페스트인가

용어 혼동은 이 프로젝트가 실제로 손해를 본 결함 유형이다. 2026-08-12 회전율 사고
(`tag` vs `artifact_key`), 2026-08-14 CAGR 1.86%p 오염(구간 수 세 층)이 둘 다 "이름은
같은데 가리키는 게 다르다"에서 나왔다. 그런 설명을 검토 문서 산문에만 두면 ① 화면 옆에
없어서 볼 사람이 안 보고 ② 숫자가 바뀌어도 아무도 안 고친다.

그래서 `SeriesSpec.notes` 와 같은 방식을 쓴다 — **매니페스트가 내용을 소유하고, 검사가
그 내용을 실제 산출물과 대조한다** (`tests/integrity/test_glossary.py`). 여기 적힌
23·21·20 이나 −34.14%·−58.12% 는 장식이 아니라 **검사 대상**이다. 산출물이 바뀌면
검사가 깨져서 이 파일을 고치게 만든다.

## 배정 규칙

`series` 가 비어 있으면 **공통 용어**로 모든 축에 붙는다. 채워져 있으면 그 축에서만
뜬다. 축을 하나하나 열거해야만 붙는 구조로 만들면 새 축이 생겼을 때 조용히 안 붙는다 —
공통을 기본값으로 두는 쪽이 안전하다.
"""
from __future__ import annotations

from dataclasses import dataclass

from dashboard.series import STATUS_MEANING

#: 상태 코드 표는 매니페스트(`dashboard/series.py`)의 정의에서 **생성한다.**
#: 여기 손으로 다시 적으면 화면과 사전이 갈라지고, 갈라지면 한쪽만 고쳐진다.
_STATUS_TABLE = '\n'.join(f'| **`{code}`** | {meaning} |'
                          for code, meaning in STATUS_MEANING.items())


@dataclass(frozen=True)
class Term:
    """용어 하나.

    `one_line` 은 목록 표에 뜨는 한 줄, `body` 는 펼쳤을 때의 마크다운이다. 둘을 나눈
    이유는 **훑어보기와 확인하기가 다른 동작**이기 때문이다. 한 줄만 있으면 헷갈릴 때
    판별을 못 하고, 본문만 있으면 목록에서 원하는 항목을 못 찾는다.
    """

    id: str
    term: str
    one_line: str
    body: str
    sources: tuple[str, ...] = ()   # repo 상대 경로. `path:LINE` 허용
    series: tuple[str, ...] = ()    # 비어 있으면 공통 (모든 축에 표시)
    aliases: tuple[str, ...] = ()   # 검색어 (코드에 쓰이는 실제 식별자 등)
    incident: str = ''              # 이 혼동이 실제로 낸 사고

    @property
    def is_common(self) -> bool:
        return not self.series


GLOSSARY: tuple[Term, ...] = (
    Term(
        id='period_layers',
        term='구간 수 23 → 21 → 20',
        one_line='같은 태그의 구간 개수가 세 가지다. 어느 층을 세느냐로 CAGR 이 1.86%p 달라진다.',
        aliases=('n_periods', 'n_gate', '완결 구간', 'closed'),
        series=(),
        sources=('dashboard/series_view.py', 'backtest/metrics.py',
                 '설계메모_v3_ablation_대시보드_3층_재구성.md'),
        incident='2026-08-14 — 대시보드가 21구간으로 재계산해 공식 20.33% 자리에 18.48% 를 띄웠다.',
        body="""
`F_pbr_ma200_n13` 하나를 놓고 세어도 답이 셋이다.

| 층 | 개수 | 무엇인가 |
|---|---|---|
| 구간 CSV 원본 행 | **23** | `_periods.csv` 의 전체 행. 게이트 통과 종목이 0인 구간도 들어 있다 |
| `n_gate > 0` | **21** | 게이트를 통과한 종목이 하나라도 있던 구간 |
| **완결 구간** | **20** | 진입·청산이 모두 끝난 구간. **공식 성과 지표는 이것만 쓴다** |

**공식 수치는 맨 아래 층이다.** 산출물 JSON 의 `n_periods` 가 그 값이고, 화면은 그걸
읽기만 한다. 열린 구간(아직 청산 안 된 마지막 구간)은 실행일과 가격 신선도에 종속돼
매일 값이 달라지므로 참고 지표로만 쓴다 (CLAUDE.md 영구 규칙).

> ⚠️ **구간 수를 화면이 세지 마라.** 구간별 탭의 행을 세어 CAGR 을 재계산하면 반드시
> 어긋난다. 대시보드가 그렇게 해서 1.86%p 오염이 오래 살아남았다.
"""),

    Term(
        id='tag_vs_artifact_key',
        term='`tag` vs `artifact_key`',
        one_line='태그는 전략 이름, artifact_key 는 파일 조회 키. 태그 하나가 여러 종목 수의 결과를 가리킨다.',
        aliases=('base_tag', 'ScenarioRef', 'F_pbr_ma200', '_n13'),
        series=(),
        sources=('dashboard/artifacts.py:43', 'scripts/live/freeze_rebalance.py:141'),
        incident='2026-08-12 — n=13 운영이 n=20 tape 을 읽어 매니페스트 회전율이 0.9231 대신 0.9500 으로 기록됐다. 에러는 나지 않았다.',
        body="""
- **`tag`** (= `base_tag`) — 전략 설정의 이름. `ABLATION_CONFIGS` 의 키다. 예: `F_pbr_ma200`
- **`artifact_key`** — 산출물 파일의 이름. `{tag}_n{종목수}` 규약. 예: `F_pbr_ma200_n13`

**파일 조회는 무조건 `artifact_key` 로 한다.** 태그만으로는 어느 종목 수의 결과인지
알 수 없기 때문이다. 접미사가 없는 레거시 파일(`F_pbr_ma200.json`)은 **n=20 이다** —
`F_pbr_ma200_n20.json` 과 `cagr`·`mdd`·`sharpe` 가 소수점 끝까지 같다는 대조로 확인했다.

현행 채택안은 `F_pbr_ma200_n13` 이다. 접미사 없는 이름을 보고 "그게 현행이겠지"라고
집으면 다른 전략의 숫자를 읽는다.

> 종목 수가 다른 tape 을 집으면 회전율에 **종목 수 전이 비용**이 섞인다. 지금은
> `freeze_rebalance._previous_holdings` 가 tape 상한과 `N_STOCKS` 를 대조해 멈춘다.
"""),

    Term(
        id='mdd_basis',
        term='구간 기준 vs 일별 NAV 기준 (MDD·Sharpe)',
        one_line='같은 태그의 MDD 가 −34.14%(구간·gross)와 −58.12%(일별·net)로 24%p 다르다. 둘 다 맞는 값이다.',
        aliases=('MDD', 'Sharpe', 'daily_nav', 'drawdown', 'endpoint_mdd_gross'),
        series=(),
        sources=('docs/CANONICAL.md', 'dashboard/series_view.py:24',
                 'docs/설계/SPEC_13_rebalance_calendar_v0.7.md'),
        incident='',
        body="""
낙폭은 **얼마나 자주 들여다보느냐**에 따라 달라진다. 반기마다 한 번 찍은 값으로 재면
그 사이의 골짜기가 안 보인다.

**두 개의 축이 겹쳐 있다** — ① 구간 vs 일별 ② gross vs net. "구간 −34% / 일별 −58%"
라고만 말하면 두 축을 하나로 뭉개는 것이다. 현행 채택안의 실제 값 셋:

| 기준 | 산출물 필드 | 현행 채택안 |
|---|---|---|
| 구간 · gross | `mdd` (= `endpoint_mdd_gross`) | **−34.14%** |
| 일별 · gross | `daily_mdd_gross` | **−57.08%** |
| **일별 · net** | `net.daily_mdd` | **−58.12%** |

**24%p 차이의 대부분은 측정 빈도에서 나온다** (−34.14% → −57.08%). 거래비용이 더하는
몫은 1%p 남짓(−57.08% → −58.12%)이다. "비용 때문에 낙폭이 커졌다"고 읽으면 틀린다 —
구간 끝 값만 이으면 구간 **안쪽**의 골짜기가 통째로 안 보이는 것이 원인이다.

**판정의 SSOT 는 일별 net 이다** (SPEC_13 §9-1). SPEC_10 G5 게이트가 −45% 한계선에
FAIL 인 것도 일별 net −58.12% 기준이고, 이것이 현행 채택 보류의 **단독 사유**다.

그런데 **비교표의 MDD·Sharpe 열은 구간 기준으로 통일돼 있다.** 일별 NAV 를 가진 태그가
76개 중 14개뿐이라, 있는 행만 일별 값으로 채우면 한 열에 두 정의가 섞이기 때문이다.
라벨을 붙여도 사람 눈은 숫자 크기를 먼저 보므로 정렬하는 순간 순위가 뒤집힌다.
그래서 **행이 아니라 열 단위로 기준을 고정**했고, 일별 값은 현행 채택 배너에만 뜬다.

> 요약: **축들끼리 비교할 때는 구간 기준, 게이트 판정을 인용할 때는 일별 기준.**
> 두 숫자를 한 문장에 섞어 쓰지 마라.
"""),

    Term(
        id='source_file_vs_summary',
        term="`source='file'` vs `'summary'`",
        one_line='단일 실행 산출물이냐, 500회 추첨의 분포 집계냐. 후자는 원래 파일이 없다.',
        aliases=('median_cagr', '분포집계', '단일실행', 'A_random'),
        series=(),
        sources=('dashboard/artifacts.py:185',),
        incident='',
        body="""
- **`file`** — `experiments/ablation/{key}.json` 이 실재한다 (72개). 단일 결정적 실행이다.
- **`summary`** — 파일이 없고 `summary.json` 의 `scenarios` 에만 있다 (4개:
  `A_random`·`B_hard_random`·`C_stability_random`·`C_no_r6`). **500회 무작위 추첨의
  분포 집계**라 단일 실행 산출물이라는 게 애초에 존재하지 않는다.

그래서 `summary` 행은 `cagr` 이 아니라 **`median_cagr`(중앙값)** 을 CAGR 열에 쓴다.
비교표 `출처` 열의 `분포집계`/`단일실행` 이 이 구분이다.

> 둘을 뭉개면 **"파일이 없다"와 "원래 파일로 존재하지 않는다"** 를 구별할 수 없다.
> 앞은 산출물이 서버에만 있다는 뜻이라 가져오면 되고, 뒤는 가져올 게 없다.
"""),

    Term(
        id='pbr_parent',
        term='`_parent` 접미사',
        one_line='"부모 실행"이 아니다. PBR 분모를 자본총계 대신 지배기업소유주지분으로 바꾼 랭킹 변형이다.',
        aliases=('rank_mode', 'pbr_parent', 'F_pbr_no_r3r4_parent', '지배기업소유주지분'),
        series=('ranking_signal',),
        sources=('backtest/ablation.py:633',
                 'docs/설계/SPEC_11_decomposition_live_manifest.md'),
        incident='이름 오독 탓에 `F_pbr_no_r3r4_parent` 가 인벤토리 어디에도 없이 미배정으로 남아 있었다 (2026-08-14 배정).',
        body="""
`F_pbr_no_r3r4_parent` 의 `_parent` 는 **실행 계보가 아니라 랭킹 신호**를 가리킨다
(`rank_mode='pbr_parent'`).

- 기본 PBR — 분모가 **자본총계**
- `_parent` — 분모가 **지배기업소유주지분** (연결 자본에서 비지배지분을 뺀 것)

자회사 지분이 큰 종목에서 둘이 크게 갈린다. 즉 이건 필터를 켜고 끈 변형이 아니라
**순위를 만드는 신호 자체를 바꾼 것**이라서 `랭킹 신호 분리` 축에 속한다.

> "부모 태그의 실행"으로 읽으면 이 산출물이 어느 축 소속인지 영영 정해지지 않는다.
> 실제로 그래서 한동안 미배정이었다.
"""),

    Term(
        id='rerun_vs_truncation',
        term='재실행 vs 절단 (종목 수 곡선)',
        one_line='종목 수 n 을 얻는 두 방법. 곡선의 모든 점은 절단이고, 재실행은 4개뿐이다.',
        aliases=('truncation', 'n_stocks_curve', 'tape', 'cross_check_vs_rerun'),
        series=('n_stocks',),
        sources=('scripts/analysis/n_stocks_curve.py',
                 'docs/설계/SPEC_10_pbr_gate_robustness.md'),
        incident='',
        body="""
같은 `n` 이라도 어떻게 얻은 값이냐가 다르다.

| 방법 | 무엇 | 개수 |
|---|---|---|
| **재실행** | 그 종목 수로 백테스트를 처음부터 다시 돌린 것 | **4개** (n=10/12/13/20) |
| **절단** | 이미 있는 n=20 holdings tape 을 앞에서 n 개만 잘라 재계산 | n=1..20 전부 |

절단이 성립하는 이유는 `build_portfolio` 가 `candidates[:n]` 인 **순수 접두어
슬라이스**이기 때문이다. 순위 1~13위는 n=13 이든 n=20 이든 같은 종목이다.

**산출물에서 둘을 구별하는 법**: `n_stocks_curve.json` 의 `points[]` 는 전부
`method='truncation'` 이다. 재실행값은 곡선에 섞이지 않고 `cross_check_vs_rerun[]` 에
따로 있으며, 거기서 `truncation_gross_cagr` 와 `rerun_gross_cagr` 를 나란히 대조한다.

> **절단 곡선은 gross · 구간 기준이다.** net 과 일별 MDD 는 이 방법으로 구할 수 없다 —
> 회전율이 n 에 따라 달라지고, 일별 지표의 SSOT 는 NAV 이기 때문이다. 곡선의
> MDD·Sharpe 를 [mdd_basis] 의 일별 값과 비교하지 마라.
"""),

    Term(
        id='status_codes',
        term='상태 코드 5종',
        one_line='축의 판정 상태. ARCHIVED 는 "틀렸다"가 아니라 "계보가 끝났다"는 뜻이다.',
        aliases=('ADOPTED', 'CLOSED_FAIL', 'CLOSED_PASS', 'EXPLORING', 'ARCHIVED', 'Status'),
        series=(),
        sources=('dashboard/series.py',),
        incident='',
        body=f"""
| 코드 | 뜻 |
|---|---|
{_STATUS_TABLE}

`ARCHIVED` 가 가장 오해받는다. 레이어 ablation(A~H) 축은 전부 **RIM 랭킹 경로**의
기록인데, 현행 채택안은 1/PBR 랭킹이라 계보가 다르다. 그 수치들은 그 당시 정확하지만
**현행 전략의 성적으로 인용하면 틀린다.**

> 상태는 문자열 하나가 아니라 `code`·`label`·`as_of`·`source` 네 필드다. `source` 는
> 실재하는 근거 문서 경로여야 하고, 검사가 이를 대조한다 — 죽은 링크는 근거가 아니라
> 근거인 척이다.
"""),
)

GLOSSARY_BY_ID: dict[str, Term] = {t.id: t for t in GLOSSARY}


# ── 조회 ────────────────────────────────────────────────────────────────────

def terms_for(series_id: str) -> tuple[Term, ...]:
    """축 하나에 붙는 용어. 공통 용어 + 그 축 전용.

    공통이 기본값이라, 새 축이 생겨도 핵심 용어는 자동으로 따라붙는다.
    """
    return tuple(t for t in GLOSSARY if t.is_common or series_id in t.series)


def search(query: str) -> tuple[Term, ...]:
    """용어·별칭·한 줄 정의를 훑는다. 별칭에 **코드에 실제로 쓰이는 식별자**를 넣어둔
    이유가 이것이다 — 사람은 화면에서 본 `median_cagr` 로 찾지 한글 설명으로 안 찾는다.
    """
    q = query.strip().lower()
    if not q:
        return GLOSSARY
    return tuple(t for t in GLOSSARY
                 if q in t.term.lower() or q in t.one_line.lower()
                 or any(q in a.lower() for a in t.aliases))


def source_file(source: str) -> str:
    """`path:LINE` 에서 경로만. 줄 번호는 표시용이지 조회용이 아니다."""
    return source.rsplit(':', 1)[0] if ':' in source else source


def index_rows(terms: tuple[Term, ...] | None = None) -> list[dict]:
    """목록 표 행. 화면이 아니라 여기서 만든다 — 화면 안에 있으면 검사할 수 없다."""
    return [{
        '용어': t.term,
        '한 줄 정의': t.one_line,
        '적용': '공통' if t.is_common else ', '.join(t.series),
        '사고 이력': '있음' if t.incident else '—',
    } for t in (GLOSSARY if terms is None else terms)]
