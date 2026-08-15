"""시리즈 뷰모델 — 화면에 뿌릴 행을 만든다. **Streamlit 을 import 하지 않는다.**

페이지 스크립트 안에 있던 로직을 떼어냈다. 이유는 하나다: **화면 안에 있으면 검사할 수
없다.** 2026-08-14 에 대시보드가 CANONICAL 과 다른 CAGR 을 띄우고 판정 배지를 뒤집은
채로 오래 살아남은 것도, 그 계산이 페이지 스크립트 안에 있어 테스트가 닿지 않았기
때문이다.

여기 함수들은 순수하다 — 카탈로그와 매니페스트를 받아 dict 리스트를 돌려준다.
그래서 `tests/integrity/` 가 직접 호출해 불변식을 검사할 수 있다.
"""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

from dashboard.artifacts import ArtifactCatalog
from dashboard.series import Series, SeriesSpec

ROOT = Path(__file__).resolve().parent.parent

#: MDD·Sharpe 열 제목. **기준을 제목에 박는다** — 값만 보면 어느 정의인지 알 수 없고,
#: 구간 기준(−34%)과 일별 NAV 기준(−58%)은 같은 태그에서 24%p 차이가 난다.
MDD_COL = 'MDD (구간 기준)'
SHARPE_COL = 'Sharpe (구간 기준)'

#: 비교표가 **읽어도 되는** 지표의 출처. 일별 NAV 는 여기 없다 — 의도적이다.
METRIC_SOURCE = 'ablation_artifact'


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
            'n': str(a.n_stocks) if a.n_stocks is not None else '미기록',
            '캘린더': (m.get('calendar') or {}).get('id', '미기록'),
            '산출': (a.generated_at or '')[:10],
            '출처': '분포집계' if a.source == 'summary' else '단일실행',
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
        '태그': r['tag'],
        '초점': r['tag'] == focal,
        'horizon': r['horizon'],
        '앞 순위': r['front_rank'],
        '뒤 순위': r['back_rank'],
        '이동': r['back_rank'] - r['front_rank'],
        '전체 순위': r['full_rank'],
        '앞 배수': round(r['front_mult'], 3),
        '뒤 배수': round(r['back_mult'], 3),
    } for r in sorted(d['rows'], key=lambda r: r['front_rank'])]
