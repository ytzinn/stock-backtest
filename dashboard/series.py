"""SERIES 매니페스트 — "이 축으로 무엇을 비교하나?"

카탈로그(`dashboard/artifacts.py`)가 "무엇이 존재하는가"를 답하면, 여기는 **어떤 축으로
무엇을 나란히 놓는가**를 답한다. 둘을 나눈 이유가 있다.

## 태그→시리즈는 1:N 이다 (분류가 아니라 membership)

한 태그가 여러 축의 baseline 으로 재사용된다. `F_pbr_no_r3r4` 는 PBR 룰 조합·캘린더·
랭킹 분해의 공통 baseline 이고, `D_rim_only` 는 레이어와 LOO 양쪽에 들어간다. "파일을
시리즈로 분류"하면 한 태그를 한 곳에만 넣게 되어 표현이 불가능해진다.

## 매니페스트가 배정을 소유한다

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


@dataclass(frozen=True)
class Status:
    """판정·종결 상태. 문자열 하나로 두면 "종결만 보기" 같은 필터가 불가능하다."""

    code: str          # CLOSED_PASS | CLOSED_FAIL | EXPLORING | ADOPTED | ARCHIVED
    label: str
    as_of: str
    source: str


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


@dataclass(frozen=True)
class Series:
    spec: SeriesSpec
    members: tuple[ScenarioRef, ...]
    missing: tuple[str, ...] = field(default=())   # 매니페스트엔 있는데 산출물이 없는 키

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


# ── 정본 인벤토리 — 16축 ────────────────────────────────────────────────────

SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec(
        id='layers', title='레이어 ablation', kind='A',
        changes='필터 레이어를 A~H 로 하나씩 쌓는다',
        baseline='D_rim_only',
        tags=('A_random', 'B_hard_random', 'C_stability_random', 'D_rim_only',
              'E_screener_rim', 'F_momentum_rim', 'G_full', 'H_no_stability'),
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
        tags=('D_rim_only', 'D_pbr_only', 'D_factor_only', 'D_pbr_no_r3r4',
              'F_momentum_rim', 'F_pbr_only'),
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
        paths=('experiments/analysis/*.json', 'experiments/live/dryrun/manifest.yaml'),
        renderer='live_decomposition',
        status=Status('EXPLORING', '라이브 전환 준비 중', '2026-08-10',
                      'docs/설계/SPEC_11_decomposition_live_manifest.md')),

    SeriesSpec(
        id='n_stocks', title='포트폴리오 종목 수 민감도', kind='A',
        changes='종목 수 k (10/12/13/20)',
        baseline='F_pbr_ma200_n13',
        patterns=('F_pbr_ma200_n*',),
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
                  members=tuple(_split_variant(ScenarioRef.from_key(k)) for k in members),
                  missing=tuple(missing))


def resolve_all(catalog: ArtifactCatalog | None = None) -> list[Series]:
    catalog = catalog if catalog is not None else build_catalog()
    return [resolve(s, catalog) for s in SERIES]


def unassigned(catalog: ArtifactCatalog | None = None) -> list[str]:
    """어느 축에도 안 들어간 산출물. 조용히 사라지게 두지 않는다."""
    catalog = catalog if catalog is not None else build_catalog()
    used = {m.artifact_key for s in resolve_all(catalog) for m in s.members}
    return sorted(k for k in catalog.keys() if k not in used)
