# 다음 세션 프롬프트 — `make_canonical.py` + 문서 갱신

> 2026-08-12 세션에서 도출. 이 파일 자체를 다음 세션 첫 프롬프트로 붙여넣으면 된다.

---

## 배경 (왜 이걸 하는가)

2026-08-12 세션에서 모멘텀 `insufficient` 처리 정책을 fail-open → fail-closed 로 바꿨고
(`on_insufficient` 스위치, 커밋 `9ff2635`), 그 여파로 여러 문서의 수치가 낡았다.
그 과정에서 **실제 사고가 두 번** 났다:

1. **낡은 수치를 근거로 판단했다.** SPEC_14 §14-4 의 "MA200 시간 축 5위 → 5위"를
   인용해 논리를 세웠는데, 그 값은 이미 6위 → 5위로 바뀐 뒤였다. 산출 일자가 안 적혀
   있어 낡았다는 걸 알아챌 방법이 없었다.
2. **지표와 tape 가 자기모순이었다.** `run_ablation` 이 지표를 갱신한 뒤 5시간 동안
   `{tag}.json`(하림지주 제외한 수익률)과 `{tag}_holdings.json`(하림지주 포함한 목록)이
   공존했다. 둘 다 최신처럼 보였다.

수치가 산문에 박혀 있는 한 이 문제는 반복된다.

## 합의된 설계 방향 (2026-08-12 논의)

| 방안 | 판단 | 이유 |
|---|---|---|
| 손으로 쓰는 canonical 문서 | **반대** | 두 번째 진실 원천. 각주를 못 지키는 규율로는 canonical 도 못 지킨다. 선례도 있다 — 감사 Pass 0A 에서 *"CANONICAL 은 F_momentum_rim 이 아니라 F_no_r2r3"* 가 발견됐다 |
| **산출물에서 생성하는 canonical** | **찬성** | 파생물이라 드리프트 원천 차단 |
| 원본 SPEC 에 각주 | **제한적 유지** | 판정 근거로 쓰인 수치에만 |
| **수치에 산출 일자 병기** | **강력 찬성** | 제일 싸고, 위 사고 1번을 정확히 막았을 것 |

## 작업 1 — `scripts/make_canonical.py`

산출물에서 **생성**한다. 사람이 손으로 갱신하지 않는다.

```
입력  experiments/ablation/summary.json          (태그별 지표 + run_at)
      experiments/daily_nav/summary.json         (일별 지표 + generated_at + 게이트)
      experiments/live/dryrun/manifest.yaml      (config_hash, git_commit_sha, 편입 종목)
      scripts/live/freeze_rebalance.py           (DEFAULT_TAG, N_STOCKS)
      backtest/configs/constants.py              (RF/RK/OMEGA/VB_CAP)
출력  docs/CANONICAL.md
```

**담을 것 (좁게)**
- 현재 채택안: 태그·종목 수·필터 스택·모멘텀 기준
- 그 태그의 최신 지표 (구간 CAGR / 일별 net CAGR·MDD·Sharpe) + **각 값의 산출 일자**
- SPEC_10 게이트 현황 (G1/G2/G5 — G5 는 여전히 FAIL)
- 미해결 과제 목록
- 생성 시각 + 소스 파일별 mtime·해시

**담지 말 것**: SPEC 전체의 거울. 판정 논리·근거. 그건 SPEC 이 SSOT 다.

**주의**
- 값마다 출처 파일과 `run_at` 을 함께 찍어야 한다. 신선도가 안 보이면 만드는 의미가 없다.
- 손으로 수정하지 못하게 파일 상단에 **자동 생성 경고**를 박을 것.
- 생성 후 `git diff docs/CANONICAL.md` 가 비어야 정상(재생성 멱등성) — 테스트로 고정.

## 작업 2 — 낡은 수치 갱신

2026-08-12 세션에서 **의도적으로 다음 세션으로 미룬** 것들이다.

| 문서 | 상태 |
|---|---|
| `SPEC_12 §11` | ✅ 완료 (재발행 섹션 신설, §9-4 포인터) |
| `SPEC_14 §14-4` | ✅ 완료 (각주 + 출처·모집단 경고) |
| `docs/검토/f_pbr_ma200_median_split.md §5` | ✅ 완료 |
| `[이슈] 모멘텀필터_coverage_gate_미구현.md` | ✅ 완료 (A/B 결과 + tape 별건) |
| **`experiments/runs/2026.08.10._CALENDAR_SENS_A.md`** | ❌ **미처리** |
| **`experiments/runs/2026.08.10._CALENDAR_SENS_B.md`** | ❌ **미처리** |
| **`MASTER.md`** | ❌ 채택안 서술 확인 필요 |

보고서 2종은 날짜가 파일명에 있어 스냅샷임이 자명하므로, **덮어쓰지 말고 상단에 포인터**만
다는 것이 §14-4·§9-4 와 일관된 처리다.

## 작업 3 — tape/지표 비동기 이슈

`[이슈] 모멘텀필터_coverage_gate_미구현.md` 의 `[별건]` 절에 기록해 뒀다. 미착수.

- tape 헤더에 생성 시각 + 코드 SHA + 소스 지표 파일 해시를 박아 소비처가 stale 감지
- 또는 `run_ablation` 이 지표 갱신 시 대응 tape 무효화
- 현재 tape 없음: `52w80`·`ma250`·`ma300`·`ma150`·`mktresid126`

## 현재 확정 상태 (2026-08-12 기준)

```
운영 태그   F_pbr_ma200,  N_STOCKS = 13   (scripts/live/freeze_rebalance.py)
정책        on_insufficient = 'reject'    (전 모멘텀 태그, ablation.py setdefault)
커밋        7307d0f (로컬·GitHub·서버 동기화 완료)
테스트      fast 314 passed / integration 45 skipped (DB 미기동)

n=13 일별   net CAGR 18.55%  MDD −58.12%  Sharpe 0.725  게이트 PASS
            ※ SPEC_10 G5(> −45%) 여전히 FAIL — 미해결 최대 과제
라이브 신호  2026-04-03 편입 13종목, 정책 변경으로 **바뀌지 않음**
```

## 서버 환경 메모

```
/opt/stock-backtest    master 7307d0f      운영 DB 5433
~/qg_a                 detached 9c63d0c    스냅샷 DB 5435  ← A 실행용, 재사용 가능
~/qg_run               detached 0100296    스냅샷 DB 5435  ← §14 원본 작업장
```

`~/qg_run` 에는 이번 세션에서 fail-closed 를 얹었다(B 실행). 원본은
`/tmp/qg_momentum_criteria.py.bak`·`/tmp/qg_ablation.py.bak` 에 백업했으나 **`/tmp` 는
tmpfs 라 재부팅 시 소실**된다. qg_run 을 원복할 계획이면 먼저 확인할 것.

`/opt` 에 `smoke_test*_diagnostics_summary.json` 2건이 untracked 로 남아 있다
(2026-07-23 자 타 작업 산출물, 이번 커밋에서 의도적으로 제외).

## 이번 세션에서 배운 것 (반복 방지)

- **순위를 인용할 때는 모집단을 명시하라.** 재실행 태그 수가 바뀌면 순위가 통째로 밀린다.
  이걸 놓쳐 "MA 20/60 이 13→16위로 악화"라 잘못 보고했다.
- **확인 안 한 것을 단정하지 마라.** 파일 1개만 diff 하고 "6개 파일이 재현 불가능"이라
  단정했다가, 실제로는 5개가 master 와 바이트 동일이었다.
- **한 축만 보고 결론 내지 마라.** 반기 캘린더만 보고 "MA200 과 52w75 가 동률"이라 했으나,
  안 C·시간 축을 보면 52w75 는 앞 16위 → 뒤 1위로 크게 불안정하다.
- **채택 근거를 성과에 두지 마라.** fail-closed 는 의미론(`passed=True` 는 "확인됨"을
  뜻해야 한다)으로 채택했는데, 실제 성과는 오히려 나빠졌다(18.76% → 18.55%).
  성과 기반이었다면 뒤집혔을 것이다.
