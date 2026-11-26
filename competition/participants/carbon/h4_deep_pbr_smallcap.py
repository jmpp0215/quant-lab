"""H4: 바구니 내 심층 저평가(-PBR) 및 소형주(-log(mktcap)) 동일 순위 결합 단독 신호.

결정일(또는 lag 시점 마지막 스냅샷) 기준 PBR(close/bps)이 낮을수록, 시가총액이 작을수록 높은 점수 부여.
"""
import numpy as np
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    at = (hist[(hist["snap"] == last) & hist["code"].isin(universe)]
          .drop_duplicates("code", keep="first")
          .set_index("code"))
    valid_bps = at["bps"].where(at["bps"] > 0)
    pbr = at["close"].where(at["close"] > 0) / valid_bps
    log_cap = np.log(at["mktcap"].where(at["mktcap"] > 0))
    r_val = (-pbr).reindex(universe).rank(pct=True)
    r_sz = (-log_cap).reindex(universe).rank(pct=True)
    return (0.5 * r_val + 0.5 * r_sz).reindex(universe)
