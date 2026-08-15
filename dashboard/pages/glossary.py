"""용어사전 (sub2) — 같은 이름이 다른 것을 가리키는 자리의 목록.

내용은 여기 없다. `dashboard/glossary.py` 매니페스트가 소유하고 이 화면은 그리기만
한다 — 시리즈 매니페스트와 같은 구조다. 설명글을 화면 스크립트에 박으면 검사가 닿지
않아 조용히 낡는다 (`tests/integrity/test_glossary.py` 가 본문의 숫자를 산출물과 대조한다).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from dashboard.glossary import GLOSSARY, index_rows, search, source_file

st.set_page_config(page_title='용어사전', layout='wide', page_icon='📖')

st.title('📖 용어사전')
st.caption('이 저장소에서 **같은 이름이 다른 것을 가리키는 자리**를 모았습니다. '
           '여기 실린 혼동은 대부분 실제로 잘못된 숫자를 만든 적이 있습니다.')

col_q, col_scope = st.columns([3, 1.4])
query = col_q.text_input('검색', placeholder='용어·코드 식별자로 찾기 (artifact_key, median_cagr, MDD …)')
scope = col_scope.selectbox('범위', ['전체', '공통만', '축 전용만'])

terms = search(query)
if scope == '공통만':
    terms = tuple(t for t in terms if t.is_common)
elif scope == '축 전용만':
    terms = tuple(t for t in terms if not t.is_common)

if not terms:
    st.info(f'`{query}` 에 해당하는 용어가 없습니다.')
    st.stop()

st.dataframe(pd.DataFrame(index_rows(terms)), use_container_width=True, hide_index=True)
st.divider()

for t in terms:
    with st.expander(f'**{t.term}** — {t.one_line}', expanded=len(terms) == 1):
        st.markdown(t.body)
        if t.incident:
            st.warning(f'**실제 사고** — {t.incident}')
        meta = []
        if not t.is_common:
            meta.append('적용 축 ' + ', '.join(f'`{s}`' for s in t.series))
        if t.sources:
            meta.append('근거 ' + ' · '.join(f'`{s}`' for s in t.sources))
        if meta:
            st.caption(' | '.join(meta))

st.divider()
st.caption(
    f'총 {len(GLOSSARY)}개 항목. 항목은 `dashboard/glossary.py` 가 소유하고, '
    f'본문에 박힌 숫자는 `tests/integrity/test_glossary.py` 가 산출물과 대조합니다 — '
    f'재발행으로 값이 바뀌면 검사가 깨져서 설명이 낡은 채로 남지 않습니다.')

# 근거 경로가 실재하는지는 검사가 지킨다. 화면은 경로를 보여주기만 한다 —
# 여기서 파일을 열어 보여주려 하면 서버에만 있는 산출물에서 조용히 빈 화면이 된다.
missing = sorted({s for t in GLOSSARY for s in t.sources
                  if not (Path(__file__).resolve().parent.parent.parent / source_file(s)).exists()})
if missing:
    st.error('근거 경로가 이 환경에 없습니다 (서버에는 있을 수 있습니다): '
             + ', '.join(f'`{m}`' for m in missing))
