"""systemd 유닛 정의의 형태 검사.

## 왜 필요한가

유닛이 repo 에 없던 동안 서버 사본에 결함 두 개가 살아 있었다(2026-08-14 발견):

- `--server.port=8501 -- server.address=0.0.0.0` — **`--` 뒤 공백.** streamlit 에서 `--`
  는 인자 분리자라, 그 뒤는 streamlit 옵션이 아니라 **앱의 위치 인자**로 넘어간다.
  주소 설정이 조용히 무시된다.
- `BACKTEST_LOG_DIR=var/log/backtest` — **앞 슬래시 누락.** 상대 경로라
  `/opt/stock-backtest/var/log/backtest` 로 해석돼 로그가 엉뚱한 곳에 쌓인다.

둘 다 문법 오류가 아니라 **조용히 다르게 동작하는** 종류다. 사람 눈으로는 안 보인다.

## 무엇을 검사하지 않나

서버에 설치된 사본과의 대조는 하지 않는다 — 테스트가 서버에 붙지 않기 때문이다.
그래서 규약이 하나 있다: **유닛을 고칠 때 repo 를 먼저 고치고 서버로 복사한다**
(`deploy/README.md`). 반대 방향으로 하면 이 검사가 무의미해진다.
"""
from __future__ import annotations

import configparser
import re

import pytest

from backtest.canonical_state import ROOT

UNIT = ROOT / 'deploy/backtest-dashboard.service'
PORT = '8501'


@pytest.fixture(scope='module')
def unit() -> configparser.ConfigParser:
    assert UNIT.exists(), f'{UNIT} 이 없다 — 유닛 정의는 repo 가 정본이다.'
    # systemd 는 같은 키(Environment=)를 여러 번 쓴다. strict=False 라야 읽힌다.
    cp = configparser.ConfigParser(strict=False, allow_no_value=True)
    cp.optionxform = str          # systemd 키는 대소문자를 구분한다
    cp.read_string(UNIT.read_text(encoding='utf-8'))
    return cp


def test_has_required_sections(unit):
    for section in ('Unit', 'Service', 'Install'):
        assert unit.has_section(section), f'[{section}] 절이 없다'
    assert unit['Install'].get('WantedBy'), (
        '[Install] WantedBy 가 없으면 `systemctl enable` 이 아무 일도 하지 않는다 — '
        '재부팅 후 대시보드가 안 뜬다.')


def test_exec_start_has_no_argument_separator(unit):
    """`--` 뒤에 공백을 두면 그 뒤 옵션이 streamlit 이 아니라 앱으로 넘어간다.

    서버 사본에 실제로 있던 결함이다. 에러 없이 **설정만 조용히 무시된다.**
    """
    exec_start = unit['Service']['ExecStart']
    assert not re.search(r'(?<!\S)--\s', exec_start), (
        f'ExecStart 에 인자 분리자 `-- ` 가 있다 — 그 뒤 옵션은 streamlit 에 전달되지 '
        f'않는다:\n  {exec_start}')
    for opt in re.findall(r'--\S+', exec_start):
        assert '=' in opt or opt in ('--server.headless',), (
            f'옵션 형식이 `--key=value` 가 아니다: {opt}')


def test_exec_start_is_absolute_and_uses_the_venv(unit):
    """cron 규칙과 같은 이유 — `python3` 가 아니라 venv 절대경로여야 한다."""
    exec_start = unit['Service']['ExecStart']
    binary = exec_start.split()[0]
    assert binary.startswith('/'), f'ExecStart 가 절대경로가 아니다: {binary}'
    assert 'venv/bin/python' in binary, (
        f'venv 파이썬이 아니다: {binary}. 시스템 python 은 의존성이 다르다.')
    assert f'--server.port={PORT}' in exec_start, f'포트가 {PORT} 이 아니다'


def test_paths_are_absolute(unit):
    """경로처럼 생긴 값은 전부 절대경로여야 한다.

    `BACKTEST_LOG_DIR=var/log/backtest` 처럼 앞 슬래시를 빠뜨리면 WorkingDirectory
    기준 상대경로가 되어 로그가 엉뚱한 곳에 쌓인다 — 에러는 안 난다.
    """
    svc = unit['Service']
    assert svc['WorkingDirectory'].startswith('/'), 'WorkingDirectory 가 상대경로다'
    assert svc['EnvironmentFile'].startswith('/'), 'EnvironmentFile 이 상대경로다'

    # Environment= 는 여러 줄이라 원문에서 직접 훑는다 (configparser 는 마지막 것만 남긴다).
    bad = []
    for line in UNIT.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line.startswith('Environment='):
            continue
        key, _, value = line[len('Environment='):].partition('=')
        if ('DIR' in key or 'ROOT' in key or 'PATH' in key) and not value.startswith('/'):
            bad.append(f'{key}={value}')
    assert not bad, f'경로 환경변수가 절대경로가 아니다: {bad}'


def test_restart_survives_clean_exit(unit):
    """`Restart=on-failure` 면 정상 종료(exit 0)로 내려갔을 때 안 살아난다.

    2026-08-14 에 kill 로 두 번 내려갔고 둘 다 exit 0 이었다. 대시보드는 사람이 명시적으로
    멈추는 경우 외에는 떠 있어야 하므로 `always` 여야 한다 (`systemctl stop` 은 always
    여도 재기동하지 않는다).
    """
    assert unit['Service'].get('Restart') == 'always', (
        f"Restart={unit['Service'].get('Restart')} — 정상 종료로 내려가면 안 살아난다.")
    assert unit['Service'].get('RestartSec'), 'RestartSec 이 없으면 기본 100ms 로 폭주한다'
