"""jongmin #2: "망할 종목 피하기" 합성 점수 (탐색 #E1에서 16개 중 4개 선택).

바구니 안 순위 백분위의 평균. 높을수록 좋음.
  - 변동성: 최근 12칸(약 120거래일) ret 표준편차, 낮을수록 좋음
  - 순발행: 1년(25칸) 시가총액 증가 ÷ 보유자 수익 − 1, 적을수록 좋음 (분할은 상쇄됨)
  - 배당수익률: KRX div, 높을수록 좋음 (빈칸 = 무배당 0. 그날 전 종목이 빈칸이면 이 항목 제외)
  - 12-1개월 수익률: t−25칸 → t−2칸 보유자 수익, 높을수록 좋음
값이 없는 항목은 그 종목의 평균에서 뺀다. suspect 칸의 ret은 0으로 본다.
"""
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    w = hist[(hist["snap"] >= last - 25) & hist["code"].isin(universe)]
    w = w.drop_duplicates(["snap", "code"], keep="last")
    wide = lambda col: w.pivot(index="snap", columns="code", values=col)  # noqa: E731
    suspect = wide("suspect").astype("boolean").fillna(False).astype(bool)
    ret = wide("ret").where(~suspect).fillna(0)
    level = (1 + ret).cumprod()
    present = wide("close").notna()
    mcap = wide("mktcap")

    def at(df, s):
        return df.loc[s] if s in df.index else pd.Series(float("nan"), index=df.columns)

    old = at(present, last - 25).fillna(False).astype(bool)
    vol = ret.loc[last - 11:].std()
    issuance = (mcap.loc[last] / at(mcap, last - 25)) / (level.loc[last] / at(level, last - 25)) - 1
    r12_1 = (at(level, last - 2) / at(level, last - 25) - 1).where(old)
    div = w[w["snap"] == last].set_index("code")["div"]
    div = div.fillna(0) if div.notna().any() else div

    parts = pd.DataFrame({
        "vol": -vol,
        "issuance": -issuance.where(old),
        "div": div,
        "r12_1": r12_1,
    }).reindex(universe)
    return parts.rank(pct=True).mean(axis=1)
