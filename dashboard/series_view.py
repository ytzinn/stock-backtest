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
