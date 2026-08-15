"""SERIES 등록 대장 — "이 축으로 무엇을 비교하나?"

카탈로그(`dashboard/artifacts.py`)가 "무엇이 존재하는가"를 답하면, 여기는 **어떤 축으로
무엇을 나란히 놓는가**를 답한다. 둘을 나눈 이유가 있다.

## 태그→시리즈는 1:N 이다 (분류가 아니라 membership)

한 태그가 여러 축의 baseline 으로 재사용된다. `F_pbr_no_r3r4` 는 PBR 룰 조합·캘린더·
랭킹 분해의 공통 baseline 이고, `D_rim_only` 는 레이어와 LOO 양쪽에 들어간다. "파일을
시리즈로 분류"하면 한 태그를 한 곳에만 넣게 되어 표현이 불가능해진다.

## 등록 대장이 배정을 소유한다

명명 규칙(`patterns`)으로 후보를 채우되, 최종 배정은 이 파일이 소유한다. 자동 수집만
믿으면 새 태그가 조용히 아무 축에도 안 들어가거나 엉뚱한 축에 붙는다. 어디에도 배정되지
않은 산출물은 **경고로 드러낸다** (`unassigned()`), 조용히 사라지게 두지 않는다.

## A형 / B형

- **A형** — 태그 성과 비교. `ablation/{key}.json` 을 읽어 제네릭 뷰로 그린다.
- **B형** — 검정·진단 산출물. 전용 요약 + 원본 링크. `paths` 가 원본 glob 이고
  `renderer` 는 나중에 붙일 전용 뷰의 키다 (없으면 raw fallback).
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field, replace

from backtest.ablation import ABLATION_CONFIGS
from dashboard.artifacts import ArtifactCatalog, ScenarioRef, build_catalog

#: 캘린더 안(案) 접미사. 런타임 파생이라 `ABLATION_CONFIGS` 에는 부모 태그만 있다.
CALENDAR_VARIANTS = {'A': '안A (분기 빈도)', 'C': '안C (위상 이동)'}


def _split_variant(ref: ScenarioRef) -> ScenarioRef:
    """캘린더 접미사를 base_tag 에서 떼어 `params['calendar']` 로 옮긴다.

    **떼어낸 나머지가 실제 config 일 때만 뗀다.** 이름 끝 글자만 보고 무조건 자르면
    `..._A` 로 끝나는 멀쩡한 태그를 망가뜨린다. "잘랐더니 아는 config 가 나왔다"는
    사실이 곧 파싱이 옳았다는 증거다.
    """
    if ref.base_tag in ABLATION_CONFIGS:
        return ref
    for v in CALENDAR_VARIANTS:
        parent = ref.base_tag[:-len(v) - 1]
        if ref.base_tag.endswith(f'_{v}') and parent in ABLATION_CONFIGS:
            return replace(ref, base_tag=parent,
                           params={**ref.params, 'calendar': v})
    return ref


#: 유형 코드 → (짧은 이름, 이 유형이 무엇인지). **화면이 `A` 라고만 쓰지 않게 하려고**
#: 등록 대장이 뜻까지 갖는다. 코드만 띄우면 처음 보는 사람은 뜻을 물어볼 데가 없다.
KIND_MEANING: dict[str, tuple[str, str]] = {
    'A': ('태그 성과 비교',
          'ablation 산출물(`{artifact_key}.json`)이 기록한 지표를 나란히 놓는 축이다. '
          '비교표·구간별·랜덤 분포로 그린다. **화면은 지표를 계산하지 않고 읽기만 한다.**'),
    'B': ('검정 · 진단 산출물',
          '성과 비교가 아니라 가설 검정·진단 결과다. 전용 뷰가 판정과 **그 판정을 만든 '
          '문턱**(또는 문턱이 없다는 사실)을 함께 띄운다 — 결론만 주면 사후 해석을 부른다.'),
}

#: 상태 코드 → 뜻. 용어사전(`dashboard/glossary.py`)이 이 정의를 그대로 쓴다.
#: 두 곳에 각자 적으면 한쪽만 고쳐진다 — 이 저장소가 이미 겪은 실패다.
STATUS_MEANING: dict[str, str] = {
    'ADOPTED':     '현행 운영 기준으로 채택됐다. `docs/CANONICAL.md` 와 일치해야 한다.',
    'CLOSED_PASS': '검정을 통과하고 종결됐다.',
    'CLOSED_FAIL': '검정에서 떨어져 종결됐다. **결론이 난 것이지 미완이 아니다.**',
    'EXPLORING':   '아직 진행 중이다. 여기 수치를 결론으로 인용하면 안 된다.',
    'ARCHIVED':    '계보 자체가 끝났다. 실험이 틀린 게 아니라 **전제가 교체됐다** — '
                   '그 수치는 당시엔 정확하지만 현행 성적으로 인용하면 틀린다.',
}


@dataclass(frozen=True)
class Status:
    """판정·종결 상태. 문자열 하나로 두면 "종결만 보기" 같은 필터가 불가능하다."""

    code: str          # CLOSED_PASS | CLOSED_FAIL | EXPLORING | ADOPTED | ARCHIVED
    label: str
    as_of: str
    source: str

    @property
    def meaning(self) -> str:
        """코드의 뜻. 화면이 코드만 띄우지 않게 한다."""
        return STATUS_MEANING.get(self.code, '')


#: `내 이해` 칸에 붙일 수 있는 라벨. 이 저장소의 문서 표기 규약과 같다 —
#: 확인한 것과 해석한 것을 같은 무게로 적으면 나중에 구별할 방법이 없다.
CONFIDENCE = ('검증된 사실', 'Claude 의견', '확실하지 않은 사실')


@dataclass(frozen=True)
class WhyMap:
    """왜-지도 (sub1) — "6개월 뒤 따라잡기"가 목적이다. 판정 재현이 아니다.

    성적표(A형 비교표)는 **무엇이 나왔나**를 답하지만 **왜 이 축이 있고 무엇이 결정
    됐나**는 답하지 못한다. 그 계보는 SPEC·검토 문서에 흩어져 있어서, 6개월 뒤에
    화면만 보면 "그래서 이걸 왜 봤더라"가 된다.

    `history` 가 핵심이다. 결론만 적으면 다음 사람이 같은 실험을 다시 한다.
    """

    variable: str                                   # 무엇을 바꾸나
    question: str                                   # 이 축이 답하는 질문
    failure_mode: str                               # 이 축이 막는 착각
    history: tuple[str, ...]                        # 먹여준 결정 (시간순)
    warnings: tuple[str, ...] = ()                  # 탐색 경고
    understanding: tuple[tuple[str, str], ...] = ()  # (세부, 라벨)
    sources: tuple[str, ...] = ()                   # 근거 문서 (repo 상대 경로)


@dataclass(frozen=True)
class SeriesSpec:
    id: str
    title: str
    kind: str                       # 'A' | 'B'
    changes: str                    # 무엇을 바꿨나
    status: Status
    baseline: str | None = None     # 비교 기준 artifact_key
    tags: tuple[str, ...] = ()      # 명시 배정 (우선)
    patterns: tuple[str, ...] = ()  # 명명 규칙 후보
    exclude: tuple[str, ...] = ()   # 패턴에서 뺄 것 (다른 축 소유)
    # 화면에서 함께 묶어 보여줄 세트. **순서가 곧 표시 순서**이고, 세트 사이에는
    # 빈 줄이 들어간다. 비어 있으면 baseline 을 맨 앞으로 올리고 나머지는 이름순인데,
    # 그러면 `D_rim_only` 가 짝(`D_no_r6`)에서 떨어져 on/off 대조가 안 보인다.
    groups: tuple[tuple[str, ...], ...] = ()
    periods_per_year: int | None = 2   # None = 축마다 다름. 구간 수는 산출물에서 읽는다
    paths: tuple[str, ...] = ()     # B형 원본 (repo 상대 glob)
    renderer: str | None = None     # B형 전용 뷰 키
    notes: str = ''                 # 축을 처음 보는 사람에게 필요한 배경 (마크다운)
    why: WhyMap | None = None       # 왜-지도 (sub1). 없으면 화면에 그 칸이 안 뜬다


@dataclass(frozen=True)
class Series:
    spec: SeriesSpec
    members: tuple[ScenarioRef, ...]
    missing: tuple[str, ...] = field(default=())   # 등록 대장엔 있는데 산출물이 없는 키

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def title(self) -> str:
        return self.spec.title


# ── 상태 상수 ────────────────────────────────────────────────────────────────

_SPEC05 = 'docs/설계/SPEC_05_backtest.md'
_SPEC10 = 'docs/설계/SPEC_10_pbr_gate_robustness.md'
_SPEC13 = 'docs/설계/SPEC_13_rebalance_calendar_v0.7.md'
_SPEC14 = 'docs/설계/SPEC_14_calendar_sensitivity_v0.3.md'

_CLOSED = lambda label, as_of, src: Status('CLOSED_FAIL', label, as_of, src)  # noqa: E731

#: 사람이 읽는 이름. 레거시 ablation 페이지가 갖고 있던 자산을 여기로 옮겼다 —
#: 화면이 둘이면 한쪽만 고쳐지는 상태가 되기 때문이다(2026-08-14 오염이 그랬다).
LABELS: dict[str, str] = {
    'A_random':           'A  랜덤 (필터 없음)',
    'B_hard_random':      'B  Hard + 랜덤',
    'C_stability_random': 'C  Hard + Stability + 랜덤',
    'C_no_r6':            "C′ Hard + Stability(−R6) + 랜덤",
    'D_rim_only':         'D  Hard + Stability + RIM',
    'D_no_r6':            "D′ RIM (R6 제외)",
    'E_screener_rim':     'E  D + 팩터스크리닝',
    'E_no_r6':            "E′ E (R6 제외)",
    'F_momentum_rim':     'F  D + 모멘텀',
    'F_no_r6':            "F′ F (R6 제외)",
    'G_full':             'G  전체 (E + F)',
    'G_no_r6':            "G′ 전체 (R6 제외)",
    'H_no_stability':     'H  G − Stability',
}

# raw 문자열이다. 본문의 `\~` 는 **마크다운 이스케이프**라 파이썬이 건드리면 안 된다 —
# 물결표 두 개가 한 줄에 있으면 Streamlit 이 그 사이를 취소선으로 렌더한다
# (`A~C ... D~H` 가 통째로 그어져 나갔다, 2026-08-15).
_LAYER_NOTES = r"""
**Ablation Test** 란 필터를 하나씩 추가해 가며 각 구성 요소가 수익률에 얼마나 기여하는지
측정하는 실험이다. A(아무 필터 없는 랜덤 매매)에서 시작해 G(모든 필터 적용)까지 쌓는다.
A\~C 는 필터 통과 후 **무작위 추첨을 500회 반복**한 분포이고, D\~H 는 단일 결정적 실행이다.
C→D 차이가 RIM 모델 자체의 기여를 보여준다.

| 레이어 | 무엇을 거르나 |
|---|---|
| 🔒 **Hard Filter** | 일평균 거래대금 1억 미만·상장 6개월 미만 제외. 슬리피지로 현실성이 깨지는 종목을 뺀다 |
| 🏦 **Stability Filter** | 재무안정성 하드 룰 6개 중 하나라도 걸리면 탈락 — R1 부채비율>200% · R2 차입금비율>150%(3FY 단조 개선이면 예외) · R3 최근 3FY 중 매출 YoY −5% 이하 2회 · R4 영업CF 2년 연속 음수 · R5 영업CF<0 & 재무CF>0(차입 운영) · R6 adjROE < 요구수익률 |
| 🔍 **Factor Screener** | 매출·영업이익 성장(각 1/6), GPA(1/3), 1/PBR(1/3) 복합 점수 상위 20% |
| 📈 **Momentum Filter** | 가격이 추세 위에 있는 종목만. "좋은 기업이라도 지금 하락 중이면 사지 않는다" (밸류 트랩 회피) |
| 💡 **RIM 적정가** | 주주자본 + 초과이익 누적으로 적정가 산출. 고평가 5% 초과 제외 후 상승여력순 편입 |

> ⚠️ 이 축은 **RIM 랭킹 경로**의 기록이다. 현행 채택안은 1/PBR 랭킹이라 계보가 다르다 —
> 여기 수치를 현행 성적으로 인용하지 마라.
"""


_LOO_NOTES = r"""
**LOO (leave-one-out, "하나만 빼기")** 는 나머지를 **전부 그대로 둔 채 한 가지만** 끄고
같은 백테스트를 다시 돌려, 그 하나가 성적에 얼마나 기여했는지 재는 방법이다. 여러 개를
동시에 바꾸면 차이가 어느 쪽에서 왔는지 말할 수 없기 때문이다.

이 축이 껐다 켜는 것은 **R6** — *adjROE 가 요구수익률(r)보다 낮으면 탈락* 규칙이다.

각 세트는 **같은 구성에서 R6 만 켠 것과 끈 것 한 쌍**이고, **쌍 안의 차이가 곧 R6 의
기여**다. `′` 가 붙은 쪽이 R6 를 뺀 것이다.

| 세트 | 구성 | R6 켬 | R6 끔 |
|---|---|---|---|
| C | Hard + Stability + 랜덤 | `C_stability_random` | `C_no_r6` |
| D | + RIM 랭킹 | `D_rim_only` | `D_no_r6` |
| E | + 팩터 스크리너 | `E_screener_rim` | `E_no_r6` |
| F | + 모멘텀 | `F_momentum_rim` | `F_no_r6` |
| G | 전체 (E + F) | `G_full` | `G_no_r6` |

> ⚠️ **세트를 가로질러 비교하지 마라.** 예컨대 `C_no_r6` 와 `G_full` 을 견주면 R6 말고도
> 스크리너·모멘텀이 함께 달라져 있어서, 그 차이는 R6 의 기여가 아니다.

> ⚠️ 이 축은 **RIM 랭킹 경로**의 기록이다. 현행 채택안은 1/PBR 랭킹이라 계보가 다르다 —
> 여기 수치를 현행 성적으로 인용하지 마라.
"""


_APPENDIX_A = 'docs/설계/SPEC_05_부록A_StabilityFilter검증.md'

_WHY_LAYERS = WhyMap(
    variable='필터 레이어를 A→G 로 하나씩 쌓는다 (Hard → Stability → 스크리너/모멘텀 → RIM). '
             'H 는 G 에서 Stability 를 뺀 대조군으로 만들어졌다.',
    question='각 레이어가 성적에 얼마나 기여하나? 특히 **C→D 차이가 RIM 모델 자체의 기여**다.',
    failure_mode='"파이프라인 전체가 좋다"는 뭉뚱그린 주장. 어느 레이어가 일하는지 모른 채 '
                 '전부 유지하면, 사족인 레이어가 비용과 회전율만 올리며 남는다.',
    history=(
        '**2026-07-02** Phase 2 ablation 최초 실행 — 레이어별 기여를 처음 관측했다.',
        '**2026-07-05 팩터 스크리너 폐기.** E(스크리너 추가)가 D 보다 **−5.7%p** 였다. '
        '현행 경로에서 빠졌고, 그래서 지금 채택안에는 스크리너가 없다. (부록A §0)',
        '**2026-07-05 게이트 지표가 틀렸다는 지적.** 당시 게이트는 `C > B (p95 기준)` 였는데 '
        'Stability 는 상방이 아니라 **하방을 막는** 필터다 — B→C 에서 p5 +2.63%p · '
        '중앙값 +2.12%p 인데 p95 는 **−0.19%p** 로 오히려 낮아진다. 상방 지표로 판정하면 '
        '"기여 없음"이라는 반대 결론이 나온다. (부록A §0·§1)',
        '**2026-07 RIM 랭킹 폐기 → 1/PBR 경로로 교체.** 이 축의 계보는 여기서 끝났다. '
        '이후 결정은 PBR 경로 축들(`pbr_rules`·`momentum_grid`)에서 이어진다.',
    ),
    warnings=(
        '**H 는 Stability 대조군이 아니다.** `H_no_stability` 는 `use_stability=False` 인 동시에 '
        '`use_screener=True` 인데 F 는 스크리너가 꺼져 있다 — 두 축이 함께 달라져 교란됐다. '
        '**F−H 를 "Stability 의 기여"로 읽으면 틀린다.** (부록A §2, 지금도 코드가 그렇다)',
        '**A~C 와 D~H 는 값의 종류가 다르다.** 앞 셋은 무작위 추첨 500회의 **중앙값**이고 '
        '뒤는 **단일 결정적 실행**이다. 그래서 화면에서 두 덩어리를 떼어 그린다.',
        '이 축은 **RIM 랭킹 경로**의 기록이다. 현행 채택안은 1/PBR 랭킹이라 계보가 다르다 — '
        '여기 수치를 현행 성적으로 인용하면 틀린다.',
    ),
    understanding=(
        ('레이어를 쌓아 기여를 분리한다는 논리', '검증된 사실'),
        ('스크리너 폐기 근거 (E vs D −5.7%p)', '검증된 사실'),
        ('H 의 교란 (stability·screener 동시 변경)', '검증된 사실'),
        ('"Stability 는 하방 방어형 필터"라는 해석', 'Claude 의견'),
        ('레이어별 차이가 통계적으로 유의한지', '확실하지 않은 사실'),
    ),
    sources=(_APPENDIX_A, 'docs/설계/SPEC_05_backtest.md'),
)

_WHY_R6 = WhyMap(
    variable='R6(adjROE < 요구수익률이면 탈락)만 껐다 켠다. 나머지 구성은 세트 안에서 고정.',
    question='안정성 필터 **안에서** R6 이 실제로 일하는가? 6개 룰 중 주력인가?',
    failure_mode='"안정성 필터가 통째로 기여한다"는 뭉뚱그린 착각. 어느 룰이 일하는지 모르면 '
                 '사족인 룰이 유니버스만 좁히며 남는다.',
    history=(
        '**2026-07-05** R6 을 단독 격리해 처음 쟀다 — 랜덤 유니버스 기준 중앙값 **+1.39%p**, '
        'p5 +0.93%p, p95 +0.90%p. 하방만 막는 게 아니라 **수익·하방 동시**로 움직여, '
        '필터 내부의 **주력**으로 판단했다. (부록A §1)',
        '같은 문서에서 **R1~R5 합계**는 중앙값 +0.73%p · p5 +1.70%p 로 **하방 방어 편중**이었다. '
        '다만 R1~R5 **개별**은 그때까지 한 번도 격리된 적이 없었고, 그래서 `안정성 룰 개별` 축이 '
        '따로 만들어졌다.',
        '가설 `R6 > R1≈R4≈R5 > R2≈R3` 은 **불확실**로 남았다 — R6 격리치 외에는 측정된 적이 '
        '없다. (부록A G-4)',
        '**현행 채택안은 R6 을 유지한다** — `{R1,R2,R5,R6}`. 다만 그 결정은 이 축(RIM 경로)이 '
        '아니라 PBR 경로의 룰 조합 축에서 내려졌다.',
    ),
    warnings=(
        '**세트를 가로질러 비교하지 마라.** 쌍 안의 차이만이 R6 의 기여다. `C_no_r6` 와 '
        '`G_full` 을 견주면 스크리너·모멘텀까지 달라져 있다.',
        '**부호가 세트마다 뒤집힌다.** 지금 산출물 기준 C·D·F 에서는 R6 을 켜는 쪽이 낫지만 '
        '**E·G 에서는 끄는 쪽이 낫다.** E·G 는 팩터 스크리너가 켜진 두 세트이고(`use_screener=True`), '
        '그 스크리너는 2026-07-05 에 폐기됐다. `[Claude 의견]` 스크리너와 R6 이 같은 종목을 '
        '두 번 거르는 상호작용으로 보이나 **검증된 적 없다.**',
        '`_no_r6` 의 **holdings** 4개는 상폐 판정 버그 수정(2026-07-06) 이전에 만들어져 '
        '**종목 단위 표시는 신뢰할 수 없다.** 집계 지표는 영향 없음이 확인됐다 '
        '(GAPS.md `PROV-ABL-001`).',
    ),
    understanding=(
        ('LOO 로 룰 기여를 분리한다는 논리', '검증된 사실'),
        ('R6 격리치 +1.39%p (2026-07-02 랜덤 기준)', '검증된 사실'),
        ('E·G 에서 R6 의 부호가 뒤집힌다', '검증된 사실'),
        ('뒤집힘의 원인이 스크리너와의 상호작용', 'Claude 의견'),
        ('R1~R5 개별 기여의 순위', '확실하지 않은 사실'),
        ('R6 을 유지한다는 현행 결정이 RIM 경로 근거와 무관한지', '확실하지 않은 사실'),
    ),
    sources=(_APPENDIX_A, 'docs/audit/GAPS.md'),
)


# ── 정본 인벤토리 — 16축 ────────────────────────────────────────────────────

SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec(
        id='layers', title='레이어 ablation', kind='A',
        changes='필터 레이어를 A~H 로 하나씩 쌓는다',
        baseline='D_rim_only',
        tags=('A_random', 'B_hard_random', 'C_stability_random', 'D_rim_only',
              'E_screener_rim', 'F_momentum_rim', 'G_full', 'H_no_stability'),
        # A→H 누적 순서가 이 축의 전부다. baseline(D)을 맨 위로 올리면 "쌓아 간다"는
        # 이야기가 끊긴다. 세 덩어리로 나눈 이유는 **값의 종류가 다르기 때문**이다 —
        # A~C 는 500회 추첨의 중앙값, D~G 는 단일 결정적 실행, H 는 대조군이다.
        groups=(('A_random', 'B_hard_random', 'C_stability_random'),
                ('D_rim_only', 'E_screener_rim', 'F_momentum_rim', 'G_full'),
                ('H_no_stability',)),
        notes=_LAYER_NOTES, why=_WHY_LAYERS,
        status=Status('ARCHIVED', 'RIM 경로 — 랭킹 폐기로 계보 종료', '2026-07', _SPEC05)),

    SeriesSpec(
        id='r6_loo', title='R6 단독 (LOO, leave-one-out)', kind='A',
        changes='R6(adjROE < r) 만 껐다 켠다',
        baseline='D_rim_only',
        tags=('C_stability_random', 'C_no_r6', 'D_rim_only', 'D_no_r6',
              'E_screener_rim', 'E_no_r6', 'F_momentum_rim', 'F_no_r6',
              'G_full', 'G_no_r6'),
        # R6 켬/끔 한 쌍이 한 세트다. 쌍 안의 차이만이 R6 의 기여다 — 세트를
        # 가로질러 비교하면 다른 필터까지 함께 달라진다.
        groups=(('C_stability_random', 'C_no_r6'),
                ('D_rim_only', 'D_no_r6'),
                ('E_screener_rim', 'E_no_r6'),
                ('F_momentum_rim', 'F_no_r6'),
                ('G_full', 'G_no_r6')),
        notes=_LOO_NOTES, why=_WHY_R6,
        status=Status('ARCHIVED', 'RIM 경로 — 현행은 R6 유지', '2026-07', _SPEC05)),

    SeriesSpec(
        id='stability_loo_d', title='안정성 룰 개별 (D 기준)', kind='A',
        changes='R1~R5 중 하나만 제거',
        baseline='D_rim_only',
        tags=('D_rim_only',), patterns=('D_no_r[1-5]',),
        status=Status('ARCHIVED', 'RIM 경로 결정 {R1,R4,R5,R6} — 현행과 다른 계보',
                      '2026-07', _SPEC05)),

    SeriesSpec(
        id='stability_combo_f', title='안정성 룰 조합 (F 기준)', kind='A',
        changes='R2·R3·R4 를 조합으로 제거',
        baseline='F_momentum_rim',
        tags=('F_momentum_rim',), patterns=('F_no_r[234]*',),
        status=Status('ARCHIVED', 'RIM 경로', '2026-07-07', _SPEC05)),

    SeriesSpec(
        id='stability_all', title='안정성 필터 전체 on/off', kind='A',
        changes='stability 레이어를 통째로 끈다',
        baseline='F_momentum_rim',
        tags=('D_rim_only', 'D_no_stability', 'F_momentum_rim',
              'F_no_stability_clean', 'G_full', 'H_no_stability'),
        status=Status('ARCHIVED', 'RIM 경로', '2026-07', _SPEC05)),

    SeriesSpec(
        id='momentum_grid', title='모멘텀 기준 그리드', kind='A',
        changes='MA 창·이중MA·cd/sl·시장초과·52주·절대수익·부호수 (23종)',
        baseline='F_pbr_no_r3r4',
        tags=('F_pbr_no_r3r4',),
        patterns=('F_pbr_ma*', 'F_pbr_52w*', 'F_pbr_absret*',
                  'F_pbr_mktresid*', 'F_pbr_signcount*'),
        exclude=('F_pbr_ma200_n*',),        # 종목 수 축(#16) 소유
        status=Status('ADOPTED', 'MA200 채택 — 현행 운영 기준', '2026-08-11', _SPEC14)),

    SeriesSpec(
        id='pbr_rules', title='PBR-경로 안정성 룰 조합', kind='A',
        changes='1/PBR 랭킹 위에서 R 룰 조합을 바꾼다',
        baseline='F_pbr_no_r3r4',
        tags=('F_pbr_no_r3r4', 'F_pbr_no_r1r2r3r4', 'F_pbr_no_r1r3r4',
              'F_pbr_no_r2r3r4', 'F_pbr_no_r3r4r6', 'F_pbr_nostab',
              'F_pbr_r6only', 'F_pbr_r6'),
        status=Status('ADOPTED', '{R1,R2,R5,R6} 채택', '2026-08', _SPEC10)),

    SeriesSpec(
        id='ranking_signal', title='랭킹 신호 분리', kind='A',
        changes='RIM vs 1/PBR vs 팩터 — 무엇이 순위를 만드나',
        baseline='D_rim_only',
        # `_parent` 는 랭킹 자체를 바꾼다 — PBR 분모가 자본총계가 아니라 지배기업
        # 소유주지분이다 (rank_mode='pbr_parent', SPEC_11 §3). 이름만 보면 "부모 실행"
        # 으로 오독하기 쉬워 미배정으로 남아 있었다.
        tags=('D_rim_only', 'D_pbr_only', 'D_factor_only', 'D_pbr_no_r3r4',
              'F_momentum_rim', 'F_pbr_only', 'F_pbr_no_r3r4_parent'),
        status=Status('CLOSED_FAIL', 'RIM 랭킹 근거 상실 → 1/PBR 로 교체', '2026-07', _SPEC10)),

    SeriesSpec(
        id='screener_single', title='스크리너 단일 팩터 (폐기)', kind='A',
        changes='복합 스크리너를 단일 팩터로 분해',
        baseline='E_screener_rim',
        tags=('E_screener_rim',), patterns=('E_*_only',),
        status=Status('ARCHIVED', '팩터 스크리너 미사용으로 종결', '2026-07', _SPEC05)),

    SeriesSpec(
        id='benchmarks', title='채택 후보 대조군', kind='A',
        changes='귀무(랜덤 추첨)·EW 벤치와 대조',
        baseline='F_pbr_ma200_n13',
        tags=('U_pbr_path_ew', 'F_pbr_ma200_n13'),
        paths=('experiments/robustness/C_pbr_path_random_draws.csv',
               'experiments/robustness/random_summary_n13.json',
               'experiments/robustness/gate_results_F_pbr_ma200_n13.json'),
        status=Status('CLOSED_FAIL', 'G1·G2 PASS · G5 FAIL → 채택 보류', '2026-08-12', _SPEC10)),

    SeriesSpec(
        id='calendar_phase', title='캘린더 — 위상/빈도', kind='A',
        changes='안A(분기 빈도) vs 안C(위상 이동) vs 현행 반기',
        baseline='F_pbr_no_r3r4',
        tags=('F_pbr_no_r3r4', 'F_pbr_no_r3r4_A', 'F_pbr_no_r3r4_C',
              'U_pbr_path_ew', 'U_pbr_path_ew_A', 'U_pbr_path_ew_C'),
        periods_per_year=None,   # 안A=분기·안C/현행=반기 — 하나로 못 적는다
        status=_CLOSED('두 후보 FAIL — 캘린더 축 종결', '2026-08-10', _SPEC13)),

    SeriesSpec(
        id='regime_overlay', title='레짐/타이밍 오버레이 (Phase A/B)', kind='B',
        changes='Signal→Tilt 그리드',
        paths=('experiments/runs/*phaseB*.csv', 'experiments/runs/*REGIME_PHASE*.md',
               'experiments/runs/2026-07-07_*.png'),
        renderer='regime_overlay',
        status=Status('CLOSED_FAIL', 'Layer2 0/144 — 기여 없음', '2026-07-10',
                      'docs/설계/SPEC_08_regime_phaseB.md')),

    SeriesSpec(
        id='calendar_bootstrap', title='캘린더 민감도 (block-bootstrap)', kind='B',
        changes='쌍대 bootstrap contrast',
        paths=('experiments/calendar_sens/stage_b.json',
               'experiments/calendar_sens/stage_b_tables.md',
               'experiments/runs/*CALENDAR_SENS_B.md'),
        renderer='calendar_bootstrap',
        status=_CLOSED('Stage B — 우열 불확정', '2026-08-10', _SPEC14)),

    SeriesSpec(
        id='time_overfit', title='캘린더 민감도 (시간분할 과적합)', kind='B',
        changes='전/후반 순위 역전 검정',
        paths=('experiments/calendar_sens/time_split.json',
               'experiments/calendar_sens/plateau.json',
               'experiments/calendar_sens/rank_stability.json',
               'experiments/calendar_sens/integrity_gates.json',
               'experiments/calendar_sens/stage_a.json'),
        renderer='time_overfit',
        status=Status('CLOSED_FAIL', 'TIME_OVERFIT_CONFIRMED', '2026-08-10', _SPEC14)),

    SeriesSpec(
        id='decomposition', title='성과 분해 / 라이브 전환', kind='B',
        changes='희생자·룰 멤버십·선호 스캔·dry-run',
        # 파일을 **열거한다.** `experiments/analysis/*.json` 으로 훑으면 종목 수 축(#16)의
        # `n_stocks_curve.json` 까지 삼켜, 분해와 무관한 산출물이 이 축의 원본 목록에
        # 뜬다 (2026-08-15 발견). A형에서 `exclude` 로 막은 것과 같은 종류의 사고다.
        paths=('experiments/analysis/momentum_decomposition.json',
               'experiments/analysis/rule_membership.json',
               'experiments/analysis/preferred_scan.json',
               'experiments/live/dryrun/manifest.yaml'),
        renderer='live_decomposition',
        status=Status('EXPLORING', '라이브 전환 준비 중', '2026-08-10',
                      'docs/설계/SPEC_11_decomposition_live_manifest.md')),

    SeriesSpec(
        id='n_stocks', title='포트폴리오 종목 수 민감도', kind='A',
        changes='종목 수 n — 재실행 4개(10/12/13/20) + tape 절단 곡선 n=1..20',
        baseline='F_pbr_ma200_n13',
        patterns=('F_pbr_ma200_n*',),
        # 재실행은 4개(10/12/13/20)뿐이지만, build_portfolio 가 순수 접두어 슬라이스라
        # n=1..20 곡선은 tape 절단으로 구할 수 있다. G5-MDD 과제가 인용하는 "구간간
        # 표준편차가 안 줄어든다"의 근거가 여기 있다 — 이전에는 코드 주석과 산문에만
        # 있어 재현이 불가능했다 (2026-08-14).
        paths=('experiments/analysis/n_stocks_curve.json',),
        renderer='n_stocks_curve',
        status=Status('ADOPTED', 'n=13 채택 (낙폭은 미해결)', '2026-08-11', _SPEC10)),
)

SERIES_BY_ID = {s.id: s for s in SERIES}


# ── 해석 ────────────────────────────────────────────────────────────────────

def resolve(spec: SeriesSpec, catalog: ArtifactCatalog | None = None) -> Series:
    """스펙 + 카탈로그 → 실제 멤버. 없는 키는 **버리지 않고 `missing` 으로 보고한다.**"""
    catalog = catalog if catalog is not None else build_catalog()
    keys = list(spec.tags)
    for pat in spec.patterns:
        keys += [k for k in catalog.keys() if fnmatch.fnmatchcase(k, pat)]
    for pat in spec.exclude:
        keys = [k for k in keys if not fnmatch.fnmatchcase(k, pat)]

    seen: set[str] = set()
    members, missing = [], []
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        (members if k in catalog else missing).append(k)

    if spec.groups:
        # 세트가 있으면 **그 순서가 정답이다.** baseline 을 위로 올리지 않는다 —
        # 올리면 짝에서 떨어져 나가 on/off 대조가 사라진다. 세트에 없는 멤버는
        # 뒤에 이름순으로 붙인다 (조용히 사라지지 않게).
        order = {k: i for i, k in enumerate(k for g in spec.groups for k in g)}
        members.sort(key=lambda k: (order.get(k, len(order)), k))
    elif spec.baseline in seen:
        # baseline 을 맨 앞으로. 비교표에서 기준이 첫 행이어야 눈이 덜 미끄러진다.
        members.sort(key=lambda k: (k != spec.baseline, k))
    else:
        members.sort()
    return Series(spec=spec,
                  members=tuple(_split_variant(ScenarioRef.from_key(k, LABELS.get(k)))
                                for k in members),
                  missing=tuple(missing))


def resolve_all(catalog: ArtifactCatalog | None = None) -> list[Series]:
    catalog = catalog if catalog is not None else build_catalog()
    return [resolve(s, catalog) for s in SERIES]


def unassigned(catalog: ArtifactCatalog | None = None) -> list[str]:
    """어느 축에도 안 들어간 산출물. 조용히 사라지게 두지 않는다."""
    catalog = catalog if catalog is not None else build_catalog()
    used = {m.artifact_key for s in resolve_all(catalog) for m in s.members}
    return sorted(k for k in catalog.keys() if k not in used)
