"""
기업 분류기 — Phase 3 이후 활성화. 현재는 skeleton.

분류 타입: STABLE | GROWTH | CYCLICAL | TURNAROUND | ASSET | LEVERAGED
"""


class Classifier:
    """Phase 3 활성화 예정. 현재 모든 종목을 STABLE로 분류."""

    TYPES = ('STABLE', 'GROWTH', 'CYCLICAL', 'TURNAROUND', 'ASSET', 'LEVERAGED')

    def classify(self, ticker: str, pit_series: list[dict]) -> str:
        # TODO Phase 3: pit_series 기반 rule-based 분류 구현
        return 'STABLE'
