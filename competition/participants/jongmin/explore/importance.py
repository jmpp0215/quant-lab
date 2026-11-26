"""탐색 #E2 (REPORT.md 1절): 지표 24개를 릿지에 넣고 영향도 보기. train만 사용.

실행: python competition/participants/jongmin/explore/importance.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # competition/
sys.path.insert(0, str(HERE.parent))      # participants/jongmin/
import harness as h  # noqa: E402
from features import NAMES, Wide, features, rank_center  # noqa: E402

ALPHA_PER_ROW = 0.01
BLOCKS = [(2006, 2007), (2008, 2009), (2010, 2011), (2012, 2013)]

panel, kospi = h.load("train")
W = Wide(panel, kospi)
X, Y, D = [], [], []
for s in h.decision_snaps(panel, "train"):
    at = panel[panel.snap == s]
    codes = h.universe(at)
    y = h.forward_return(panel, s, codes).dropna()
    f = rank_center(features(W, s, codes)).loc[y.index]
    X.append(f)
    Y.append(y.rank(pct=True) - 0.5)
    D.append(pd.Series(at.date.iloc[0], index=y.index))
X, Y, D = pd.concat(X), pd.concat(Y), pd.concat(D)
year = D.dt.year.values


def fit(x, y):
    mu, sd = x.mean(0), x.std(0) + 1e-12
    z = (x - mu) / sd
    a = ALPHA_PER_ROW * len(z)
    b = np.linalg.solve(z.T @ z + a * np.eye(z.shape[1]), z.T @ (y - y.mean()))
    return mu, sd, b


def predict(m, x):
    mu, sd, b = m
    return ((x - mu) / sd) @ b


def ic_by_date(pred, y, d):
    df = pd.DataFrame({"p": pred, "y": y, "d": d})
    return df.groupby("d").apply(lambda g: g.p.rank().corr(g.y.rank()), include_groups=False)


x, y = X.values, Y.values
rng = np.random.default_rng(0)
oob_ic, perm = [], {n: [] for n in NAMES}
for lo, hi in BLOCKS:
    test = (year >= lo) & (year <= hi)
    m = fit(x[~test], y[~test])
    xt, yt, dt = x[test], y[test], D.values[test]
    base = ic_by_date(predict(m, xt), yt, dt)
    oob_ic.append(base)
    for j, n in enumerate(NAMES):
        drops = []
        for _ in range(5):
            xp = xt.copy()
            for d in np.unique(dt):  # 결정일 안에서 섞기
                idx = np.where(dt == d)[0]
                xp[idx, j] = xp[rng.permutation(idx), j]
            drops.append(base.mean() - ic_by_date(predict(m, xp), yt, dt).mean())
        perm[n].append(np.mean(drops))

print("블록 밖 IC (모델 전체):")
for (lo, hi), ic in zip(BLOCKS, oob_ic):
    print(f"  {lo}-{hi}: IC {ic.mean():+.4f} (t {ic.mean() / (ic.std() / np.sqrt(len(ic))):+.2f}, n {len(ic)})")
allic = pd.concat(oob_ic)
print(f"  전체: IC {allic.mean():+.4f} (t {allic.mean() / (allic.std() / np.sqrt(len(allic))):+.2f})")

m_all = fit(x, y)
uni = {n: ic_by_date(X[n].values, y, D.values) for n in NAMES}
table = pd.DataFrame({
    "perm 평균": {n: np.mean(perm[n]) for n in NAMES},
    "perm 양수블록": {n: int(np.sum(np.array(perm[n]) > 0)) for n in NAMES},
    "계수(전체)": dict(zip(NAMES, m_all[2])),
    "단변량 IC": {n: uni[n].mean() for n in NAMES},
    "단변량 t": {n: uni[n].mean() / (uni[n].std() / np.sqrt(len(uni[n]))) for n in NAMES},
}).sort_values("perm 평균", ascending=False)
pd.set_option("display.width", 200)
print()
print(table.round(4).to_string())
