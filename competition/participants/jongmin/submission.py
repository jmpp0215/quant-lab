"""jongmin의 제출 파일. 규칙은 competition/RULES.md.

predict()는 결정일마다 한 번 불립니다.
  hist      그날까지의 스냅샷 패널 (pandas.DataFrame, 모든 KOSPI 종목, 미래 행 없음)
  universe  그날의 PBR Top 50 종목코드 목록
  as_of     hist의 마지막 날짜 (pandas.Timestamp)
  kospi     as_of까지의 KOSPI 지수 일별 데이터
  ext_dir   이 폴더의 external/ (외부 데이터를 둘 곳, RULES.md §8)
돌려줄 것: pandas.Series, index = 종목코드, 값 = 점수.
점수가 높을수록 이후 20거래일 수익률이 높을 것으로 본다는 뜻입니다.

파일 이름은 자유입니다 (submission_v2.py 등). 최종 제출은 FINAL.txt에 적습니다.
"""
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    # 예시: 52주(25스냅샷) 고가 대비 현재가 위치. 아이디어로 바꾸세요.
    last = hist["snap"].max()
    w = hist[(hist["snap"] > last - 25) & hist["code"].isin(universe)].copy()
    r = w["ret"].where(~w["suspect"].astype(bool)).fillna(0)  # 의심 구간은 0으로
    w["level"] = (1 + r).groupby(w["code"]).cumprod()
    now = w[w["snap"] == last].set_index("code")["level"]
    high = w.groupby("code")["level"].max()
    return (now / high).reindex(universe)
