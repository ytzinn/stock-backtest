"""태그 대장 — "이 태그는 **왜** 만들어졌나".

## 조건과 이유를 나눠 둔 이유

`docs/TAG_MATRIX.md` 는 **무엇이 켜져 있나**를 답한다. 그건 코드에서 기계적으로 나온다.
반면 **왜 이 태그를 만들었나**는 코드에 안 적혀 있다 — 사람이 쓸 수밖에 없다.

그 사람이 쓴 내용을 JSON 에 두면 검사가 닿지 않아 썩는다. 실제로 그랬다:
`tests/baselines/SCENARIO_REGISTRY.json` 은 2026-07-12 에 33개로 만들어졌고, 태그가
72개가 될 때까지 **39개가 빠진 걸 아무도 몰랐다.** 그래서 여기(코드)로 옮겼다 —
등록 대장이 축 설명을 소유하는 것과 같은 방식이고, 생성기가 매트릭스로 렌더한다.

## 무엇을 적고 무엇을 안 적나

**열이 이미 말해 주는 것은 적지 않는다.** `F_pbr_ma200` 에 "MA200 모멘텀"이라고 쓰면
매트릭스의 `모멘텀` 열과 중복이고, 중복한 설명은 갈라진다. 축이 설명해 주는 태그
(모멘텀 그리드의 한 칸, PBR 룰 조합의 한 칸)도 마찬가지다 — 축 설명이 답한다.

여기 적는 것은 **축과 조건만으로는 알 수 없는 것**이다: 왜 이 조합을 굳이 만들었나,
어떤 판정의 어느 자리에 쓰였나, 인용할 때 무엇을 조심해야 하나.
"""
from __future__ import annotations

#: 감사 시절 4분류. `tests/baselines/SCENARIO_REGISTRY.json`(2026-07-12) 에서 옮겼다.
CLASSES = ('CANONICAL', 'DIAGNOSTIC', 'ARCHIVE', 'RANDOM')

#: 태그 → (분류, 왜 만들어졌나).
#:
#: 2026-07-12 감사 산출물 33개를 그대로 옮기고(전사 오류를 막으려고 스크립트로 추출),
#: 2026-08-15 에 축·조건만으로 설명되지 않는 10개를 문서에서 찾아 채웠다.
TAG_NOTES: dict[str, tuple[str, str]] = {
    # ── 2026-08-15 추가 — 축·조건이 설명하지 못하는 태그 ──────────────────
    'C_pbr_path_random': (
        'RANDOM',
        'SPEC_10 §3-1 이 **채택 후보 전용으로 새로 만든 짝 대조군.** 기존 '
        '`C_stability_random` 은 룰이 전 6개인 데다 모멘텀을 안 태워서, 채택안'
        '(룰 {R1,R2,R5,R6} + 모멘텀)의 관문으로 쓰면 "유니버스가 좁아서"와 "랭킹이 '
        '좋아서"가 섞인다. 그래서 **모멘텀 통과 풀에서 무작위 20종목**을 1,000회 뽑는다 '
        '(p95 추정 안정화). G1 관문의 귀무분포가 이것이다.'),
    'U_pbr_path_ew': (
        'DIAGNOSTIC',
        'SPEC_10 §3-2 — **적격 유니버스 전체를 동일가중**으로 담은 대조군. '
        '"종목을 고른 것"이 아니라 "이 유니버스에 그냥 다 넣었을 때"를 재는 기준선이라, '
        'G2(net 초과) 판정의 상대가 된다. 구간 승률 비교의 3종 기준(KOSPI·KOSDAQ·이것) '
        '중 하나이기도 하다.'),
    'F_rimrank_no_r3r4': (
        'DIAGNOSTIC',
        'SPEC_14 §14-1 **랭킹×컷 2×2 의 한 칸** — RIM 랭킹인데 밸류에이션 컷은 끈 설정. '
        '기본 태그들에서는 랭킹을 바꾸면 컷이 함께 따라 움직여 "랭킹만의 효과"를 잴 수 '
        '없어서, 컷을 독립 스위치로 빼고 만든 신규 태그다. `C_RANK_NOCUT`(컷 끈 상태의 '
        '랭킹 효과)의 변량 쪽.'),
    'F_pbr_no_r3r4_rimcut': (
        'DIAGNOSTIC',
        'SPEC_14 §14-1 **랭킹×컷 2×2 의 한 칸** — 1/PBR 랭킹에 밸류에이션 컷을 켠 설정. '
        '현행안(`F_pbr_no_r3r4`, 컷 없음)에 컷만 더한 것이라 `C_RIMCUT`(랭킹 고정, 컷 '
        '효과)의 변량이고, `C_RANK_CUT`(컷 켠 상태의 랭킹 효과)의 기준이기도 하다.'),
    'F_pbr_no_r3r4r5': (
        'DIAGNOSTIC',
        'SPEC_14 캘린더 민감도의 `C_R5` contrast 를 만들려고 **새로 생성한 태그** '
        '(룰 {R1,R2,R6}). 현행안에서 R5 만 더 뺀 구성이며, 그 전에는 R5 단독 대조가 '
        '아예 존재하지 않아 contrast 를 구성할 수 없었다.'),
    'F_pbr_no_r3r4': (
        'DIAGNOSTIC',
        '**PBR 경로의 공통 기준선.** 룰 조합·캘린더·랭킹 분해·모멘텀 그리드가 전부 이 '
        '태그를 baseline 으로 쓴다. 2026-07-18 에 채택 후보로 지목됐고, 이후 모멘텀 '
        '기준을 MA200 으로 바꾸고 종목 수를 13 으로 줄인 것이 현행 채택안이다 — '
        '즉 **현행안의 직계 조상이지 현행안이 아니다.**'),
    'F_pbr_only': (
        'DIAGNOSTIC',
        'RIM 랭킹 자리에 1/PBR 만 넣은 모멘텀 경로 대조군. 2026-07-18 판정의 '
        'head-to-head 쌍 `F_no_r6 vs F_pbr_only` 의 한쪽이다 — 그 쌍은 **양쪽 다 R6 가 '
        '꺼져 있어** 랭킹만 견줄 수 있게 맞춰져 있다.'),
    'F_pbr_r6': (
        'DIAGNOSTIC',
        '1/PBR 랭킹에 안정성 룰을 **R1~R6 전부** 켠 설정. `F_momentum_rim`(RIM, 같은 '
        '전 6룰)과 룰이 정확히 같아, 2026-07-18 판정에서 "R1~R6 동일조건" head-to-head '
        '쌍으로 쓰였다.'),
    'D_pbr_no_r3r4': (
        'DIAGNOSTIC',
        '모멘텀을 뺀 1/PBR 경로 — 현행 룰 {R1,R2,R5,R6} 을 유지한 채 모멘텀만 없앤 '
        '구성이다. 랭킹 신호 분리에서 "모멘텀 없는 층"의 PBR 쪽 값을 준다.'),
    'F_pbr_no_r3r4_parent': (
        'DIAGNOSTIC',
        'PBR 분모를 자본총계가 아니라 **지배기업소유주지분**으로 바꾼 랭킹 변형 '
        '(`rank_mode=pbr_parent`, SPEC_11 §3). 이름의 `_parent` 를 "부모 실행"으로 '
        '오독하기 쉬워 한동안 어느 축에도 배정되지 않은 채 남아 있었다.'),

    # ── 2026-07-12 감사 산출물에서 이관 ───────────────────────────────────
    # `F_no_r2r3` 는 **그때의** 채택안이었다. 지금은 아니다 (RIM 랭킹 경로가 폐기됐고
    # 현행은 1/PBR + MA200 + n=13). 분류를 손으로 `CANONICAL` 이라 적어 뒀더니
    # 채택안이 두 번 바뀌는 동안 아무도 못 고쳐서, 매트릭스가 **폐기된 RIM 태그를
    # 현행 채택안이라고** 띄우고 있었다 (2026-08-15 사용자 발견). 그래서 이제
    # `class_of` 가 채택 태그를 SSOT 에서 읽는다 — 여기 손으로 적지 않는다.
    'F_no_r2r3': ('ARCHIVE',
     '**2026-07 시점의 채택 파이프라인**이었다 (RIM 랭킹 경로). 현행 채택안은 1/PBR + MA200 + n=13 이라 '
     '계보가 다르다 — 여기 수치를 현행 성적으로 인용하지 마라. '
     'phase2_rim.py:55 주석은 ’채택 파이프라인 F_momentum_rim 구조’라고 적혀 있으나 이는 오기(誤記)다. '
     'F_momentum_rim 태그는 stability_rules 키가 없어 StabilityFilter 기본값(_ALL_RULES = '
     'R1~R6, R2/R3 포함)으로 빌드되므로 실제 프로덕션 설정과 다르다. 프로덕션과 필터 구성이 정확히 일치하는 태그는 '
     'F_no_r2r3 뿐이었다. GAPS.md DOC-ABL-002 참조. '
     ),
    # ── DIAGNOSTIC ────────────────────────────────────────
    'D_factor_only': ('DIAGNOSTIC',
     'RIM 없이 FactorScreener 4팩터 합산 점수로 직접 랭킹 — 신호분리 대조군 (스크리너 자체는 폐기됐으나 진단 목적 '
     '보존). '
     ),
    'D_no_r1': ('DIAGNOSTIC',
     'R1 단독 leave-one-out. '
     ),
    'D_no_r2': ('DIAGNOSTIC',
     'R2 단독 leave-one-out — R2 폐기 결정의 근거. '
     ),
    'D_no_r3': ('DIAGNOSTIC',
     'R3 단독 leave-one-out — R3 폐기 결정의 근거. '
     ),
    'D_no_r4': ('DIAGNOSTIC',
     'R4 단독 leave-one-out. '
     ),
    'D_no_r5': ('DIAGNOSTIC',
     'R5 단독 leave-one-out. '
     ),
    'D_no_r6': ('DIAGNOSTIC',
     'R6(가치파괴 구간 제외) 단독 leave-one-out. '
     ),
    'D_no_stability': ('DIAGNOSTIC',
     'SPEC_05 부록A — StabilityFilter 완전 제거 대조군 (D 계열, 스크리너 없음). '
     ),
    'D_pbr_only': ('DIAGNOSTIC',
     'RIM 업사이드 랭킹 대신 1/PBR 랭킹 — RIM 알파가 저PBR 재포장인지 신호분리 검증. '
     ),
    'D_rim_only': ('DIAGNOSTIC',
     'RIM 유효성(D>C) 판정용 핵심 대조군. 스크리너/모멘텀 없이 Hard+Stability(전체 6룰 기본)+RIM만. '
     ),
    'F_momentum_rim': ('DIAGNOSTIC',
     '모멘텀 기여도(F>D) 판정용. 단, stability_rules 미지정 → 기본값(R1~R6 전체)이라 '
     'CANONICAL(R1,R4,R5,R6)과 필터 구성이 다르다. GAPS.md DOC-ABL-002 참조. '
     ),
    'F_no_r2': ('DIAGNOSTIC',
     'F 계열에서 R2 단독 제외. '
     ),
    'F_no_r2r3r4': ('DIAGNOSTIC',
     'F 계열에서 R2+R3+R4 동시 제외 (조합 확인용, 채택안 아님). '
     ),
    'F_no_r2r4': ('DIAGNOSTIC',
     'F 계열에서 R2+R4 동시 제외 (조합 확인용, 채택안 아님). '
     ),
    'F_no_r3': ('DIAGNOSTIC',
     'F 계열에서 R3 단독 제외. '
     ),
    'F_no_r3r4': ('DIAGNOSTIC',
     'F 계열에서 R3+R4 동시 제외 (조합 확인용, 채택안 아님). '
     ),
    'F_no_r4': ('DIAGNOSTIC',
     'F 계열에서 R4 단독 제외 (참고용, R4는 채택 유지 규칙). '
     ),
    'F_no_r6': ('DIAGNOSTIC',
     'F 계열에서 R6 제외 leave-one-out. '
     ),
    'F_no_stability_clean': ('DIAGNOSTIC',
     'SPEC_05 부록A — StabilityFilter 완전 제거 대조군 (F 계열, 스크리너 없음). '
     'H_no_stability(스크리너 포함으로 교란)의 정정판. '
     ),
    # ── ARCHIVE ────────────────────────────────────────
    'E_gpa_only': ('ARCHIVE',
     '폐기된 스크리너의 단일 팩터(gpa) 변형. '
     ),
    'E_no_r6': ('ARCHIVE',
     '폐기된 스크리너 경로의 R6 leave-one-out. '
     ),
    'E_op_only': ('ARCHIVE',
     '폐기된 스크리너의 단일 팩터(op_yoy) 변형. '
     ),
    'E_pbr_only': ('ARCHIVE',
     '폐기된 스크리너의 단일 팩터(inv_pbr) 변형. '
     ),
    'E_rev_only': ('ARCHIVE',
     '폐기된 스크리너의 단일 팩터(rev_yoy) 변형. '
     ),
    'E_screener_rim': ('ARCHIVE',
     'FactorScreener 폐기(2026-07-05, phase2_rim.py:7 주석). 원칙 5에 따라 삭제하지 않고 기록 보존. '
     ),
    'G_full': ('ARCHIVE',
     '스크리너+모멘텀+RIM 풀 파이프라인. 스크리너 폐기로 더 이상 채택 후보 아님. '
     ),
    'G_no_r6': ('ARCHIVE',
     'G_full의 R6 leave-one-out. 동일 사유로 ARCHIVE. '
     ),
    'H_no_stability': ('ARCHIVE',
     'SPEC_05 부록A 주석(backtest/ablation.py:72-74)에 명시: use_screener=True까지 같이 꺼져 '
     'stability·screener 두 축이 동시에 달라 교란됨. F_no_stability_clean/D_no_stability로 '
     '대체됨. '
     ),
    # ── RANDOM ────────────────────────────────────────
    'A_random': ('RANDOM',
     '무작위 20종목 선택, seed x rebalance_date 복합 시드, 500회 반복. 결과: A_random_dist.csv '
     '(experiments/ablation/, gitignore됨). '
     ),
    'B_hard_random': ('RANDOM',
     'HardFilter만 통과 후 무작위 선택, 500회 반복. B_hard_random_dist.csv. '
     ),
    'C_no_r6': ('RANDOM',
     '이름과 달리 코드상 RANDOM_TAGS에 포함(use_rim_filter=False, random_n=20) — '
     'Hard+Stability(R6 제외) 통과 후 무작위 선택. C_no_r6_dist.csv. '
     ),
    'C_stability_random': ('RANDOM',
     'Hard+Stability(기본 전체 6룰) 통과 후 무작위 선택, 500회 반복. '
     'C_stability_random_dist.csv. '
     ),
}

#: 이 축들에 속하면 태그별 설명이 없어도 된다 — **축 설명이 곧 그 태그의 이유**다.
#: (모멘텀 그리드의 한 칸, PBR 룰 조합의 한 칸에 "MA200"·"R1 제거"를 다시 적으면
#: 매트릭스의 열과 중복이고, 중복한 설명은 갈라진다.)
AXIS_EXPLAINS = ('momentum_grid', 'pbr_rules', 'stability_loo_d', 'stability_combo_f',
                 'screener_single', 'n_stocks')


def note_of(tag: str) -> str:
    return TAG_NOTES.get(tag, ('', ''))[1]


def adopted_tag() -> str:
    """현행 채택 태그. **SSOT 는 `scripts/live/freeze_rebalance.py::DEFAULT_TAG` 다.**

    손으로 적지 않는다. 실제로 그렇게 적어 뒀다가 채택안이 두 번 바뀌는 동안
    (RIM → 1/PBR, MA 20/60 → MA200, n 20 → 13) 아무도 못 고쳐서, 매트릭스가 폐기된
    RIM 태그 `F_no_r2r3` 를 `CANONICAL` 이라고 띄우고 현행 `F_pbr_ma200` 은 분류가
    비어 있었다. `docs/CANONICAL.md` 도 같은 함수에서 읽으므로 둘이 갈라질 수 없다.
    """
    from backtest.canonical_state import _freeze_constants

    return _freeze_constants()[0]


def class_of(tag: str) -> str:
    """태그 분류. `CANONICAL` 만은 대장이 아니라 **운영 설정에서** 나온다."""
    if tag == adopted_tag():
        return 'CANONICAL'
    return TAG_NOTES.get(tag, ('', ''))[0]
