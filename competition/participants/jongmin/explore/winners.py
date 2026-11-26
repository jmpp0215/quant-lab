"""탐색 #E1 (REPORT.md 1절): 저PBR 바구니 수익을 견인한 종목의 공통점. train만 사용.

실행: python competition/participants/jongmin/explore/winners.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import harness as h  # noqa: E402

SPLIT_YEAR = 2012  # 결정일 연도 < 2012: 탐색, >= 2012: 확인
TOP = 5            # 견인 종목 = 바구니 안 20일 수익률 상위 5개

panel, _ = h.load("train")
panel = panel.drop_duplicates(["snap", "code"], keep="last")  # 2007-10-23 033270 중복 1건
wide = lambda col: panel.pivot(index="snap", columns="code", values=col)  # noqa: E731
ret = wide("ret").where(~wide("suspect").fillna(False).astype(bool)).fillna(0)
level = (1 + ret).cumprod()
close, mcap, value, eps, div = (wide(c) for c in ["close", "mktcap", "value", "eps", "div"])
present = wide("close").notna()


def back(df, s, k):
    return df.loc[s - k] if s - k in df.index else pd.Series(np.nan, index=df.columns)


def features(s, codes):
    L = level.loc[s]
    f = pd.DataFrame(index=codes)
    f["pbr"] = (panel[panel.snap == s].set_index("code").eval("close / bps")).reindex(codes)
    f["mktcap"] = mcap.loc[s, codes]
    f["price"] = close.loc[s, codes]
    f["turnover"] = (value.loc[s] / mcap.loc[s])[codes]
    f["value"] = value.loc[s, codes]
    for name, k in [("r1m", 2), ("r3m", 6), ("r6m", 12), ("r12m", 25)]:
        f[name] = (L / back(level, s, k) - 1)[codes].where(back(present, s, k)[codes] == True)  # noqa: E712
    f["r12_1"] = (back(level, s, 2) / back(level, s, 25) - 1)[codes].where(back(present, s, 25)[codes] == True)  # noqa: E712
    f["vol"] = ret.loc[s - 11:s, codes].std()
    f["pos52w"] = (L / level.loc[s - 24:s].max())[codes]
    # 순발행 = 시가총액 증가 중 보유자 수익으로 설명 안 되는 부분 (분할은 상쇄됨)
    f["issuance"] = ((mcap.loc[s] / back(mcap, s, 25)) / (L / back(level, s, 25)) - 1)[codes]
    f["eps_le0"] = (eps.loc[s, codes].fillna(0) <= 0).astype(float)
    f["div"] = div.loc[s, codes].fillna(0)
    f["pref"] = pd.Series([float(c[-1] != "0") for c in codes], index=codes)
    return f


rows, ics = [], []
for s in h.decision_snaps(panel, "train"):
    at = panel[panel.snap == s]
    codes = h.universe(at)
    y = h.forward_return(panel, s, codes).dropna()
    f = features(s, codes).loc[y.index]
    pct = f.rank(pct=True) * 100
    win = y.nlargest(TOP).index
    lose = y.nsmallest(TOP).index
    period = "탐색" if at.date.iloc[0].year < SPLIT_YEAR else "확인"
    rows.append(pd.concat({"win": pct.loc[win].mean() - 50, "lose": pct.loc[lose].mean() - 50}, axis=1)
                .assign(period=period))
    ic = {c: (0.0 if f[c].nunique() < 2 else f[c].rank().corr(y.rank())) for c in f}
    ics.append(pd.Series(ic, name=s).to_frame().T.assign(period=period))

R = pd.concat(rows)
I = pd.concat(ics)
out = []
for period in ["탐색", "확인"]:
    r = R[R.period == period].drop(columns="period")
    i = I[I.period == period].drop(columns="period")
    g = r.groupby(level=0)
    t = i.mean() / (i.std() / np.sqrt(len(i)))
    out.append(pd.DataFrame({
        (period, "견인 백분위-50"): g["win"].mean(),
        (period, "꼴찌 백분위-50"): g["lose"].mean(),
        (period, "IC"): i.mean(),
        (period, "t"): t,
    }))
table = pd.concat(out, axis=1).sort_values(("탐색", "t"))
n = I.period.value_counts()
print(f"결정일: 탐색 {n.get('탐색', 0)}, 확인 {n.get('확인', 0)}")
pd.set_option("display.width", 200)
print(table.round(3).to_string())
