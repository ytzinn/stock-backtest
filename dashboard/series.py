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
from pathlib import Path

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
class Delta:
    """두 시나리오의 차이 한 줄. **화면이 산출물의 CAGR 두 개를 빼서 보여준다.**

    "레이어를 하나 추가하면 얼마가 달라지나"가 이 축들의 질문인데, 표에는 절대값만
    있어서 사람이 머릿속으로 뺄셈을 한다. 그러다 엉뚱한 두 행을 빼면(예: 세트가 다른
    둘) 조용히 틀린다. 어느 둘을 빼야 하는지를 등록 대장이 정해 준다.
    """

    label: str
    base: str        # 기준 artifact_key
    variant: str     # 바꾼 쪽 artifact_key
    note: str = ''
    # 이 뺄셈이 **실제로 몇 개를 바꾸나**. 산문으로 적힌 주의는 기계가 못 읽는다 —
    # `dashboard/claims` 가 조건표와 대조해 어긋나면 검사를 깨뜨린다. 룰은 하나하나가
    # 축이다(`stage_b` 의 `n_axes` 와 같은 눈금). 기본 1 = "하나만 바꾼 짝".
    axes: int = 1
    # 세트를 가로지르는 비교인가. 기본은 금지다 — 세트를 넘으면 여러 조건이 함께
    # 달라진 값이 "무엇을 바꿨나" 한 줄로 뜬다. 그래도 필요한 자리가 있어서(무작위
    # 추첨 vs 모델 선택) **명시적으로 켤 때만** 허용하고, 화면이 그 사실을 표시한다.
    crosses_sets: bool = False


@dataclass(frozen=True)
class Elsewhere:
    """이 축에 속하지만 **`experiments/ablation/` 에 산출물이 없는** 태그.

    `missing` 과 혼동하면 안 된다. `missing` 은 *"있어야 하는데 없다"* — 유실이거나
    개발 PC 에 안 받아진 것이고, 화면이 빨간 오류로 띄운다. 여기는 *"애초에 그 형태로
    존재하지 않는다"* 다. 카탈로그가 `source='file'` 과 `source='summary'` 를 나눠 둔
    것과 같은 종류의 구별이고, 뭉개면 **정상 상태가 영구 오류로 뜬다.**

    실제로 넷 다 그렇다. `C_pbr_path_random` 은 `run_random_pool.py` fast-path 가
    `experiments/robustness/` 로 뽑고, 랭킹×컷 2×2 세 태그는 캘린더 민감도 B단계가
    `experiments/calendar_sens/stage_b.json` 안에서만 굴린다. `run_ablation` 을 한
    번도 태운 적이 없으니 `{tag}.json` 이 있을 리 없다.

    그래도 **축에는 배정한다.** 안 하면 태그 매트릭스가 `미배정` 이라 부르고 화면
    어디에도 안 떠서, 판정의 근거가 된 태그가 만들어 두고 잊은 것처럼 남는다.
    """

    tag: str
    where: tuple[str, ...]   # 값이 실제로 있는 산출물 (repo 상대 경로)
    why: str                 # 왜 ablation 산출물이 없나
    note: str = ''           # 그 값을 읽을 때의 주의 (기간·눈금이 다르다 등)


@dataclass(frozen=True)
class WhyMap:
    """왜-지도 (sub1) — "6개월 뒤 따라잡기"가 목적이다. 판정 재현이 아니다.

    성적표(A형 비교표)는 **무엇이 나왔나**를 답하지만 **그래서 그게 무슨 뜻이고 무엇이
    결정됐나**는 답하지 못한다. 그 계보는 SPEC·검토 문서에 흩어져 있어서, 6개월 뒤에
    화면만 보면 "그래서 이걸 왜 봤더라"가 된다.

    **`reading`(결과 해석)이 맨 앞이다.** 사람이 화면에 온 이유는 숫자를 봤기 때문이지
    방법론이 궁금해서가 아니다. 배경부터 늘어놓으면 정작 답을 못 찾고 닫는다.
    `history` 는 그다음으로 중요하다 — 결론만 적으면 다음 사람이 같은 실험을 다시 한다.
    """

    reading: tuple[str, ...]                        # 결과가 무슨 뜻인가 (맨 앞)
    variable: str                                   # 이 축이 바꾸는 것
    question: str                                   # 이 축이 답하려는 질문
    failure_mode: str                               # 이 축이 막으려는 오해
    history: tuple[str, ...]                        # 여기서 내려진 결정 (시간순)
    deltas: tuple[Delta, ...] = ()                  # 화면이 빼서 보여줄 두 시나리오
    warnings: tuple[str, ...] = ()                  # 읽을 때 주의할 점
    understanding: tuple[tuple[str, str], ...] = ()  # (세부, 확신 라벨)
    sources: tuple[str, ...] = ()                   # 근거 문서 (repo 상대 경로)
    # 이 축이 남긴 질문을 어느 축이 받았나. 축들이 서로를 가리키지 않으면 화면이
    # 16개의 섬이 되고, "그래서 다음에 뭘 봤나"를 매번 문서에서 다시 찾아야 한다.
    next_step: str = ''
    next_axes: tuple[str, ...] = ()                 # 이어받는 축 id


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
    # 이 축에 속하지만 ablation 산출물이 없는 태그. 배정으로는 세되(`claimed_keys`)
    # 비교표 멤버로도 `missing` 으로도 잡지 않는다 — 없는 게 아니라 다른 데 있다.
    elsewhere: tuple[Elsewhere, ...] = ()
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
    'D_rim_only':         'D  RIM 랭킹',
    'D_no_r6':            "D′ RIM 랭킹 (−R6)",
    'E_screener_rim':     'E  D + 팩터스크리닝',
    'E_no_r6':            "E′ E (R6 제외)",
    'F_momentum_rim':     'F  RIM 랭킹 + 모멘텀',
    'F_no_r6':            "F′ RIM 랭킹 + 모멘텀 (−R6)",
    'G_full':             'G  전체 (E + F)',
    'G_no_r6':            "G′ 전체 (R6 제외)",
    'H_no_stability':     'H  G − Stability',
    # 랭킹 신호 축. 어느 신호로 줄을 세운 것인지가 이름에서 보여야 한다.
    'D_pbr_only':         'D  1/PBR 랭킹 (−R6)',
    'D_factor_only':      'D  팩터 복합 랭킹 (−R6)',
    'D_pbr_no_r3r4':      'D  1/PBR (−R3·R4)',
    'F_pbr_only':         'F  1/PBR + 모멘텀 (−R6)',
    'F_pbr_no_r3r4_parent': 'F  1/PBR (분모=지배지분)',
    # PBR 룰 조합 축. **이 축에서만 쓰이는 태그에만 붙인다** — 라벨은 전역이라
    # 여러 축에 든 태그(`F_pbr_r6`·`F_pbr_no_r3r4`)에 이 축의 어법으로 이름을 달면
    # 다른 축에서 엉뚱하게 읽힌다. 룰 집합을 이름에 넣는 이유는 `F_pbr_no_r1r3r4`
    # 같은 키가 "무엇이 빠졌나"를 이름으로 말해 주지 않기 때문이다.
    # `F_pbr_r6` 만 두 축(pbr_rules · ranking_signal)에 든다. 그래서 **양쪽에서 맞는
    # 이름**을 쓴다 — 룰 축에서는 "R3·R4 를 되돌린 것", 랭킹 축에서는 "RIM 쪽 짝과
    # 룰이 같은 1/PBR" 이 요점이라 둘 다 필요하다.
    'F_pbr_r6':           '1/PBR · {R1~R6} 전부',
    'F_pbr_no_r1r3r4':    '{R2,R5,R6}  − R1',
    'F_pbr_no_r2r3r4':    '{R1,R5,R6}  − R2',
    'F_pbr_no_r3r4r6':    '{R1,R2,R5}  − R6',
    'F_pbr_no_r1r2r3r4':  '{R5,R6}  − R1·R2 (사다리)',
    'F_pbr_r6only':       '{R6} 만 (사다리)',
    'F_pbr_nostab':       '{} 안정성 없음 (사다리)',
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
    reading=(
        '**필터를 쌓는다고 성적이 올라가지는 않는다.** 아래 증분표를 보면 기여가 몰려 '
        '있다 — 모멘텀과 RIM 랭킹이 대부분을 만들고, 팩터 스크리너는 붙일 때마다 '
        '성적을 깎는다.',
        '**Hard 필터는 수익률을 올리려고 넣은 것이 아니다.** A 에서 B 로 가며 거의 '
        '변하지 않거나 오히려 낮아지는데, 이 필터의 목적은 거래대금이 너무 적어 '
        '슬리피지로 현실성이 깨지는 종목을 빼는 것이다. 수익률로 판정할 대상이 아니다.',
        '**Stability 는 평균이 아니라 바닥을 들어올린다.** B 에서 C 로 갈 때 중앙값은 '
        '조금 오르지만 하위 5%(p5)가 크게 오른다. 이 필터의 값어치는 평균 수익이 아니라 '
        '**좌측 꼬리를 눌러 주는 것**에 있다 — 그래서 상방(p95)으로 판정하면 "기여 없음"이라는 '
        '반대 결론이 나온다.',
        '**스크리너는 두 번 다 손해였다.** D 에 붙였을 때(→E)도, 모멘텀 위에 붙였을 '
        '때(→G)도 성적이 내려간다. 2026-07-05 에 폐기된 이유가 이것이고, 현행 채택안에는 '
        '들어 있지 않다.',
    ),
    variable='필터 레이어를 A 에서 G 까지 하나씩 쌓는다 (Hard → Stability → 스크리너·모멘텀 '
             '→ RIM). H 는 G 에서 Stability 만 뺀 대조군으로 만들어진 것이다.',
    question='각 레이어가 성적에 얼마나 기여하는가? 특히 C 에서 D 로 가는 차이가 '
             'RIM 모델 자체의 기여를 보여준다.',
    failure_mode='"파이프라인 전체가 좋다"는 뭉뚱그린 결론을 막는다. 어느 레이어가 일하는지 '
                 '모른 채 전부 끌고 가면, 사족인 레이어가 회전율과 거래비용만 올리며 남는다.',
    deltas=(
        Delta('Hard 필터 추가', 'A_random', 'B_hard_random',
              '거래대금·상장기간 컷. 수익률이 아니라 현실성을 위한 필터다'),
        Delta('Stability 추가', 'B_hard_random', 'C_stability_random',
              '재무 하드룰 6개. 중앙값보다 p5(바닥)에서 더 크게 움직인다', axes=6),
        Delta('RIM 랭킹으로 선택', 'C_stability_random', 'D_rim_only',
              '같은 풀에서 무작위 추첨 대신 RIM 순위로 고른다 = RIM 모델의 기여. '
              '추첨 중앙값과 단일 실행을 견주는 것이라 값의 종류가 다르다',
              crosses_sets=True, axes=2),
        Delta('스크리너 추가', 'D_rim_only', 'E_screener_rim', '2026-07-05 폐기'),
        Delta('모멘텀 추가', 'D_rim_only', 'F_momentum_rim', '현행 경로가 물려받은 레이어'),
        Delta('스크리너 추가 (모멘텀 위에)', 'F_momentum_rim', 'G_full',
              '스크리너가 두 번째로 손해를 낸 자리'),
    ),
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
        '**H 를 Stability 대조군으로 쓰면 안 된다.** `H_no_stability` 는 Stability 를 끈 '
        '동시에 스크리너를 켜 놓았는데(`use_screener=True`), F 는 스크리너가 꺼져 있다. '
        '두 가지가 함께 달라져 있어서 **F 와 H 의 차이를 "Stability 의 기여"로 읽으면 '
        '틀린다.** (부록A §2, 지금도 코드가 그렇다)',
        '**A·B·C 와 D 이후는 값의 종류가 다르다.** 앞 셋은 무작위 추첨 500회의 **중앙값**이고 '
        '뒤는 **단일 결정적 실행**의 값이다. 그래서 화면에서 두 덩어리를 떼어 그린다. '
        'C 에서 D 로 가는 증분만은 의도적으로 이 경계를 넘는데, 같은 풀에서 "무작위로 골랐을 '
        '때"와 "모델로 골랐을 때"를 견주는 것이 곧 RIM 의 기여이기 때문이다.',
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
    next_step='여기서 두 갈래가 나왔다. **Stability 가 통째로 기여한다면 그중 어느 룰인가** — '
              'R6 단독 축이 그 질문을 받았다. 그리고 **스크리너가 두 번 다 손해였다면 순위를 '
              '무엇으로 매겨야 하나** — 랭킹 신호 분리 축이 그 질문을 받았고, 거기서 RIM 랭킹이 '
              '1/PBR 로 교체됐다.',
    next_axes=('r6_loo', 'ranking_signal'),
)

_WHY_R6 = WhyMap(
    reading=(
        '**R6 의 기여는 일정하지 않다. 세트에 따라 부호가 뒤집힌다.** 아래 증분표에서 '
        'C·D·F 는 켜는 쪽이 낫고 E·G 는 끄는 쪽이 낫다. 하나의 답("R6 은 좋다/나쁘다")을 '
        '내릴 수 있는 상태가 아니다.',
        '**뒤집히는 두 세트에는 공통점이 있다 — 팩터 스크리너가 켜져 있다.** E 와 G 가 '
        '`use_screener=True` 인 유일한 두 세트다. 당시 결론도 같았다: '
        '*"R6 는 D/F 파이프라인에서 유효하나 E 처럼 **중복 팩터** 존재 시 역효과 가능성 '
        '있음 — FactorScreener 가 이미 adjROE 를 포착"* (2026-06-22 §6-5).',
        '**겹치는 지점이 둘이다.** 스크리너 점수는 GPA(1/3) + 1/PBR(1/3) + 성장(1/3) 인데, '
        'GPA 는 R6 이 보는 **수익성**과, 1/PBR 은 RIM 이 보는 **저평가**와 겹친다. 즉 '
        'R6·스크리너·랭킹이 상당 부분 같은 종목을 집는다. `[확실하지 않은 사실]` 둘 중 '
        '어느 통로가 역효과를 만드는지는 **분리 측정된 적이 없다.**',
        '**그래서 이 축만으로는 "R6 을 켠다"는 결정이 나오지 않는다.** 남은 세트(C·D·F)에서 '
        'R6 이 도움이 된다는 방향만 일관될 뿐이고, 겹침이 확인된 이상 다음 질문은 '
        '**"순위를 만드는 신호가 무엇이어야 하나"** 로 넘어간다.',
    ),
    variable='R6(adjROE 가 요구수익률보다 낮으면 탈락)만 껐다 켠다. 나머지 구성은 세트 안에서 '
             '고정한다.',
    question='안정성 필터 안에서 R6 이 실제로 일하는가? 여섯 룰 가운데 주력인가?',
    failure_mode='"안정성 필터가 통째로 기여한다"는 뭉뚱그린 결론을 막는다. 어느 룰이 '
                 '일하는지 모르면 사족인 룰이 유니버스만 좁히며 남는다.',
    deltas=(
        Delta('C 세트 (랜덤)', 'C_no_r6', 'C_stability_random', '500회 추첨의 중앙값'),
        Delta('D 세트 (RIM)', 'D_no_r6', 'D_rim_only', ''),
        Delta('E 세트 (+스크리너)', 'E_no_r6', 'E_screener_rim', '스크리너 켜짐 — 부호가 뒤집힌다'),
        Delta('F 세트 (+모멘텀)', 'F_no_r6', 'F_momentum_rim', ''),
        Delta('G 세트 (전체)', 'G_no_r6', 'G_full', '스크리너 켜짐 — 부호가 뒤집힌다'),
    ),
    history=(
        '**2026-06-22** 파이프라인별로 R6 을 껐다 켜 본 첫 기록. 이상치 구간(2025-08 이후)을 '
        '뺀 기준으로 D 는 +1.2%p, F 는 +2.4%p 로 R6 이 도움이 됐지만 **E 에서는 역효과**였고 '
        'G 는 차이가 미미했다. 원인은 **중복 팩터** — 스크리너가 이미 adjROE 를 포착한다고 '
        '봤다. (`experiments/runs/2026.06.22. BACKTEST_RESULTS.md` §6-5)',
        '**2026-07-05** R6 을 단독 격리해 처음 쟀다 — 랜덤 유니버스 기준 중앙값 **+1.39%p**, '
        'p5 +0.93%p, p95 +0.90%p. 하방만 막는 게 아니라 **수익·하방 동시**로 움직여, '
        '필터 내부의 **주력**으로 판단했다. (부록A §1)',
        # 마크다운 이스케이프(`\~`)를 담으므로 raw 문자열이다. 물결표 두 개가 한 줄에
        # 있으면 그 사이가 취소선으로 렌더된다.
        r'같은 문서에서 **R1\~R5 합계**는 중앙값 +0.73%p · p5 +1.70%p 로 **하방 방어 편중**이었다. '
        r'다만 R1\~R5 **개별**은 그때까지 한 번도 격리된 적이 없었고, 그래서 `안정성 룰 개별` 축이 '
        '따로 만들어졌다.',
        '가설 `R6 > R1≈R4≈R5 > R2≈R3` 은 **불확실**로 남았다 — R6 격리치 외에는 측정된 적이 '
        '없다. (부록A G-4)',
        '**현행 채택안은 R6 을 유지한다** — `{R1,R2,R5,R6}`. 다만 그 결정은 이 축(RIM 경로)이 '
        '아니라 PBR 경로의 룰 조합 축에서 내려졌다.',
    ),
    warnings=(
        '**세트를 가로질러 비교하지 마라.** 쌍 안의 차이만이 R6 의 기여다. `C_no_r6` 와 '
        '`G_full` 을 견주면 스크리너·모멘텀까지 달라져 있다.',
        '**한 세트의 결과를 R6 의 결론으로 일반화하지 마라.** 위에서 봤듯 부호가 세트마다 '
        '뒤집히므로, 어느 한 줄만 인용하면 정반대 주장을 둘 다 만들 수 있다.',
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
        ('역효과가 GPA(수익성) 통로인지 1/PBR(저평가) 통로인지', '확실하지 않은 사실'),
        ('"G 는 차이 미미"라던 2026-06-22 관측 — 현행 산출물에서는 뚜렷하다', '확실하지 않은 사실'),
    ),
    sources=(_APPENDIX_A, 'docs/audit/GAPS.md',
             'experiments/runs/2026.06.22. BACKTEST_RESULTS.md'),
    next_step='R6 이 스크리너·랭킹과 같은 종목을 집는다면, 다음 질문은 **"순위를 만드는 '
              '신호가 무엇이어야 하나"** 다 — RIM(적정가 대비 상승여력) 인가 1/PBR(저평가) '
              '인가. 그 비교가 다음 축에서 이어지고, 거기서 **RIM 랭킹 근거 상실 → 1/PBR '
              '교체**라는 결론이 나왔다.',
    next_axes=('ranking_signal',),
)


_PIT_OFFICIAL = 'experiments/runs/2026.07.18._PIT_OFFICIAL.md'

_WHY_RANKING = WhyMap(
    reading=(
        '**결론을 만든 것은 순위표가 아니라 관문 하나다.** RIM 랭킹(D)이 "필터만 통과한 풀에서 '
        '무작위로 20종목 뽑기"의 상위 5%(C p95)조차 넘지 못한다 — **9.54% vs 12.13%, '
        '−2.59%p 미달.** 모델로 고른 것이 제비뽑기 잘된 경우만도 못하다는 뜻이라, '
        '다른 수치가 어떻든 랭킹 신호로서의 근거가 서지 않는다.',
        '**관문은 RIM 만 떨어뜨리는 게 아니다.** 짝이 맞는 무작위 분포에 대보면 '
        r'RIM 은 −2.44\~−2.59%p 로 확실히 미달이지만, **1/PBR 도 +0.05%p 로 겨우 닿는다**'
        '(11.2830% vs `C_no_r6` p95 11.2348%). 즉 ① 이 말해 주는 것은 "RIM 이 랜덤에 '
        '못 미친다"까지이고, **"그러므로 1/PBR 이 낫다"는 말해 주지 않는다.** 그건 아래 '
        'head-to-head 가 말한다.',
        '**채택안의 관문(G1) 수치를 여기 끌어와 비교하지 마라.** 채택안은 자기 짝 관문을 '
        '통과하지만(20.33% vs p95 15.61%), 그 관문은 **다른 눈금**이다 — 귀무분포가 '
        '모멘텀 통과 풀에서 n=13 으로 1,000회 뽑은 것이고, 이 축의 관문은 모멘텀 없는 '
        '풀에서 n=20 으로 500회 뽑은 것이다. 전략 쪽도 랭킹만이 아니라 모멘텀(MA200)·'
        '종목 수·룰 집합·컷이 전부 다르다. 두 마진(−2.59%p 와 +4.72%p)을 나란히 놓으면 '
        '**여섯 가지 차이의 합을 랭킹의 차이로 읽게 된다.**',
        '**모멘텀이 없는 경로에서는 1/PBR 이 크게 앞선다** (+2.49%p, R6 를 양쪽 다 끈 짝 기준). '
        '게다가 회전율이 낮아 비용을 뺀 net 에서 격차가 더 벌어진다 — 순위를 매기는 신호를 '
        '바꿨을 뿐인데 거래비용까지 같이 줄어드는 셈이다.',
        '**단, 모멘텀 경로에서는 RIM 이 근소하게 앞선다** (+0.13%p). 이 줄만 보고 결론을 '
        '뒤집으면 안 된다. 2026-07-30 재발행에서 `F_no_r6 > F_pbr_only` 가 FAIL→PASS 로 '
        '실제로 뒤집혔지만 **1차 관문이 그대로 실패라서 판정은 유지됐다.** 이 크기는 '
        '구간을 조금만 바꿔도 뒤집힌다.',
        '**아래 설정표를 먼저 보라 — 이 표의 비교는 다축이다.** 기본 태그들은 랭킹을 '
        'RIM 에서 1/PBR 로 바꾸면 **밸류에이션 컷(고평가 5% 초과 제외)이 함께 사라진다.** '
        '그래서 캘린더 민감도 작업이 이 대조를 단일축에서 **다축으로 강등**했다.',
        '**"랭킹만의 효과"는 다른 곳에 있다.** SPEC_14 가 컷을 독립 스위치로 빼고 '
        '랭킹×컷 **2×2**(`F_rimrank_no_r3r4`·`F_pbr_no_r3r4_rimcut` 포함)를 만들어 뒀고, '
        '그 값은 `experiments/calendar_sens/stage_b.json` 의 랭킹×컷 블록에 있다. '
        '**거기서 랭킹만의 효과는 컷 상태에 따라 부호가 뒤집힌다** — 컷을 끄면 1/PBR 이 '
        '앞서고(−0.023), 켜면 RIM 이 앞선다(+0.010). 즉 "어느 랭킹이 낫다"는 컷을 '
        '고정해야만 말할 수 있다. (그 블록은 기간이 달라 이 표와 직접 견주면 안 된다)',
        '**팩터 복합 랭킹은 비교 대상이 아니었다.** 1.60% 로 셋 중 압도적 꼴찌이고, '
        '이것이 팩터 스크리너를 폐기한 결정과 같은 방향의 증거다.',
    ),
    variable='**순위를 만드는 신호**만 바꾼다 — RIM 적정가 대비 상승여력 / 1/PBR(저평가) / '
             '팩터 복합 점수. 필터 구성은 그대로 두고 "무엇으로 줄을 세우나"만 교체한다.',
    question='편입 종목의 순위를 무엇으로 매겨야 하는가? RIM 모델은 그 값을 할 만한가?',
    failure_mode='"RIM 은 저PBR 의 재포장이 아니라 별개의 신호다"라는 믿음을 검증 없이 '
                 '끌고 가는 것을 막는다. 그 명제는 검증 결과 **룩어헤드의 산물**이었다.',
    # **R6 를 양쪽 다 끈 짝**으로만 비교한다. `D_rim_only`(R6 켬)와 `D_pbr_only`(R6 끔)를
    # 그냥 빼면 랭킹·R6·밸류에이션 컷 셋이 한꺼번에 달라진 값을 "랭킹의 차이"로 읽게 된다.
    # SPEC_13 §9-9 가 쓴 짝도 `D_no_r6` vs `D_pbr_only`, `F_no_r6` vs `F_pbr_only` 다.
    deltas=(
        # 넷 다 **2축이다** — 랭킹을 바꾸면 밸류에이션 컷이 함께 사라진다(위 `reading`
        # 참조). 그 사실이 산문에만 있어서 기계는 단일축으로 읽고 있었다.
        Delta('① 모멘텀 없음 (R6 둘 다 끔)', 'D_no_r6', 'D_pbr_only',
              '2026-07-18 에 PBR +2.26%p. 회전율도 함께 내려간다', axes=2),
        Delta('② 모멘텀 있음 (R6 둘 다 끔)', 'F_no_r6', 'F_pbr_only',
              '07-18 엔 PBR +1.89%p 였다 — 재발행 뒤 **RIM 쪽으로 뒤집힘**', axes=2),
        Delta(r'③ 룰 R1\~R6 동일', 'F_momentum_rim', 'F_pbr_r6',
              '07-18 엔 PBR +1.09%p 였다 — 재발행 뒤 **RIM 쪽으로 뒤집힘**', axes=2),
        Delta('④ R3·R4 제거', 'F_no_r3r4', 'F_pbr_no_r3r4',
              '07-18 엔 PBR +2.37%p. 방향은 유지, 격차는 줄었다', axes=2),
        Delta('R6 를 켜면 (RIM 경로)', 'D_no_r6', 'D_rim_only',
              '위 쌍들이 R6 를 맞춰 둔 이유 — 이만큼이 랭킹과 무관하게 움직인다'),
    ),
    history=(
        r'**2026-07-16** 강건성 분석에서 교훈을 하나 세웠다 — **0.4\~2%p 차이는 구간 의존일 '
        '수 있다.** 이후 판정은 이 눈금으로 읽는다.',
        '**2026-07-18 "RIM 랭킹은 현 형태로는 채택 근거 상실"** 로 판정했다. 근거는 두 겹이다: '
        '① 유효성 관문 `D ≥ C_p95` 미달 ② 전 구성에서 PBR 열위. 단일 구간 노이즈로 설명되지 '
        '않는 이유는 **3쌍이 일관되게 같은 방향이었고 관문 미달까지 겹쳤기** 때문이다. '
        f'(`{_PIT_OFFICIAL}` §4)',
        '같은 문서가 **"RIM 은 저PBR 재포장이 아니다"라는 명제는 룩어헤드 산물이었다**고 적었다. '
        '그리고 **RIM 의 잔존 가치는 R6(adjROE < r) 스크린 산식뿐이며 PBR 경로에서도 쓸 수 '
        '있다**고 정리했다 — 실제로 현행 채택안이 R6 을 그대로 쓴다.',
        '**2026-07-30 TTM 재발행 후 재확인.** 판정 10건이 뒤집혔고 그중 `F_no_r6 > F_pbr_only` '
        '가 FAIL→PASS 였다(모멘텀 경로에서 RIM 이 +0.13%p 앞섬). **그럼에도 1차 관문은 여전히 '
        'FAIL** 이고 모멘텀 없는 경로의 격차도 그대로여서 **결론은 유지**됐다. (SPEC_13 §9-9)',
        '**그래서 지금 ②는 절반만 남았다.** 07-18 에 PBR 이 이겼던 네 쌍 중 **둘이 뒤집혔다** — '
        '모멘텀 경로의 `F_no_r6 vs F_pbr_only` 와 `F vs F_pbr_r6` 다. 나머지 둘(모멘텀 없는 '
        '경로, R3·R4 제거 경로)은 방향이 유지된다. **온전히 남은 근거는 ① 관문 하나**이고, '
        '이것이 SPEC_13 이 "1차 관문은 유지된다"를 굳이 따로 적은 이유다.',
        '**중위값 분할 분석에서 또 한 번 확인됐다** — "RIM 은 포트폴리오 안에서 아무것도 '
        '구분하지 못한다". 성적이 낮은 게 아니라 **종목 간 우열을 가리지 못한다**는 뜻이다. '
        '(`docs/검토/f_pbr_ma200_median_split.md`)',
        '**RIM 폐기 자체는 전략 정체성 변경이라 사용자 결정 사항으로 남겨졌다.** 이 축은 '
        '랭킹 신호에서 RIM 을 뺐을 뿐, RIM 산식 전체를 버린 것이 아니다.',
    ),
    warnings=(
        '**모멘텀 경로의 한 줄만 뽑아 인용하지 마라.** 거기서는 RIM 이 앞서지만 그 크기가 '
        r'2026-07-16 에 세운 눈금(0.4\~2%p 는 구간 의존)보다도 작고, 1차 관문 실패를 뒤집지 '
        '못한다. 실제로 재발행 때 이 쌍의 판정이 뒤집혔는데도 결론은 그대로였다.',
        '**짝이 맞는 무작위 분포가 없는 조합에는 관문을 묻지 마라.** 예컨대 '
        '`D_pbr_no_r3r4`(룰 {R1,R2,R5,R6})에 맞는 랜덤 분포는 없다 — `C_stability_random` '
        '은 전 6룰로 거른 **다른 유니버스**라 그 p95 에 대보면 "랭킹의 기여"와 "유니버스가 '
        '달라서"가 섞인다. SPEC_10 이 채택안을 위해 `C_pbr_path_random` 을 새로 만든 이유가 '
        '이것이다. 어느 태그에 짝이 있는지는 `docs/TAG_MATRIX.md` 의 **짝 대조군** 열에 있다.',
        '**gross 만 보면 격차를 과소평가한다.** 1/PBR 은 회전율이 눈에 띄게 낮아 net 에서 '
        '더 벌어진다. 랭킹 신호를 고르는 일이 곧 거래비용을 고르는 일이기도 하다.',
        '**"RIM 폐기"가 아니라 "RIM 랭킹 폐기"다.** RIM 산식은 R6(adjROE < 요구수익률) '
        '스크린으로 현행 채택안에 그대로 남아 있다.',
        '이 축의 수치는 **RIM 경로 계보**로 산출된 것이다. 현행 채택안(1/PBR + 모멘텀 + n=13)의 '
        '성적표는 다른 축에 있다.',
    ),
    understanding=(
        ('관문 `D ≥ C_p95` 미달 (9.54% vs 12.13%)', '검증된 사실'),
        ('모멘텀 없는 경로에서 1/PBR 우위', '검증된 사실'),
        ('②(전 구성 PBR 열위)가 4쌍 중 2쌍에서 뒤집혀 더는 성립하지 않는다', '검증된 사실'),
        ('1/PBR 도 같은 관문을 +0.05%p 로 겨우 넘는다 (RIM 은 −2.44%p 미달)', '검증된 사실'),
        ('랭킹만의 효과가 컷 상태에 따라 부호가 뒤집힌다 (stage_b 2×2)', '검증된 사실'),
        ('채택안 G1 과 이 축의 관문은 귀무분포가 달라 마진을 비교할 수 없다', '검증된 사실'),
        ('1/PBR 의 회전율이 더 낮다', '검증된 사실'),
        ('모멘텀 경로에서는 RIM 이 근소 우위', '검증된 사실'),
        ('"RIM ≠ 저PBR 재포장"이 룩어헤드 산물이었다는 판정', '검증된 사실'),
        (r'근소 우위가 구간 의존이라는 해석 (0.4\~2%p 눈금)', 'Claude 의견'),
        ('RIM 산식 자체(ω·요구수익률)가 옳은지', '확실하지 않은 사실'),
        ('RIM 이 다른 필터 조합에서는 살아날 수 있는지', '확실하지 않은 사실'),
    ),
    sources=(_PIT_OFFICIAL, 'docs/검토/f_pbr_ma200_median_split.md',
             'docs/설계/SPEC_13_rebalance_calendar_v0.7.md'),
    next_step='랭킹이 1/PBR 로 정해지자 질문이 둘로 갈라졌다. **그 위에 어떤 안정성 룰을 '
              '얹을 것인가**(PBR 경로 룰 조합 축)와 **어떤 모멘텀 기준을 쓸 것인가**'
              '(모멘텀 그리드 축). 현행 채택안 `{R1,R2,R5,R6}` + MA200 이 그 둘의 답이다.',
    next_axes=('pbr_rules', 'momentum_grid'),
)


_TTM_REISSUE = 'experiments/runs/2026.07.30._TTM_REISSUE_OFFICIAL.md'

# raw 문자열이다 — 물결표 두 개가 한 줄에 있으면 Streamlit 이 그 사이를 취소선으로
# 긋는다. 본문의 `\~` 는 마크다운 이스케이프라 파이썬이 건드리면 안 된다.
_WHY_PBR_RULES = WhyMap(
    reading=(
        r'**채택안은 이 표의 1등이 아니다.** `{R1,R5,R6}`(R2 를 뺀 것)이 15.93% 로 '
        r'채택안 15.82% 를 **+0.11%p** 앞선다. 그런데 그 우위는 21개 리밸런싱 중 '
        r'**단 한 구간(2025-08-20)의 종목 한 개 교체**에서 전부 나오고, MDD·Sharpe·'
        r'Robustness 는 소수점까지 같다. SPEC_13 §9-6 "max 선택 금지" 에 따라 '
        r'교체하지 않았다.',
        r'**낙폭에서는 채택안이 유일한 최저다.** 다른 룰 조합은 전부 −35.5% \~ −40.2% '
        r'구간인데 채택안만 **−31.83%** 다(R2 를 뺀 쌍둥이와 동률). CAGR 열만 보고 '
        r'순위를 매기면 이 축이 실제로 고른 것이 무엇인지 안 보인다.',
        r'**룰의 기여는 더할 수 없다.** R6 를 아무것도 없는 위에 혼자 켜면 오히려 '
        r'**해롭고**(`{}` 14.46% → `{R6}` 14.11%, −0.35%p), 같은 R6 를 `{R1,R2,R5}` '
        r'위에 얹으면 **+1.20%p** 다. 하나씩 뺀 값들을 합산해 "룰별 기여"라 부르면 틀린다.',
        r'**R1 과 R2 는 부분 대체재다.** R1 이 있으면 R2 는 있으나 마나(−0.11%p)이고, '
        r'R1 이 없으면 R2 가 **+0.92%p** 로 강하게 일한다. 그래서 "R2 를 빼도 되나"는 '
        r'R2 만 보고 답할 수 없다.',
        r'**PBR 경로에서 안정성 레이어는 명확한 순기여다** — 통째로 끄면 −1.36%p 에 '
        r'MDD 가 −40.2% 로 최악이다. RIM 경로에서 관측된 순감 반전'
        r'(`F_no_stability_clean` > `F`)은 여기 적용되지 않는다.',
        r'**R5 칸이 이 표에 없다.** 채택 룰 넷 중 R5 만 단독 대조가 늦게 만들어져'
        r'(`F_pbr_no_r3r4r5`), 구간 지표 산출물이 아직 없다. 값은 아래 **다른 데 있는 '
        r'태그** 표의 캘린더 민감도 contrast 로만 존재한다 — R5 를 빼면 net 로그연율 '
        r'−0.96%p 지만 CI95 가 0 을 포함한다.',
        r'**캘린더를 바꿔도 이 축의 결론은 확정되지 않는다.** 단일축 contrast 5개'
        r'(R1·R2·R5·R6·모멘텀)가 **전부** `neutral_or_inconclusive` 라, 방향 일치율의 '
        r'분모가 0 이다. "캘린더가 룰 결론을 뒤집지 않았다"가 아니라 **뒤집혔는지 잴 수 '
        r'없었다**는 뜻이다.',
    ),
    variable='1/PBR 랭킹·모멘텀·HardFilter 를 고정한 채 **안정성 룰 집합만** 바꾼다. '
             'R3·R4 는 전 조합에서 빠져 있고(유해로 판정), 바뀌는 것은 R1·R2·R5·R6 의 '
             '조합이다.',
    question='현행 채택안의 안정성 룰 `{R1,R2,R5,R6}` 은 왜 이 넷인가? 하나 빼거나 '
             '전부 끄면 얼마가 달라지나?',
    failure_mode='"안정성 룰이 많을수록 안전하다"와 그 반대인 "룰은 유니버스만 좁힌다"를 '
                 '둘 다 막는다. 실제 사다리는 단조가 아니다 — 룰을 더 빼면 더 나빠지다가 '
                 '전부 빼면 다시 올라간다.',
    deltas=(
        Delta('R1 제거', 'F_pbr_no_r3r4', 'F_pbr_no_r1r3r4',
              '단일축. R1 이 이 조합의 지지 역할을 한다'),
        Delta('R2 제거', 'F_pbr_no_r3r4', 'F_pbr_no_r2r3r4',
              '단일축. **오르는데도 채택하지 않았다** — 우위 전부가 2025-08-20 한 구간의 '
              '종목 1개 교체(363280 → 021050)에서 나온다'),
        Delta('R6 제거', 'F_pbr_no_r3r4', 'F_pbr_no_r3r4r6', '단일축'),
        Delta('R3·R4 복원', 'F_pbr_no_r3r4', 'F_pbr_r6',
              '**2축이다** — 함께 폐기된 쌍이라 한 번에 되돌린다. 어느 쪽이 유해한지는 '
              '이 값으로 못 가른다 (SPEC_14 는 `C_R3R4` 를 다축으로 분류한다)',
              crosses_sets=True, axes=2),
        Delta('안정성 전체 제거', 'F_pbr_no_r3r4', 'F_pbr_nostab',
              '**다축**. 네 룰이 한꺼번에 빠진다 — 레이어의 총 기여이지 어느 룰의 기여도 '
              '아니다', crosses_sets=True, axes=4),
        Delta('R1 이 없을 때의 R2', 'F_pbr_no_r1r3r4', 'F_pbr_no_r1r2r3r4',
              'R1·R2 가 부분 대체재라는 근거. 위의 "R2 제거"(−0.11%p)와 부호가 반대다. '
              '**사다리 셀이라 판정에는 쓰지 않는다**', crosses_sets=True),
        Delta('아무것도 없는 위에 R6 만', 'F_pbr_nostab', 'F_pbr_r6only',
              'R6 를 혼자 켜면 해롭다 — 룰 기여가 덧셈이 아니라는 증거'),
    ),
    history=(
        '**2026-07-18 PBR 경로 안정성 분해.** 미검증 셀 둘(`F_pbr_nostab`·`F_pbr_r6only`)을 '
        '채워 사다리를 완성하고, `{R1,R2,R5,R6}` 을 채택 후보로 확정했다. 당시 근거는 '
        '**"CAGR·net·Sharpe·MDD 전 지표 최적"** 이었다. 같은 문서가 **R3·R4 만이 유해**'
        f'하다고 적었다(복원 시 −1.28%p). (`{_PIT_OFFICIAL}` §2-1)',
        '**2026-07-30 TTM 재발행에서 그 근거의 절반이 무너졌다.** `{R1,R5,R6}` 이 CAGR·net '
        '양쪽에서 채택안을 앞질러 **"전 지표 최적"은 더 이상 사실이 아니다.** 그런데도 '
        '교체하지 않았다 — 우위가 1구간·1종목에서 전부 나오고 리스크 지표가 동일해서다. '
        f'(`{_TTM_REISSUE}` §7)',
        '같은 재발행이 **R2 멤버십을 다시 쟀다.** 바뀐 구간 수는 1/21 로 같았지만 '
        '**교체된 종목이 완전히 달라졌다**(021050↔092230 → 363280→021050). 옛 수치를 '
        '재검증 없이 인용했으면 존재하지 않는 종목 쌍을 근거로 댈 뻔했다.',
        '**R1 사다리는 진단 전용이다.** 사용자 요청으로 **결과를 본 뒤** 추가한 셀이라 '
        '채택 후보가 될 수 없다고 그때 명시됐고, SPEC_14 도 2축 이상 동시 변경이라는 '
        '이유로 판정에서 뺐다. (SPEC_14 §6-3·§7-4)',
        '**2026-08-06 판정 contrast 를 실행 전에 못박다가 구멍이 드러났다.** 채택 룰 넷 중 '
        'R5 만 단독 대조가 아예 없어서 `F_pbr_no_r3r4r5` 를 새로 만들었다. '
        '(SPEC_14 §6-3, §12 N5)',
        '**2026-08-10 캘린더 민감도 B단계 — 전부 불확정.** 단일축 5개가 모두 '
        '`neutral_or_inconclusive` 로 나와 판정은 `NO_AUTOMATIC_ACTION` 이다. 룰이 '
        '견고하다는 증거도, 흔들린다는 증거도 나오지 않았다.',
        '**2026-08-11–12 현행 채택안 확정.** 이 룰 집합을 **고정한 채** 모멘텀을 '
        'MA200 으로, 종목 수를 13 으로 바꾼 것이 지금의 운영 기준이다. 룰 집합 자체는 '
        '그 두 변경 뒤에 재검토된 적이 없다.',
    ),
    warnings=(
        '**이 축의 어느 행에도 p95 를 대지 마라.** 룰을 바꾸면 유니버스가 바뀌는데, '
        '조건이 맞는 무작위 분포는 채택안 구성(`{R1,R2,R5,R6}` + 모멘텀)에만 있다'
        '(`C_pbr_path_random`). `docs/TAG_MATRIX.md` 의 **짝 대조군** 열이 이 축의 '
        '태그 전부에 `없음` 이라고 적혀 있는 이유다.',
        '**사다리 셋(`{R5,R6}`·`{R6}`·`{}`)으로 판정하지 마라.** 2축 이상이 동시에 '
        '달라지는 탐색 셀이고, 그중 R1 사다리는 **결과를 본 뒤 추가**됐다. 방향을 '
        '읽는 용도이지 후보를 고르는 용도가 아니다.',
        '**CAGR 순서로 읽으면 이 축이 실제로 고른 것을 놓친다.** 1등(`{R1,R5,R6}`)과 '
        '채택안의 차이는 0.11%p 인데, 2026-07-16 에 세운 눈금은 '
        r'**0.4\~2%p 차이는 구간 의존**이다. 그 눈금 아래의 격차다.',
        '**이 축의 수치는 모멘텀 MA 20/60 · 종목 수 20 계보다.** 현행 채택안은 MA200 · '
        'n=13 이라 성적표가 다르다 (`F_pbr_no_r3r4` 15.82% vs `F_pbr_ma200_n13` 20.33%). '
        '여기 값을 현행 성적으로 인용하면 틀린다.',
    ),
    understanding=(
        (r'`{R1,R5,R6}` 이 CAGR·net 에서 채택안을 앞선다 (+0.11%p)', '검증된 사실'),
        ('그 우위가 1구간(2025-08-20)·종목 1개에서 전부 나온다', '검증된 사실'),
        ('채택안이 MDD 최저다 (R2 제거 쌍둥이와 동률)', '검증된 사실'),
        ('R1·R2 가 부분 대체재다 (R1 유무로 R2 기여의 부호가 갈린다)', '검증된 사실'),
        ('R6 는 혼자 켜면 해롭고 R1·R2·R5 위에서는 도움이 된다', '검증된 사실'),
        ('PBR 경로에서 안정성 레이어가 순기여다 (RIM 경로와 반대)', '검증된 사실'),
        ('캘린더 민감도 단일축 5개가 전부 불확정이다', '검증된 사실'),
        ('R5 단독 기여 — 구간 지표 산출물이 없어 이 표로는 못 잰다', '확실하지 않은 사실'),
        ('룰끼리 상호작용하는 통로 (어느 종목군에서 겹치나)', '확실하지 않은 사실'),
        ('R2 를 유지한 것이 옳은지 — 격차가 노이즈 눈금 아래라 어느 쪽도 증명 안 됐다',
         'Claude 의견'),
        ('MA200·n=13 으로 바꾼 뒤에도 이 룰 집합이 최적인지', '확실하지 않은 사실'),
    ),
    sources=(_PIT_OFFICIAL, _TTM_REISSUE, _SPEC14,
             'experiments/calendar_sens/stage_b.json',
             'experiments/analysis/rule_membership.json'),
    next_step='룰 집합이 정해지자 질문이 둘로 갈라졌다. **이 조합이 같은 유니버스의 무작위 '
              '추첨을 이기는가**(채택 후보 대조군 축 — G1·G2 는 통과했고 G5 낙폭에서 '
              '떨어졌다) 와 **캘린더를 바꿔도 이 룰 결론이 유지되는가**(캘린더 민감도 축 — '
              '전부 불확정으로 나왔다).',
    next_axes=('benchmarks', 'calendar_bootstrap'),
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
        # 이 축은 기준별 **성적**만 보여줬다. 성적이 신호에서 온 것인지 유니버스가
        # 좁아져서 온 것인지는 커버리지를 봐야 갈리는데, 그 진단 26개가 화면 밖에
        # 있었다 (도달범위 측정, 2026-08-16).
        paths=('experiments/momentum_criteria/*_diagnostics_summary.json',
               'experiments/momentum_criteria/MC0_manifest.json',
               'experiments/momentum_criteria/MC0_manifest.md'),
        renderer='momentum_coverage',
        status=Status('ADOPTED', 'MA200 채택 — 현행 운영 기준', '2026-08-11', _SPEC14)),

    SeriesSpec(
        id='pbr_rules', title='PBR-경로 안정성 룰 조합', kind='A',
        changes='1/PBR 랭킹 위에서 R 룰 조합을 바꾼다',
        baseline='F_pbr_no_r3r4',
        tags=('F_pbr_no_r3r4', 'F_pbr_no_r1r2r3r4', 'F_pbr_no_r1r3r4',
              'F_pbr_no_r2r3r4', 'F_pbr_no_r3r4r6', 'F_pbr_nostab',
              'F_pbr_r6only', 'F_pbr_r6'),
        # 세 덩어리로 나눈다. **판정에 쓸 수 있는 값인지가 세트를 가른다.**
        # ① 현행안과 룰 하나만 다른 단일축 대조 ② R3·R4 를 함께 되돌린 2축
        # ③ 2축 이상이 동시에 달라지는 사다리(SPEC_14 §7-4 탐색 셀, 판정 비사용).
        # 섞어 놓으면 사후에 추가된 사다리 셀이 단일축 대조와 같은 무게로 읽힌다.
        groups=(('F_pbr_no_r3r4', 'F_pbr_no_r1r3r4', 'F_pbr_no_r2r3r4',
                 'F_pbr_no_r3r4r6'),
                ('F_pbr_r6',),
                ('F_pbr_no_r1r2r3r4', 'F_pbr_r6only', 'F_pbr_nostab')),
        # 채택 룰 넷 중 **R5 만** 단독 대조가 늦게 만들어져 구간 지표 산출물이 없다.
        # 배정하지 않으면 화면에 R1·R2·R6 칸만 있고 R5 칸은 존재 자체가 안 보인다.
        elsewhere=(Elsewhere(
            'F_pbr_no_r3r4r5',
            where=('experiments/calendar_sens/stage_b.json',
                   'experiments/calendar_sens/stage_b_tables.md',
                   'experiments/runs/2026.08.10._CALENDAR_SENS_B.md'),
            why='SPEC_14 §6-3 이 판정 contrast 를 못박다가 "R5 단독 대조가 없다"를 발견해 '
                '`[확정 2026-08-06]` 로 새로 만든 태그다. 캘린더 민감도 B단계 하네스에서만 '
                '굴렸고 `run_ablation` 은 아직 태우지 않았다 — 그래서 이 축의 비교표에 '
                '행이 없다.',
            note='`C_R5` — R5 를 빼면 net 로그연율 **−0.96%p** 지만 CI95 '
                 '[−2.00%p, +0.05%p] 가 0 을 포함해 `neutral_or_inconclusive` 다. '
                 '**반기 gross 참고값이 없다**(`semiannual_gross_ref_full_period: null`) — '
                 '다른 행의 CAGR 과 같은 눈금이 아니므로 표에 끼워 넣어 읽지 마라.'),),
        why=_WHY_PBR_RULES,
        status=Status('ADOPTED', '{R1,R2,R5,R6} 채택', '2026-08', _SPEC10)),

    SeriesSpec(
        id='ranking_signal', title='랭킹 신호 분리', kind='A',
        changes='RIM vs 1/PBR vs 팩터 — 무엇이 순위를 만드나',
        baseline='D_rim_only',
        # `_parent` 는 랭킹 자체를 바꾼다 — PBR 분모가 자본총계가 아니라 지배기업
        # 소유주지분이다 (rank_mode='pbr_parent', SPEC_11 §3). 이름만 보면 "부모 실행"
        # 으로 오독하기 쉬워 미배정으로 남아 있었다.
        # 2026-07-18 판정의 근거였던 **head-to-head 쌍 4개를 전부** 싣는다. 그중 둘
        # (`F_pbr_r6`·`F_no_r3r4`/`F_pbr_no_r3r4`)은 다른 축에만 있어서, 이 축만 보면
        # "전 구성 PBR 열위"라는 근거의 절반이 화면에 없었다 (2026-08-15 발견).
        tags=('D_rim_only', 'D_no_r6', 'D_pbr_only',
              'F_no_r6', 'F_pbr_only',
              'F_momentum_rim', 'F_pbr_r6',
              'F_no_r3r4', 'F_pbr_no_r3r4',
              'D_pbr_no_r3r4', 'D_factor_only', 'F_pbr_no_r3r4_parent'),
        # 모멘텀 유무로 나눈다. 두 덩어리에서 RIM 과 1/PBR 의 우열이 **반대로** 나오므로,
        # 섞어 놓으면 어느 비교가 어느 조건이었는지 눈으로 못 가린다.
        # **세트 = head-to-head 쌍.** 짝을 붙여 놓아야 "무엇과 무엇을 견준 것인가"가
        # 그림에서 읽힌다. 쌍마다 랭킹만 다르고 안정성 룰은 같게 맞춰져 있다.
        groups=(('D_rim_only', 'D_no_r6', 'D_pbr_only'),
                ('F_no_r6', 'F_pbr_only'),
                ('F_momentum_rim', 'F_pbr_r6'),
                ('F_no_r3r4', 'F_pbr_no_r3r4'),
                ('D_pbr_no_r3r4', 'D_factor_only', 'F_pbr_no_r3r4_parent')),
        # 이 축의 표는 랭킹과 밸류에이션 컷이 함께 움직이는 **다축 비교**다. 컷을
        # 독립 스위치로 뺀 2×2 의 나머지 두 칸이 이 둘인데, 캘린더 민감도 B단계가
        # 자기 하네스 안에서만 돌려 ablation 산출물이 없다. 배정하지 않으면
        # "랭킹만의 효과는 다른 곳에 있다"는 왜-지도 문장이 가리키는 태그가
        # 화면 어디에도 없게 된다.
        elsewhere=(
            Elsewhere(
                'F_rimrank_no_r3r4',
                where=('experiments/calendar_sens/stage_b.json',
                       'experiments/calendar_sens/stage_b_tables.md',
                       'experiments/runs/2026.08.10._CALENDAR_SENS_B.md'),
                why='SPEC_14 §14-1 랭킹×컷 2×2 전용으로 만든 태그라 캘린더 민감도 B단계 '
                    '하네스(`scripts/calendar_sens/stage_b.py`)에서만 굴렸다. '
                    '`run_ablation` 을 태운 적이 없어 구간 지표 산출물이 없다.',
                note='`contrasts_rank_cut_2x2` 의 `C_RANK_NOCUT` — **컷을 끈 상태의 랭킹 '
                     '효과.** net 로그연율 −2.31%p(1/PBR 이 앞섬), CI95 는 0 을 포함해 '
                     '`neutral_or_inconclusive` 다.'),
            Elsewhere(
                'F_pbr_no_r3r4_rimcut',
                where=('experiments/calendar_sens/stage_b.json',
                       'experiments/calendar_sens/stage_b_tables.md',
                       'experiments/runs/2026.08.10._CALENDAR_SENS_B.md'),
                why='위와 같다 — 2×2 의 나머지 한 칸이라 B단계 하네스에서만 산출됐다.',
                note='`C_RIMCUT`(랭킹 고정·컷만 켬) 의 변량이자 `C_RANK_CUT`(컷 켠 상태의 '
                     '랭킹 효과) 의 **기준**이다. 컷을 켜면 랭킹의 부호가 뒤집혀 RIM 이 '
                     '+0.95%p 앞선다 — "어느 랭킹이 낫다"를 컷을 고정하지 않고 말할 수 '
                     '없는 이유가 이 한 쌍이다.'),
        ),
        why=_WHY_RANKING,
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
        # G1 의 귀무분포다. `run_random_pool.py` fast-path 가 뽑으므로 ablation
        # 산출물이 없고, 그래서 이 축의 **판정 근거인데 화면에 없었다.**
        # 둘을 나란히 싣는다 — 현행 관문(MA200 풀)과 그 전에 쓰던 풀(MA 20/60).
        # 옛것을 지우면 "왜 판정이 바뀌었나"를 화면에서 답할 수 없다.
        elsewhere=(
            Elsewhere(
                'C_pbr_ma200_random',
                where=('experiments/robustness/random_summary_C_pbr_ma200_random_n13.json',
                       'experiments/robustness/C_pbr_ma200_random_n13_draws.csv',
                       'experiments/robustness/gate_results_F_pbr_ma200_n13.json'),
                why='무작위 추첨 1,000회를 `scripts/robustness/run_random_pool.py` fast-path 가 '
                    '돌려 `experiments/robustness/` 로 뽑는다. `run_ablation` 을 타지 '
                    '않으므로 `experiments/ablation/` 에는 원래 없다.',
                note='**현행 G1 의 귀무분포.** 채택안 설정에서 파생돼(랭킹만 무작위 추첨으로 '
                     '교체) HARD·룰·모멘텀 기준이 전부 같다 — 그래서 이 관문이 재는 것이 '
                     '**랭킹의 기여**다. 2026-08-15 재발행 산출.'),
            Elsewhere(
                'C_pbr_path_random',
                where=('experiments/robustness/random_summary_n13.json',
                       'experiments/robustness/C_pbr_path_random_n13_draws.csv',
                       'experiments/robustness/random_summary.json',
                       'experiments/robustness/C_pbr_path_random_draws.csv',
                       'experiments/robustness/gate_results.json'),
                why='위와 같은 fast-path 산출물이다. **더 이상 채택안의 관문이 아니다** — '
                    '레거시 MA 20/60 풀이라 조상 `F_pbr_no_r3r4` 계보에만 짝이 맞는다.',
                note='**세 벌이 이름을 나눠 쓴다.** `_n13` 없는 것은 n=20(p95 14.15%), '
                     '`_n13` 은 n=13 이지만 **풀이 MA 20/60** 이다(p95 15.61%) — '
                     '`pools.json`(07-29)과 `pools_n13.json`(08-12)이 md5 동일인 것이 그 '
                     '증거다. 2026-08-15 이전 G1 은 MA200 전략을 이 분포에 대고 있었다. '
                     '판정에 쓸 값은 `gate_results_*.json` 의 `draws_tag` 가 가리키는 '
                     '쪽 하나뿐이다.'),
        ),
        paths=('experiments/robustness/random_summary_C_pbr_ma200_random_n13.json',
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
        # status 는 "두 후보 FAIL" 이라 적어 두고 **그 판정을 만든 산출물을 아무 데서도
        # 가리키지 않았다** (도달범위 측정, 2026-08-16). 결론만 있고 근거가 화면 밖이면
        # 나중에 "왜 FAIL 이었더라"를 문서에서 다시 찾아야 하고, 그 사이 근거가 낡아도
        # 아무도 모른다 — G1 귀무분포가 실제로 그렇게 낡았다.
        paths=('experiments/robustness/qg_results_[AC].json',
               'experiments/robustness/random_summary_[AC].json',
               'experiments/robustness/pools_[AC].json',
               'experiments/robustness/*_[AC]_draws.csv',
               'experiments/robustness/*_[AC]_periods.csv.gz',
               'experiments/robustness/*_[AC]_contrib.csv.gz'),
        renderer='calendar_gates',
        status=_CLOSED('두 후보 FAIL (QG1·QG3·QG5) — 캘린더 축 종결',
                       '2026-08-10', _SPEC13)),

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
        id='daily_nav', title='일별 NAV — 낙폭·G5 판정', kind='B',
        changes='구간 지표를 일별 경로로 재구성한다 (측정 빈도 × 비용)',
        # **G5 FAIL 이 여기서 나온다.** 채택 보류의 유일한 사유이고 Sharpe·MDD 의
        # SSOT 인데(SPEC_13 §9-1) 축이 없어 36개 파일이 화면 밖에 있었다 — CANONICAL
        # 배너가 값만 띄웠다. 판정 근거가 화면에 없으면 낡아도 아무도 모른다
        # (`C_pbr_path_random` 이 G1 귀무분포인데 안 보이던 것과 같은 상황).
        # `_daily_positions.csv` 는 **개발 PC 에 없어서** 처음엔 빠뜨렸다. 도달범위
        # 래칫이 서버(원본 트리)에서 잡아 줬다 — 부분 사본만 보고 축을 정의하면
        # 이렇게 계열 하나가 통째로 샌다.
        paths=('experiments/daily_nav/summary.json',
               'experiments/daily_nav/summary_[AC].json',
               'experiments/daily_nav/benchmarks_daily.csv',
               'experiments/daily_nav/*_daily_nav.csv',
               'experiments/daily_nav/*_daily_positions.csv',
               'experiments/daily_nav/*_reconciliation.csv'),
        renderer='daily_nav',
        status=Status('CLOSED_FAIL', 'G5 FAIL — 일별 net MDD −58.12% (한계 −45%)',
                      '2026-08-15', _SPEC10)),

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

def claimed_keys(spec: SeriesSpec, available: list[str]) -> list[str]:
    """이 축이 주장하는 키 — 명시 배정 + 패턴 후보 − 제외.

    **배정 규칙의 단일 정의다.** `resolve()` 는 산출물 카탈로그를, 태그 매트릭스는
    `ABLATION_CONFIGS` 를 넘긴다. 이걸 안 나눠 두면 매트릭스가 명시 태그만 세어
    패턴으로 붙은 축(모멘텀 그리드 23개)을 통째로 "미배정"이라 보고한다 — 실제로
    그렇게 틀렸다 (2026-08-15).
    """
    keys = list(spec.tags)
    for pat in spec.patterns:
        keys += [k for k in available if fnmatch.fnmatchcase(k, pat)]
    for pat in spec.exclude:
        keys = [k for k in keys if not fnmatch.fnmatchcase(k, pat)]
    # 산출물이 다른 데 있는 태그도 **배정된 것**이다. 빼면 태그 매트릭스가 다시
    # `미배정` 이라 부르고, 판정 근거인 태그가 잊힌 것처럼 남는다.
    return keys + [e.tag for e in spec.elsewhere]


def resolve(spec: SeriesSpec, catalog: ArtifactCatalog | None = None) -> Series:
    """스펙 + 카탈로그 → 실제 멤버. 없는 키는 **버리지 않고 `missing` 으로 보고한다.**"""
    catalog = catalog if catalog is not None else build_catalog()
    keys = claimed_keys(spec, catalog.keys())
    # 산출물이 다른 데 있다고 등록 대장이 이미 말한 태그는 **없는 게 아니다.**
    # `missing` 에 넣으면 화면이 빨간 오류를 영구히 띄우고, 그러면 진짜 유실이
    # 났을 때 아무도 그 줄을 안 읽는다.
    elsewhere = {e.tag for e in spec.elsewhere}

    seen: set[str] = set()
    members, missing = [], []
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        if k in catalog:
            members.append(k)
        elif k not in elsewhere:
            missing.append(k)

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
    """어느 축에도 안 들어간 **ablation 산출물**. 조용히 사라지게 두지 않는다.

    이 함수는 `experiments/ablation/` 만 본다 — 카탈로그가 거기만 훑기 때문이다.
    나머지 계열(일별 NAV·robustness·momentum_criteria·runs)은 `unreachable_files()`
    가 센다. 둘을 나눈 이유: 여기는 **태그**가 단위이고 저기는 **파일**이 단위다.
    """
    catalog = catalog if catalog is not None else build_catalog()
    used = {m.artifact_key for s in resolve_all(catalog) for m in s.members}
    return sorted(k for k in catalog.keys() if k not in used)


#: 산출물이 아닌 파일. 도달범위 계산에서 뺀다 (메타·자리표시자).
_META_FILES = ('.gitkeep', 'README.md', 'ARTIFACTS_MANIFEST.json')

#: **도달 불가로 알고 있는 계열** — 사유와 날짜를 함께 적는다.
#:
#: 억제 목록이 아니다. 검사가 두 방향으로 지킨다: ① 이 밖에서 새 파일이 나오면
#: 깨진다(새 사각지대) ② 아무 파일도 안 걸리는 패턴이 남으면 깨진다(해소됐으니 지워라).
#: `dashboard/claims.KNOWN` 과 같은 자기만료 구조다.
UNCOVERED: dict[str, str] = {
    'experiments/robustness/pools*.json': (
        '추첨 풀 스냅샷 — 감사·재현용. `momentum_decomposition`·`preferred_scan` 이 '
        '읽지만 화면에는 없다 (2026-08-16).'),
    'experiments/robustness/*.csv.gz': (
        '추첨 구간·기여도 원본(대용량, git 미추적). `gate_analysis` 가 G3′·G4′ 에 '
        '쓰지만 화면은 요약만 띄운다 (2026-08-16).'),
    # ── 날짜·실행 접미사가 붙은 **옛 벌** ────────────────────────────────
    # 래칫이 처음 돌 때 서버에서 잡은 것들이다(개발 PC 엔 없다 — 미추적 대용량).
    # 이 계열이 위험한 이유는 이미 겪었다: 같은 태그의 벌이 여럿이면 어느 것이 현행인지
    # 이름만으로 안 갈린다 (`pools.json` 과 `pools_n13.json` 이 md5 동일이었던 건).
    'experiments/ablation/*_periods_*.csv': (
        '손으로 남긴 **실행별 구간 대조본** (`_0715run`·`_run3_presweep`). '
        '2026-07-15 재실행 오염 사고(DRIFT-INGEST-001) 규명 때의 증거물이고 정규 '
        '부속(`{키}_periods.csv`)이 아니다. 지우지 말 것 — 그 사고의 유일한 기록이다 '
        '(2026-08-16).'),
    'experiments/robustness/*_draws_[0-9]*.csv': (
        '**폐기된 벌**의 추첨 원본 (`_20260720`). 현행은 접미사 없는 쪽이다. '
        '남겨 두는 이유는 판정이 왜 바뀌었는지 되짚을 수 있어야 해서다 (2026-08-16).'),
    'experiments/runs/*': (
        '실행 리포트 아카이브 — **렌더할 산출물이 아니라 문서다.** 축이 인용할 때만 '
        '도달한다(왜-지도 `sources`·B형 `paths`). 전부를 화면에 걸 대상은 아니고, '
        '여기서 진짜 위험은 "화면에 안 뜬다"가 아니라 **철회된 수치를 누가 다시 '
        '인용하는 것**이다. 그쪽은 도달범위가 아니라 '
        '`test_every_run_report_is_cited_or_marked_superseded` 가 막는다 — 인용되지도 '
        '않고 대체 표시도 없는 리포트를 금지한다 (2026-08-16).'),
}


def unreachable_files(root: Path | None = None) -> list[str]:
    """등록 대장에서 **화면으로 도달할 수 없는** 산출물 파일.

    `unassigned()` 는 `experiments/ablation/` 만 훑는다. 그래서 나머지 다섯 계열은
    구조적으로 안 세어졌다 — 매트릭스가 설정만 세던 것과 **같은 모양의 사각지대**다.
    2026-08-16 에 처음 재 보니 223개 중 **93개**가 도달 불가였고, 그중에는 G5 판정의
    SSOT 인 일별 NAV 36개와 캘린더 A/C 관문 산출물 6개가 들어 있었다.

    "도달 불가"는 "안 쓰인다"가 아니다. 대부분은 코드가 읽는데 **화면에 없을** 뿐이고,
    그게 위험한 이유는 `C_pbr_path_random` 이 G1 귀무분포인데 안 보이던 것과 같다 —
    판정의 근거가 화면 밖에 있으면 아무도 그게 낡았는지 모른다.
    """
    import glob as _glob

    root = root or Path(__file__).resolve().parent.parent
    exp = root / 'experiments'
    if not exp.is_dir():
        return []

    reachable: set[Path] = set()
    for spec in SERIES:
        for pattern in spec.paths:
            reachable |= {Path(p).resolve() for p in _glob.glob(str(root / pattern))}
        for e in spec.elsewhere:
            reachable |= {(root / w).resolve() for w in e.where if (root / w).exists()}
        # 왜-지도의 **근거 문서도 등록 대장의 포인터다.** 처음 재 볼 때 이걸 빼먹어서
        # `2026.07.18._PIT_OFFICIAL.md` 같은 인용 문서가 "도달 불가"로 잡혔다.
        if spec.why:
            reachable |= {(root / s).resolve() for s in spec.why.sources
                          if (root / s).exists()}
        if (root / spec.status.source).exists():
            reachable.add((root / spec.status.source).resolve())

    catalog = build_catalog()
    for art in catalog:
        if art.path:
            reachable.add(art.path.resolve())
        reachable |= {p.resolve() for p in art.sidecars.values()}
    # 묶음 요약은 태그가 아니라 카탈로그의 재료다 (`build_catalog` 가 읽는다).
    reachable |= {p.resolve() for p in (exp / 'ablation').glob('summary*.json')}

    return sorted(
        p.relative_to(root).as_posix()
        for p in exp.rglob('*')
        if p.is_file() and p.name not in _META_FILES and p.resolve() not in reachable)
