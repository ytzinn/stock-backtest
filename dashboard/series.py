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
    periods_per_year: int | None = 2   # None = 축마다 다름. 구간 수는 산출물에서 읽는다
    paths: tuple[str, ...] = ()     # B형 원본 (repo 상대 glob)
    renderer: str | None = None     # B형 전용 뷰 키
    notes: str = ''                 # 축을 처음 보는 사람에게 필요한 배경 (마크다운)


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


# ── 정본 인벤토리 — 16축 ────────────────────────────────────────────────────

SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec(
        id='layers', title='레이어 ablation', kind='A',
        changes='필터 레이어를 A~H 로 하나씩 쌓는다',
        baseline='D_rim_only',
        tags=('A_random', 'B_hard_random', 'C_stability_random', 'D_rim_only',
              'E_screener_rim', 'F_momentum_rim', 'G_full', 'H_no_stability'),
        notes=_LAYER_NOTES,
        status=Status('ARCHIVED', 'RIM 경로 — 랭킹 폐기로 계보 종료', '2026-07', _SPEC05)),

    SeriesSpec(
        id='r6_loo', title='R6 단독 (LOO)', kind='A',
        changes='R6(adjROE < r) 만 껐다 켠다',
        baseline='D_rim_only',
        tags=('C_stability_random', 'C_no_r6', 'D_rim_only', 'D_no_r6',
              'E_screener_rim', 'E_no_r6', 'F_momentum_rim', 'F_no_r6',
              'G_full', 'G_no_r6'),
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

    # baseline 을 맨 앞으로. 비교표에서 기준이 첫 행이어야 눈이 덜 미끄러진다.
    if spec.baseline in seen:
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
