import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Literal

@dataclass
class PeadConfig:
    # 1차 레이어 (항상 적용)
    # 'eps'는 DART API(fnlttSinglAcnt.json)에 필드가 없어 미지원 — 추후 별도 데이터 소스 확보 시 추가할 것.
    earnings_metric: Literal['operating_income', 'net_income'] = 'operating_income'
    entry_timing: Literal['t+1', 't+2'] = 't+1'
    dart_basis: Literal['CFS', 'OFS'] = 'CFS'
    lookback_quarters: int = 8

    # 유니버스
    universe: Literal['kospi200', 'kospi_all', 'kospi_kosdaq_mid'] = 'kospi_all'
    min_daily_trading_value: int = 10_000_000_000

    # 2차 레이어 (이익의 질 필터)
    enable_quality_filter: bool = False
    quality_metric: Literal['ocf_to_oi_ratio'] = 'ocf_to_oi_ratio'
    quality_threshold: float = 0.5

    # 3차 레이어 (수급 오버레이)
    enable_flow_overlay: bool = False
    flow_lookback_days: int = 5
    flow_weak_threshold: float = 0.0

    # 최종 가중치
    weighting_scheme: Literal['equal', 'inverse_vol'] = 'equal'

    # 포트폴리오 제약 (portfolio.py 구현 시점까지는 값만 정의)
    max_weight_per_stock: float = 0.10
    max_weight_per_sector: float = 0.30
    holding_company_handling: str = "default"

    def get_hash(self) -> str:
        """Config 조합의 고유 해시 반환 (조합별 성과 추적용)"""
        config_dict = asdict(self)
        config_str = json.dumps(config_dict, sort_keys=True)
        return hashlib.sha256(config_str.encode('utf-8')).hexdigest()
