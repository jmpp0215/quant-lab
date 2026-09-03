import hashlib
import json
from dataclasses import dataclass, asdict

@dataclass
class FactorConfig:
    lookback_months: int = 6  # 3, 6, 12
    entry_timing: str = "t+1" # t+1 or t+2 for comparison with PEAD
    lookback_quarters: int = 4
    min_periods: int = 2

    def get_hash(self) -> str:
        d = asdict(self)
        s = json.dumps(d, sort_keys=True)
        return hashlib.md5(s.encode('utf-8')).hexdigest()
