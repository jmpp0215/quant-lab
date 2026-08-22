import argparse
import sys
from decimal import Decimal

from quant import storage
import show

def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze execution slippage.")
    parser.add_argument("--account", help="filter by account")
    parser.add_argument("--since", help="filter by date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    with storage.connect() as conn:
        c = conn.cursor()
        
        query = """
            SELECT account, symbol, side, limit_price, avg_fill_price, filled_qty 
            FROM orders 
            WHERE filled_qty IS NOT NULL 
              AND avg_fill_price IS NOT NULL 
              AND filled_qty > 0
        """
        params = []
        
        if args.account:
            query += " AND account = ?"
            params.append(args.account)
        if args.since:
            query += " AND trade_date >= ?"
            params.append(args.since)
            
        rows = c.execute(query, params).fetchall()
        
        ignored_query = """
            SELECT account, symbol, side, quantity, limit_price, avg_fill_price, filled_qty 
            FROM orders 
            WHERE (filled_qty IS NULL OR avg_fill_price IS NULL OR filled_qty = 0)
        """
        ignored_params = []
        if args.account:
            ignored_query += " AND account = ?"
            ignored_params.append(args.account)
        if args.since:
            ignored_query += " AND trade_date >= ?"
            ignored_params.append(args.since)
        
        ignored_rows = c.execute(ignored_query, ignored_params).fetchall()

    if not rows:
        print("No filled orders found to analyze.")
        if ignored_rows:
            print(f"({len(ignored_rows)} orders were ignored due to missing fill data)")
        return 0

    stats = {} 
    total_slippage_krw = Decimal(0)
    total_notional = Decimal(0)
    total_favorable = 0
    total_count = 0
    
    for account, symbol, side, limit_price, avg_fill_price, filled_qty in rows:
        limit_p = Decimal(limit_price)
        avg_p = Decimal(avg_fill_price)
        qty = Decimal(str(filled_qty))
        
        if side == "BUY":
            slippage = (limit_p - avg_p) * qty
        else: # SELL
            slippage = (avg_p - limit_p) * qty
            
        notional = limit_p * qty
        is_favorable = slippage > 0
        
        key = (account, symbol)
        if key not in stats:
            stats[key] = {
                'favorable': 0, 'total_count': 0, 'slippage_krw': Decimal(0), 'total_notional': Decimal(0)
            }
        stats[key]['total_count'] += 1
        stats[key]['slippage_krw'] += slippage
        stats[key]['total_notional'] += notional
        if is_favorable:
            stats[key]['favorable'] += 1
            
        total_count += 1
        total_slippage_krw += slippage
        total_notional += notional
        if is_favorable:
            total_favorable += 1

    header_acc = "Account"
    header_sym = "Symbol"
    header_cnt = "Count (Fav)"
    header_slp = "Slippage(KRW)"
    header_pct = "Slippage(%)"
    
    print(f"\nSLIPPAGE ANALYSIS{' (Account: ' + args.account + ')' if args.account else ''}{' (Since: ' + args.since + ')' if args.since else ''}")
    print("-" * 88)
    print(f"{header_acc:<17} | {header_sym:<28} | {header_cnt:<11} | {header_slp:>13} | {header_pct:>11}")
    print("-" * 88)
    
    sorted_keys = sorted(stats.keys())
    for account, symbol in sorted_keys:
        s = stats[(account, symbol)]
        name = show._name(symbol)
        
        pct = (s['slippage_krw'] / s['total_notional'] * 100) if s['total_notional'] > 0 else Decimal(0)
        fav_str = f"{s['favorable']}/{s['total_count']}"
        krw_str = f"{s['slippage_krw']:,.0f}"
        pct_str = f"{pct:+.3f}%"
        
        display_sym = f"{symbol} {name}" if name != symbol else symbol
        sym_col = show.align(display_sym, 28) if hasattr(show, 'align') else display_sym.ljust(28)
        print(f"{account:<17} | {sym_col} | {fav_str:<11} | {krw_str:>13} | {pct_str:>11}")
        
    print("-" * 88)
    
    total_pct = (total_slippage_krw / total_notional * 100) if total_notional > 0 else Decimal(0)
    print(f"{'TOTAL':<17} | {'':<28} | {f'{total_favorable}/{total_count}':<11} | {f'{total_slippage_krw:,.0f}':>13} | {total_pct:+.3f}%")
    
    print("\nSummary:")
    if total_slippage_krw > 0:
        print(f"총 {total_count}건의 거래에서 지정가 대비 {total_slippage_krw:,.0f} KRW ({total_pct:+.3f}%) 를 유리하게 체결하여 비용을 방어했습니다.")
    elif total_slippage_krw < 0:
        print(f"총 {total_count}건의 거래에서 지정가 대비 {-total_slippage_krw:,.0f} KRW ({total_pct:+.3f}%) 의 슬리피지(비용)가 발생했습니다.")
    else:
        print(f"총 {total_count}건의 거래에서 지정가와 정확히 일치하게 체결되었습니다 (슬리피지 0).")
        
    if ignored_rows:
        print(f"\n[Exclusions] {len(ignored_rows)} orders were excluded (unfilled or missing fill price data):")
        for i_acc, i_sym, i_side, i_qty, i_limit, i_avg, i_filled in ignored_rows:
            i_name = show._name(i_sym)
            i_display_sym = f"{i_sym} {i_name}" if i_name != i_sym else i_sym
            print(f"  - {i_acc}: {i_side} {i_qty}x {i_display_sym} (Limit: {i_limit}, Filled: {i_filled}, AvgFill: {i_avg})")
            
    return 0

if __name__ == "__main__":
    sys.exit(main())
