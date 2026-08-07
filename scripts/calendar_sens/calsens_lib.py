"""
SPEC_14 공유 정의 — 사전등록 상수·contrast 레지스트리·연율 로그수익률·bootstrap.

**진단 전용.** 이 모듈이 만드는 어떤 수치도 태그 채택 근거가 될 수 없다 (SPEC_14 §9).

여기 있는 상수는 전부 **사전등록값**이다 (§12 N1~N5, `[확정 2026-08-06, 사용자]`).
수치 산출 후 수정 금지 — 바꾸려면 새 SPEC 번호를 열어라.

지표 산식 SSOT 준수 (CLAUDE.md 코드 정합성 규칙):
  - CAGR·MDD·Sharpe는 `backtest.metrics` 단일 정의를 import 한다. 여기서 재정의하지 않는다.
  - `g(·)`(연율 로그수익률)만 이 모듈 고유 정의다 — `compute_nav_cagr`와 **정확히
    대응**하도록 실제 캘린더 경과연수를 분모로 쓴다: `CAGR = exp(g) − 1` 항등
    (§12 N-g `[확정 2026-08-06, 사용자]`). 252 거래일 연율화는 채택하지 않았다.
  - 공통 기간 절단은 `metrics.slice_common_period` 재사용 — 복제 금지.

bootstrap (§10 사전등록):
  - 일별 **로그수익률** circular moving block, 블록 21거래일, 2,000회.
  - **전역 단일 RNG** `random.Random("SPEC14:CALENDAR_SENS:GLOBAL")`.
    반복 b 마다 block index를 **한 번만** 생성해 인컴번트·EW·전 variant·양 캘린더에
    **동일 적용**한다 (v0.2의 contrast별 seed 충돌 수정, §10).
  - 원자료로 저장하는 것은 **block 시작점 행렬**(B × n_blocks)이다. 확장 규칙
    (`idx = (start + arange(block)) % n` 이어붙인 뒤 n 절단)이 결정론이라 전체
    index 배열과 정보량이 동일하고, 19MB → 0.9MB로 줄어든다. 확장본의 sha256을
    함께 기록해 재현을 검증할 수 있게 한다.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.metrics import slice_common_period

# ── 경로 ─────────────────────────────────────────────────────────────────────

NAV_DIR = Path('experiments/daily_nav')
ABL_DIR = Path('experiments/ablation')
OUT_DIR = Path('experiments/calendar_sens')

# ── 공통 기간 (SPEC_13 §9-7 확정, 재계산 금지 — SPEC_14 §3) ───────────────────

COMMON_S = date(2016, 5, 18)
COMMON_E = date(2026, 4, 3)

# ── §0-4 인컴번트 baseline (전체기간 20구간 기준 — 공통 기간 값과 혼동 금지) ──

INCUMBENT_FULL_GROSS_CAGR = 0.15819563103474077   # engine metrics['cagr']
INCUMBENT_FULL_NET_CAGR   = 0.14079850522450377   # 일별 NAV 승법 net CAGR

# ── 사전등록 상수 (§12 N1~N4 `[확정 2026-08-06, 사용자]`) ─────────────────────

EPSILON              = 0.005   # N1 — net 연율 로그수익률 0.5%p (§7-2)
SIGN_PROB_THRESHOLD  = 0.90    # N4 — 부호 분류 확률 문턱 (§7-2)
Q1_LARGE_ABS         = 0.010   # N2 — |Δ_EW| ≥ 1.0%p (§8-1)
Q1_EQUIV_BOUND       = 0.005   # N2 — 90% CI ⊂ [−0.5%p, +0.5%p] (§8-1)
Q2M_ABS              = 0.010   # N3 — |δ(j)| ≥ 1.0%p (§8-2)

# §8-2 Q2-D — **절대 개수** 기준이다. C_RANK 강등(§6-3 정정)으로 단일축 분모가
# 6 → 5로 줄었지만, 원 사전등록이 비율이 아니라 개수였으므로 문턱은 그대로 둔다.
Q2D_REVERSAL_LARGE   = 2       # 명확한 방향 반전 ≥ 2개 → Q2-D 큼
Q2D_NEUTRAL_MAX      = 2       # 반전 0개 AND 중립·불확정 ≤ 2개 → Q2-D 작음

# §10 bootstrap
BLOCK_DAYS_PRIMARY   = 21
BLOCK_DAYS_SENSITIVITY = (10, 21, 63)   # §10-1 판정 비사용
N_RESAMPLES          = 2000
RNG_SEED             = 'SPEC14:CALENDAR_SENS:GLOBAL'
CI_MAIN              = (2.5, 97.5)      # 95%
CI_EQUIV             = (5.0, 95.0)      # 90% — Q1 equivalence 전용 (§8-1)

# ── 태그 ─────────────────────────────────────────────────────────────────────

INCUMBENT_TAG = 'F_pbr_no_r3r4'
EW_TAG        = 'U_pbr_path_ew'
PLUMBING_TAG  = 'F_pbr_ma_double_adapter'   # §6-4 배관 양성 대조군

CALENDARS = ('SEMIANNUAL', 'C')   # §6-1 — 안 A는 빈도 교락으로 미사용


@dataclass(frozen=True)
class Contrast:
    """(variant, baseline, 달라지는 축) — 태그 이름이 아니라 축으로 등록한다 (§6-3)."""
    contrast_id: str
    variant_tag: str
    composition:  str
    axis:         str
    n_axes:       int
    single_axis:  bool
    semi_gross_ref: float | None   # 반기 전체기간 gross 참고값 (§6-3, 판정 비사용)
    note:         str = ''
    # 'rule'     — 룰 contrast. **J1·J3 분모는 이 그룹의 단일축만** 쓴다 (§7-3).
    # 'rank_cut' — 랭킹×컷 2×2 (§14-1). 별도 블록으로 보고, J 분모 제외.
    group:        str = 'rule'
    # None = 인컴번트. 2×2 의 "컷 켠 상태에서 랭킹 비교"는 baseline 이 인컴번트가 아니다.
    baseline_tag: str | None = None

    @property
    def baseline(self) -> str:
        return self.baseline_tag or INCUMBENT_TAG


# §6-3 판정 contrast — **실행 전 확정, 변경 금지** (§9-4).
# baseline 은 전부 인컴번트 F_pbr_no_r3r4 {R1,R2,R5,R6} + PBR 랭킹 + 모멘텀.
#
# `[v0.3 → 구현 정정 2026-08-06]` **C_RANK 를 단일축에서 다축으로 강등**했다.
#   `F_no_r3r4` 는 `use_rim_filter=True` 라 `BacktestPipeline.score_and_rank` 를 타는데,
#   거기에는 랭킹 신호 교체 외에 ① `mktcap <= fv×(1+rim_threshold)` 밸류에이션 컷
#   ② FV 계산 불가 종목 제외 ③ `MIN_PORTFOLIO_STOCKS` 미달 시 고평가 보완이 함께
#   붙는다 (`_PBRRankPipeline` 에는 전부 없음). "랭킹 신호만 1축"이 아니다 —
#   SPEC_14 §6-2 가 `F_no_r6`·`F_pbr_only` 를 배제한 것과 **동일 유형의 오염**이다.
#   → 보조 표(다축)로 이동, J1·J3 분모에서 제외. 룰 단일축 분모는 6 → **5**.
#
# `[추가 2026-08-06, 사용자]` 그 대신 **랭킹 × 밸류에이션컷 2×2**(`group='rank_cut'`)를
#   신설해 "랭킹 신호 자체가 캘린더에 민감한가"에 답할 수 있게 했다. 네 칸:
#     (1/PBR, 컷없음) = 인컴번트 · (RIM, 컷없음) = F_rimrank_no_r3r4
#     (1/PBR, 컷있음) = F_pbr_no_r3r4_rimcut · (RIM, 컷있음) = F_no_r3r4
#   두 세트로 읽는다 — 컷 끈 상태의 랭킹 비교, 컷 켠 상태의 랭킹 비교.
#   **J1·J3 분모에는 넣지 않는다** — J 계열은 "어느 **룰**의 방향이 뒤집혔나"를 재는
#   지표라(§7-3), 랭킹·컷 축 3개를 같은 분모에 섞으면 룰 견고성 판정이 희석된다.
JUDGMENT_CONTRASTS: tuple[Contrast, ...] = (
    Contrast('C_R1',  'F_pbr_no_r1r3r4', '{R2,R5,R6}',  'R1 제거',      1, True,  0.145569),
    Contrast('C_R2',  'F_pbr_no_r2r3r4', '{R1,R5,R6}',  'R2 제거',      1, True,  0.159276),
    Contrast('C_R5',  'F_pbr_no_r3r4r5', '{R1,R2,R6}',  'R5 제거',      1, True,  None,
             '신규 태그 (§12 N5) — 반기 참고값 없음, B단계에서 함께 산출'),
    Contrast('C_R6',  'F_pbr_no_r3r4r6', '{R1,R2,R5}',  'R6 제거',      1, True,  0.146238),
    Contrast('C_MOM', 'D_pbr_no_r3r4',   '{R1,R2,R5,R6}, 모멘텀 off',
             '모멘텀만', 1, True, 0.107868, '§12 N6 [VERIFY] 해소 — 2026.07.30 재발행 §4'),
    Contrast('C_R3R4', 'F_pbr_r6',       '{R1~R6}',     'R3+R4 복원',   2, False, 0.145380,
             '동시 폐기된 쌍 — 어느 룰의 방향인지 분해 불가'),
    Contrast('C_STAB', 'F_pbr_nostab',   '{}',          '안정성 전체 제거', 4, False, 0.144557),
    Contrast('C_RANK', 'F_no_r3r4',      '{R1,R2,R5,R6} + RIM + 컷',
             '랭킹 + 밸류에이션컷 동시', 2, False, 0.144387,
             'v0.3 §6-3은 단일축 1로 등록했으나 pipeline.score_and_rank 의 밸류에이션 '
             '컷·보완로직이 함께 바뀐다 — 다축 강등 (사용자 확정). 2×2 의 결합 셀',
             group='rank_cut'),

    # ── 랭킹 × 밸류에이션컷 2×2 (§14-1) — J 분모 제외, 별도 블록 ────────────
    Contrast('C_RANK_NOCUT', 'F_rimrank_no_r3r4', '{R1,R2,R5,R6} + RIM, 컷 없음',
             '랭킹만 (컷 끈 상태)', 1, True, None,
             '세트1 — baseline 인컴번트(1/PBR, 컷 없음). 스코어 함수 하나만 다르다',
             group='rank_cut'),
    Contrast('C_RIMCUT', 'F_pbr_no_r3r4_rimcut', '{R1,R2,R5,R6} + 1/PBR + 컷',
             '밸류에이션컷만', 1, True, None,
             '세트 공통 — baseline 인컴번트. 랭킹은 그대로 두고 컷만 켠다',
             group='rank_cut'),
    Contrast('C_RANK_CUT', 'F_no_r3r4', '{R1,R2,R5,R6} + RIM + 컷',
             '랭킹만 (컷 켠 상태)', 1, True, 0.144387,
             '세트2 — baseline 이 인컴번트가 아니라 F_pbr_no_r3r4_rimcut 이다',
             group='rank_cut', baseline_tag='F_pbr_no_r3r4_rimcut'),
)

RULE_CONTRASTS     = tuple(c for c in JUDGMENT_CONTRASTS if c.group == 'rule')
RANKCUT_CONTRASTS  = tuple(c for c in JUDGMENT_CONTRASTS if c.group == 'rank_cut')
# J1·J3·Q2-D·Q2-M 의 분모 — **룰 그룹의 단일축만** (§7-3, §14-1)
SINGLE_AXIS_CONTRASTS = tuple(c for c in RULE_CONTRASTS if c.single_axis)
MULTI_AXIS_CONTRASTS  = tuple(c for c in RULE_CONTRASTS if not c.single_axis)

# 실행이 필요한 **고유 태그** — 캘린더별로 전부 필요하다. baseline 도 포함해야 한다
# (C_RANK_CUT 의 baseline 은 인컴번트가 아니다).
REQUIRED_TAGS: tuple[str, ...] = tuple(dict.fromkeys(
    (INCUMBENT_TAG, EW_TAG)
    + tuple(c.variant_tag for c in JUDGMENT_CONTRASTS)
    + tuple(c.baseline for c in JUDGMENT_CONTRASTS)
))

assert len(SINGLE_AXIS_CONTRASTS) == 5, '룰 단일축 contrast 수가 사전등록(5)과 다르다'
assert len(RULE_CONTRASTS) == 7, '룰 contrast 수가 사전등록(7)과 다르다'
assert len(RANKCUT_CONTRASTS) == 4, '랭킹×컷 2×2 셀 수가 4가 아니다'
assert len({c.contrast_id for c in JUDGMENT_CONTRASTS}) == len(JUDGMENT_CONTRASTS), \
    'contrast_id 중복'


# ── 태그 → 캘린더별 산출물 이름 ───────────────────────────────────────────────

def calendar_tag(tag: str, calendar: str) -> str:
    """캘린더별 실제 산출물 태그명. 반기는 무접미사 (`schedule.tag_suffix` 규약 동일)."""
    from backtest.configs.schedule import tag_suffix
    return f'{tag}{tag_suffix(calendar)}'


def nav_path(tag: str, calendar: str) -> Path:
    return NAV_DIR / f'{calendar_tag(tag, calendar)}_daily_nav.csv'


def load_nav(tag: str, calendar: str) -> pd.DataFrame:
    """일별 NAV (nav_gross·nav_net). 없으면 예외 — 조용한 기본값 금지."""
    path = nav_path(tag, calendar)
    if not path.exists():
        raise FileNotFoundError(
            f'{path} 없음 — `run_ablation --calendar {calendar} --tags {tag}` → '
            f'`export_portfolios --calendar {calendar} --tags {tag}` → '
            f'`run_daily_nav --calendar {calendar} --tags {calendar_tag(tag, calendar)}` 순으로 먼저 실행할 것'
        )
    df = pd.read_csv(path, index_col='date', parse_dates=True)
    for col in ('nav_gross', 'nav_net'):
        if col not in df.columns:
            raise ValueError(f'{path}: {col} 컬럼 없음')
    return df


# ── g(·) — 연율 로그수익률 (§7-1 + §12 N-g) ──────────────────────────────────

def common_period_years(start: date = COMMON_S, end: date = COMMON_E) -> float:
    """공통 기간의 실제 캘린더 경과연수 (365.25일 = 1년, compute_nav_cagr 관례 동일)."""
    return (end - start).days / 365.25


def log_returns(nav: pd.Series, start: date = COMMON_S, end: date = COMMON_E) -> pd.Series:
    """공통 기간 `(start, end]` 의 일별 로그수익률.

    `slice_common_period` 로 `[start, end]` 절단(시작일이 관측일에 없으면 예외) 후
    차분하므로, 반환 길이는 관측일 수 − 1 이고 인덱스는 `(start, end]` 이다.
    """
    sliced = slice_common_period(nav, start, end)
    return np.log(sliced).diff().dropna()


def annualized_log_return(logr: np.ndarray, years: float) -> float:
    """g(·) = Σ log(1+r_t) / years.

    원표본에서 이는 `compute_nav_cagr(sliced, initial_capital=NAV(S))` 와 정확히
    대응한다 — `CAGR = exp(g) − 1`. bootstrap 재표본은 길이가 원표본과 같으므로
    (§10 "N일에서 절단") 동일한 `years` 를 분모로 쓴다.
    """
    return float(np.sum(logr) / years)


def cagr_from_g(g: float) -> float:
    """g → CAGR (이해용 병기, §5 A-1). 판정은 g 로 한다."""
    return float(np.exp(g) - 1.0)


# ── bootstrap (§10) ──────────────────────────────────────────────────────────

def block_starts(
    n:            int,
    block:        int,
    n_resamples:  int = N_RESAMPLES,
    seed:         str = RNG_SEED,
) -> np.ndarray:
    """circular moving block 의 시작점 행렬 `(n_resamples, n_blocks)`.

    **전역 단일 RNG.** 호출자는 이 행렬 하나를 만들어 인컴번트·EW·전 variant·양
    캘린더에 **동일 적용**해야 한다 (§10 셀 간 동조). contrast 마다 다시 부르면
    v0.2 의 seed 충돌 결함이 되살아난다.

    circular 이므로 시작점은 `0..n-1` 전체에서 균등 추출한다 (비-circular 의
    `0..n-block` 제한은 양끝 관측의 표집 확률을 낮춘다).
    """
    if n < block:
        raise ValueError(f'표본 길이({n})가 블록 길이({block})보다 짧다')
    n_blocks = -(-n // block)          # ceil
    rng = random.Random(seed)
    starts = np.empty((n_resamples, n_blocks), dtype=np.int32)
    for b in range(n_resamples):
        for k in range(n_blocks):
            starts[b, k] = rng.randrange(n)
    return starts


def expand_starts(starts: np.ndarray, block: int, n: int) -> np.ndarray:
    """시작점 행렬 → 실제 index 행렬 `(n_resamples, n)` (circular, 마지막 n 에서 절단)."""
    offsets = np.arange(block, dtype=np.int64)
    idx = (starts[:, :, None].astype(np.int64) + offsets[None, None, :]) % n
    return idx.reshape(starts.shape[0], -1)[:, :n]


def index_digest(idx: np.ndarray) -> str:
    """확장된 block index 행렬의 sha256 — 재현 검증용 (§11)."""
    return hashlib.sha256(np.ascontiguousarray(idx, dtype=np.int32).tobytes()).hexdigest()


def bootstrap_g(series_matrix: np.ndarray, idx: np.ndarray, years: float) -> np.ndarray:
    """전 시리즈 × 전 반복의 g. 입력 `(n_series, n)` → 출력 `(n_series, n_resamples)`.

    모든 시리즈에 **같은 idx 행**을 적용하므로 paired 구조와 셀 간 상관이 보존된다.
    """
    n_series, n = series_matrix.shape
    n_resamples = idx.shape[0]
    out = np.empty((n_series, n_resamples), dtype=float)
    for b in range(n_resamples):
        out[:, b] = series_matrix[:, idx[b]].sum(axis=1) / years
    return out


def ci(samples: np.ndarray, bounds: tuple[float, float] = CI_MAIN) -> tuple[float, float]:
    lo, hi = np.percentile(samples, bounds)
    return float(lo), float(hi)


def excludes_zero(interval: tuple[float, float]) -> bool:
    lo, hi = interval
    return lo > 0.0 or hi < 0.0


def within(interval: tuple[float, float], bound: float) -> bool:
    """구간 전체가 [−bound, +bound] 안에 있는가 (equivalence 판정)."""
    lo, hi = interval
    return lo >= -bound and hi <= bound


# ── §7-2 부호 3분류 ──────────────────────────────────────────────────────────

SIGN_POSITIVE = 'clear_positive'
SIGN_NEGATIVE = 'clear_negative'
SIGN_NEUTRAL  = 'neutral_or_inconclusive'

DIR_HELD      = 'direction_held'
DIR_REVERSED  = 'clear_reversal'
DIR_NEUTRAL   = 'neutral_or_inconclusive'


def classify_sign(boot_e: np.ndarray, eps: float = EPSILON,
                  prob: float = SIGN_PROB_THRESHOLD) -> tuple[str, float, float]:
    """`e(j,c)` 의 부호 분류 (§7-2). 반환 (분류, P(e>+ε), P(e<−ε))."""
    p_pos = float(np.mean(boot_e > eps))
    p_neg = float(np.mean(boot_e < -eps))
    if p_pos >= prob:
        return SIGN_POSITIVE, p_pos, p_neg
    if p_neg >= prob:
        return SIGN_NEGATIVE, p_pos, p_neg
    return SIGN_NEUTRAL, p_pos, p_neg


def classify_contrast(sign_semi: str, sign_c: str) -> str:
    """contrast 의 3분류 (§7-2) — 양 캘린더 부호 분류의 조합."""
    if SIGN_NEUTRAL in (sign_semi, sign_c):
        return DIR_NEUTRAL
    return DIR_HELD if sign_semi == sign_c else DIR_REVERSED
