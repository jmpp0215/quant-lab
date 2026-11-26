"""jongmin #5: 지표 24개(features.py) 릿지, 결정일마다 과거만으로 다시 학습.

결정일 t(= hist의 마지막 스냅샷)마다:
  학습 = 25 ≤ s ≤ t−2인 모든 스냅샷 s. 그날 PBR Top 50(harness와 같은 규칙)의 지표 순위와
         s → s+2 보유자 수익 순위(suspect 칸이 낀 종목은 뺌, 사라진 종목은 마지막 가격 회수).
  학습 스냅샷이 MIN_TRAIN개 미만이면 모든 점수 같음(정보 없음).
지표·전처리는 features.py, alpha = 행 수 × 0.01 (탐색 E2와 같음).
"""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

_spec = importlib.util.spec_from_file_location("jongmin_features", Path(__file__).with_name("features.py"))
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)

ALPHA_PER_ROW = 0.01
MIN_TRAIN = 24
WARMUP = 25
TOP_N = 50


def _universe(rows):
    c = rows[(rows["bps"] > 0) & (rows["close"] > 0) & (rows["volume"] > 0)]
    c = c.assign(pbr=c["close"] / c["bps"]).sort_values(["pbr", "code"])
    return c["code"].head(TOP_N).tolist()


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    hist = hist.drop_duplicates(["snap", "code"], keep="last")
    last = hist["snap"].max()
    W = F.Wide(hist, kospi)
    raw_ret = hist.pivot(index="snap", columns="code", values="ret")
    sus = hist.pivot(index="snap", columns="code", values="suspect").astype("boolean").fillna(False)
    by_snap = dict(tuple(hist.groupby("snap")))

    X, Y = [], []
    for s in range(WARMUP, last - 1):
        if s not in by_snap or s + 2 > last:
            continue
        codes = _universe(by_snap[s])
        win = raw_ret.loc[s + 1:s + 2, codes]
        y = (1 + win.fillna(0)).prod() - 1
        y = y[~sus.loc[s + 1:s + 2, codes].any()]
        if len(y) < 10:
            continue
        X.append(F.rank_center(F.features(W, s, codes)).loc[y.index].values)
        Y.append(y.rank(pct=True).values - 0.5)
    if len(X) < MIN_TRAIN:
        return pd.Series(0.0, index=universe)

    x, y = np.vstack(X), np.concatenate(Y)
    mu, sd = x.mean(0), x.std(0) + 1e-12
    z = (x - mu) / sd
    b = np.linalg.solve(z.T @ z + ALPHA_PER_ROW * len(z) * np.eye(z.shape[1]), z.T @ (y - y.mean()))
    now = F.rank_center(F.features(W, last, universe)).values
    return pd.Series(((now - mu) / sd) @ b, index=universe)
