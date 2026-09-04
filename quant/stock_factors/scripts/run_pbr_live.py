import sys
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from quant import config as main_config
from quant import accounts, market, rebalance, executor
from quant.toss_client import TossClient
from quant.stock_factors.portfolio_construction import (
    PBRConfig, 
    construct_target_portfolio, 
    calculate_diff, 
    format_orders
)
import FinanceDataReader as fdr

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("run_pbr_live")

def main():
    account, _ = accounts.extract_account(sys.argv, default="toss-bot")
    log.info(f"Connecting to account: {account}")
    
    cfg = accounts.resolve(account)
    client = cfg["client"]()
    
    log.info(f"DRY RUN mode: {client.dry_run}")
    if not client.dry_run:
        log.warning("WARNING: DRY RUN IS FALSE. ACTUAL ORDERS MIGHT BE PLACED.")
        
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    
    # 1. Fetch current account state
    snap = cfg["snapshot"](client)
    cash = cfg["buying_power"](client, {})
    
    log.info(f"Settled Cash (Snapshot): {snap.cash:,.0f} KRW")
    log.info(f"Buying Power: {cash:,.0f} KRW")
    
    positions = {}
    for p in snap.positions:
        if "currency" in p and p["currency"] != "KRW":
            continue
        positions[p["symbol"]] = rebalance.Position(
            symbol=p["symbol"],
            name=p["name"],
            quantity=int(p["qty"]),
            last_price=Decimal(str(p.get("lastPrice", p.get("price", "0"))))
        )
        
    total_assets = cash + sum(pos.value for pos in positions.values())
    log.info(f"Total Assets Before Rebalance: {total_assets:,.0f} KRW")
    log.info(f"Current Holdings: {len(positions)} stocks")
    
    # 2. Get Target Portfolio
    pbr_config = PBRConfig(target_n_stocks=50)
    log.info(f"Constructing Target Portfolio (Top {pbr_config.target_n_stocks}) as of {today_str}...")
    target_weights = construct_target_portfolio(today_str, pbr_config)
    
    if not target_weights:
        log.error("Failed to construct target portfolio (no symbols returned).")
        return 1
        
    # 3. Get Prices
    # In live, we should get real-time prices for the union of target symbols and held symbols
    symbols_to_price = set(target_weights.keys()) | set(positions.keys())
    
    try:
        live_prices = cfg["price"](client, symbols_to_price)
    except Exception as e:
        log.warning(f"Failed to fetch live prices from broker: {e}. Falling back to recent close.")
        live_prices = {}
        
    # Merge with last available DB prices if missing
    from quant.stock_factors.portfolio_construction import get_latest_prices
    db_prices = get_latest_prices(today_str)
    
    final_prices = {}
    for sym in symbols_to_price:
        final_prices[sym] = live_prices.get(sym, db_prices.get(sym, Decimal("0")))
        
    # Update cash with prices (just in case buying power depends on it)
    cash = cfg["buying_power"](client, final_prices)
    
    # 4. Generate Diff Orders
    orders = calculate_diff(target_weights, positions, final_prices, cash)
    
    print("\n" + "="*50)
    print("=== LIVE PBR DRY_RUN DIFF CALCULATION ===")
    print("="*50)
    
    if not orders:
        print("No orders needed. Portfolio is perfectly balanced.")
        return 0
        
    sells = [o for o in orders if o.side == "SELL"]
    buys = [o for o in orders if o.side == "BUY"]
    
    print("\n[ SELLS (청산 및 비중 축소) ]")
    if sells:
        for o in sells:
            print(f" - {o.name:<15} ({o.symbol}): {o.quantity:>4}주 매도 @ {o.limit_price:,.0f}원 (예상 {o.notional:,.0f}원)")
    else:
        print(" - 없음")
        
    print("\n[ BUYS (신규 편입 및 비중 확대) ]")
    if buys:
        for o in buys:
            print(f" - {o.name:<15} ({o.symbol}): {o.quantity:>4}주 매수 @ {o.limit_price:,.0f}원 (예상 {o.notional:,.0f}원)")
    else:
        print(" - 없음")
        
    # 5. Calculate Expected Final Portfolio
    print("\n[ 예상 계좌 구성 (After Execution) ]")
    sim_positions = {k: v for k, v in positions.items()}
    sim_cash = cash
    
    for o in orders:
        if o.side == "SELL":
            sim_cash += o.notional
            current_qty = sim_positions[o.symbol].quantity
            if current_qty - o.quantity <= 0:
                del sim_positions[o.symbol]
            else:
                p = sim_positions[o.symbol]
                sim_positions[o.symbol] = rebalance.Position(p.symbol, p.name, current_qty - o.quantity, p.last_price)
        elif o.side == "BUY":
            sim_cash -= o.notional
            current_qty = sim_positions.get(o.symbol, rebalance.Position(o.symbol, o.name, 0, Decimal("0"))).quantity
            p_val = final_prices.get(o.symbol, Decimal("0"))
            sim_positions[o.symbol] = rebalance.Position(o.symbol, o.name, current_qty + o.quantity, p_val)
            
    sim_total = sim_cash + sum(p.quantity * final_prices.get(p.symbol, p.last_price) for p in sim_positions.values())
    
    print(f" - 현금 잔액: {sim_cash:,.0f} KRW")
    print(f" - 주식 잔고: {len(sim_positions)} 종목")
    print(f" - 총 자산: {sim_total:,.0f} KRW (슬리피지/수수료 미반영)")
    
    # 6. Execute Orders (if not dry run)
    if not client.dry_run:
        print("\n" + "!"*50)
        print("!!! EXECUTING ACTUAL ORDERS !!!")
        print("!"*50)
        
        # We need an interactive prompt to prevent accidental execution if someone just exports the env var
        ans = input("Proceed with live execution? (yes/no): ")
        if ans.lower() != 'yes':
            print("Aborted by user.")
            return 0
            
        results = {}
        if sells:
            log.info("Executing SELLS...")
            results |= executor.execute(cfg["broker"], client, sells, final_prices)
            
        if buys:
            # Recompute cash and buys after sells
            log.info("Recomputing BUYS based on actual cash after SELLS...")
            # Wait a moment for settlement cash to reflect if broker updates it immediately
            cash_after_sells = cfg["buying_power"](client, final_prices)
            
            # Apply actual fills to positions
            def apply_fills(pos_dict, executed_sells, res):
                updated = dict(pos_dict)
                for o in executed_sells:
                    r = res.get(o.symbol)
                    if not r: continue
                    filled = r.get("filled_quantity", 0)
                    if not filled: continue
                    updated[o.symbol] = rebalance.Position(
                        symbol=o.symbol, name=o.name,
                        quantity=updated[o.symbol].quantity - filled,
                        last_price=o.limit_price
                    )
                return {s: p for s, p in updated.items() if p.quantity > 0}
                
            positions_after_sells = apply_fills(positions, sells, results)
            
            # Recalculate target buys
            revised_orders = calculate_diff(target_weights, positions_after_sells, final_prices, cash_after_sells)
            revised_buys = [o for o in revised_orders if o.side == "BUY"]
            
            log.info(f"Revised BUYS: {len(revised_buys)} orders")
            if revised_buys:
                log.info("Executing revised BUYS...")
                results |= executor.execute(cfg["broker"], client, revised_buys, final_prices)
            
        log.info("Execution complete.")
        
        # 7. Summary
        failed = [sym for sym, r in results.items() if not r.get("filled", False) and r.get("filled_quantity", 0) == 0]
        partial = [sym for sym, r in results.items() if not r.get("filled", False) and r.get("filled_quantity", 0) > 0]
        
        print("\n" + "="*50)
        print("=== EXECUTION SUMMARY ===")
        print("="*50)
        if failed:
            print(f"[전량 미체결 종목]: {', '.join(failed)}")
        if partial:
            print(f"[부분 체결 종목]: {', '.join(partial)}")
        if not failed and not partial:
            print("[모든 종목 정상 체결 완료]")
            
    else:
        print("\n(DRY_RUN mode: No actual orders were placed. Run with TOSS_DRY_RUN=false to execute.)")
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
