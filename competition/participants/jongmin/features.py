"""jongmin 지표 24개 (탐색 E2). 스냅샷 s의 값은 s 이하 행만으로 계산한다.

Wide(panel, kospi)로 넓은 표를 한 번 만들고 features(W, s, codes)로 한 결정일의 지표를 뽑는다.
탐색 스크립트와 제출 파일이 같은 코드를 쓴다.
"""
import numpy as np
import pandas as pd

NAMES = ["pbr", "mktcap", "price", "turnover", "value", "r2w", "r1m", "r3m", "r6m", "r12m", "r12_1",
         "vol", "pos52w", "issuance", "eps_le0", "div", "pref",
         "max1", "beta", "value_surge", "bps_g", "ey", "pbr_chg", "age"]


class Wide:
    def __init__(self, panel, kospi):
        panel = panel.drop_duplicates(["snap", "code"], keep="last")
        piv = lambda c: panel.pivot(index="snap", columns="code", values=c)  # noqa: E731
        suspect = piv("suspect").astype("boolean").fillna(False).astype(bool)
        self.ret = piv("ret").where(~suspect).fillna(0)
        self.level = (1 + self.ret).cumprod()
        self.present = piv("close").notna()
        self.close, self.mcap, self.value = piv("close"), piv("mktcap"), piv("value")
        self.eps, self.div, self.bps = piv("eps"), piv("div"), piv("bps")
        first = self.present.idxmax()  # 첫 출현 스냅샷 (s 이하 행만 있으면 시점 안전)
        self.first = first.where(self.present.any())
        dates = panel.groupby("snap")["date"].first()
        k = kospi.set_index("date")["close"].sort_index()
        kc = k.reindex(dates.values, method="ffill")
        self.mkt = pd.Series(kc.values, index=dates.index).pct_change()

    def at(self, df, s):
        return df.loc[s] if s in df.index else pd.Series(np.nan, index=df.columns)


def features(W, s, codes):
    L = W.level.loc[s]
    ok = lambda k: W.at(W.present, s - k).fillna(False).astype(bool)  # noqa: E731
    f = pd.DataFrame(index=codes)
    bps = W.bps.loc[s].where(W.bps.loc[s] > 0)
    f["pbr"] = W.close.loc[s] / bps
    f["mktcap"] = W.mcap.loc[s]
    f["price"] = W.close.loc[s]
    f["turnover"] = W.value.loc[s] / W.mcap.loc[s]
    f["value"] = W.value.loc[s]
    for name, k in [("r2w", 1), ("r1m", 2), ("r3m", 6), ("r6m", 12), ("r12m", 25)]:
        f[name] = (L / W.at(W.level, s - k) - 1).where(ok(k))
    f["r12_1"] = (W.at(W.level, s - 2) / W.at(W.level, s - 25) - 1).where(ok(25))
    win12 = W.ret.loc[s - 11:s]
    f["vol"] = win12.std()
    f["pos52w"] = L / W.level.loc[s - 24:s].max()
    f["issuance"] = ((W.mcap.loc[s] / W.at(W.mcap, s - 25)) / (L / W.at(W.level, s - 25)) - 1).where(ok(25))
    f["eps_le0"] = (W.eps.loc[s].fillna(0) <= 0).astype(float)
    d = W.div.loc[s]
    f["div"] = d.fillna(0) if d[codes].notna().any() else d
    f["pref"] = pd.Series([float(c[-1] != "0") for c in W.close.columns], index=W.close.columns)
    f["max1"] = win12.max()
    r25, m25 = W.ret.loc[s - 24:s, codes], W.mkt.loc[s - 24:s]
    mv = m25.var()
    f["beta"] = r25.apply(lambda col: col.cov(m25)) / mv if mv > 0 else np.nan
    f["value_surge"] = W.value.loc[s - 1:s].mean() / W.value.loc[s - 24:s].mean()
    bps_old = W.at(W.bps, s - 25)
    f["bps_g"] = bps / bps_old.where(bps_old > 0) - 1
    f["ey"] = W.eps.loc[s] / W.close.loc[s]
    pbr_old = W.at(W.close, s - 25) / bps_old.where(bps_old > 0)
    f["pbr_chg"] = (f["pbr"] / pbr_old.reindex(f.index) - 1).where(ok(25))
    f["age"] = (s - W.first).clip(upper=25)
    return f.reindex(codes)[NAMES].replace([np.inf, -np.inf], np.nan)


def rank_center(f):
    """결정일 안 순위 → −0.5~+0.5, 빈칸 0."""
    return (f.rank(pct=True) - 0.5).fillna(0)
