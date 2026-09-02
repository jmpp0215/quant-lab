import sqlite3
from pathlib import Path
import logging
from typing import Dict, List
from .config import FactorConfig

log = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent.parent / "data" / "quant.db"

def init_db():
    """Initializes the database table for stock factors."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS factor_momentum_scores (
            symbol TEXT,
            rebalance_date TEXT,
            config_hash TEXT,
            momentum_score REAL,
            decile_rank INTEGER,
            is_estimable INTEGER,
            PRIMARY KEY (symbol, rebalance_date, config_hash)
        )
    """)
    conn.commit()
    conn.close()

def save_momentum_scores(
    rebalance_date: str,
    config: FactorConfig,
    results: List[Dict]
):
    """
    Saves a batch of momentum scores and rankings for a specific rebalance date.
    
    Args:
        rebalance_date: YYYYMMDD string
        config: FactorConfig instance
        results: List of dicts with keys: symbol, score, rank, is_estimable
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    config_hash = config.get_hash()
    
    records = []
    for r in results:
        records.append((
            r['symbol'],
            rebalance_date,
            config_hash,
            r.get('score'),
            r.get('rank'),
            int(r['is_estimable'])
        ))
        
    cursor.executemany("""
        INSERT OR REPLACE INTO factor_momentum_scores 
        (symbol, rebalance_date, config_hash, momentum_score, decile_rank, is_estimable)
        VALUES (?, ?, ?, ?, ?, ?)
    """, records)
    
    conn.commit()
    conn.close()
