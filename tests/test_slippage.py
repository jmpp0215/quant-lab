import sqlite3
import sys
from io import StringIO
from unittest.mock import patch

import slippage
from quant import storage

def test_slippage_exclusions(capsys):
    """Test that unfilled orders are correctly excluded and reported."""
    # Create an in-memory database with the orders table
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            trade_date TEXT,
            placed_at TEXT,
            account TEXT,
            tranche INTEGER,
            symbol TEXT,
            side TEXT,
            quantity INTEGER,
            limit_price TEXT,
            order_id TEXT,
            filled INTEGER,
            filled_qty INTEGER,
            avg_fill_price TEXT,
            commission TEXT,
            tax TEXT
        )
    """)
    
    # Insert 2 filled orders and 1 unfilled order
    c.execute("""
        INSERT INTO orders (trade_date, placed_at, account, symbol, side, quantity, limit_price, filled_qty, avg_fill_price, filled)
        VALUES 
        ('2026-08-01', 'T1', 'test-acc', '102110', 'BUY', 10, '100000', 10, '99000', 1),   -- Favorable buy
        ('2026-08-01', 'T2', 'test-acc', '091170', 'SELL', 5, '15000', 5, '14000', 1),    -- Unfavorable sell
        ('2026-08-02', 'T3', 'test-acc', '133690', 'BUY', 20, '180000', NULL, NULL, 0)    -- Unfilled
    """)
    conn.commit()

    # Patch storage.connect to yield our in-memory connection
    class DummyContextManager:
        def __enter__(self):
            return conn
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch('quant.storage.connect', return_value=DummyContextManager()), \
         patch('sys.argv', ['slippage.py']):
        slippage.main()
        
    captured = capsys.readouterr()
    output = captured.out

    # Verify filled orders are calculated
    # Buy slippage = (100000 - 99000) * 10 = +10,000 KRW
    # Sell slippage = (14000 - 15000) * 5 = -5,000 KRW
    # Total slippage = 5,000 KRW (favorable)
    
    assert "SLIPPAGE ANALYSIS" in output
    assert "102110" in output
    assert "091170" in output
    assert "5,000 KRW" in output
    
    # Verify the unfilled order is explicitly mentioned in the Exclusions section
    assert "[Exclusions] 1 orders were excluded" in output
    assert "133690" in output
    assert "(Limit: 180000, Filled: None, AvgFill: None)" in output
    
    # Verify the excluded order doesn't affect the calculation
    assert "총 2건의 거래에서" in output
    
    conn.close()
