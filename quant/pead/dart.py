import io
import json
import logging
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List

import requests

log = logging.getLogger(__name__)

# --- API Configuration ---
OPENDART_API_KEY = os.getenv("OPENDART_API_KEY")
OPENDART_BASE_URL = "https://opendart.fss.or.kr/api"

DART_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "dart_cache"
CORP_CODE_CACHE_PATH = DART_CACHE_DIR / "corpCode.json"


def _get_api_key() -> str:
    if not OPENDART_API_KEY:
        raise ValueError("OPENDART_API_KEY is not set in environment variables.")
    return OPENDART_API_KEY


def _rate_limit_and_retry(url: str, params: dict, max_retries: int = 3, delay: float = 0.1) -> dict:
    """Helper to fetch OpenDART API with retries and throttling."""
    for attempt in range(max_retries):
        time.sleep(delay)  # Basic throttling to prevent exceeding limits
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            # API errors are usually returned with status '000' for success, or '013' for no data, etc.
            data = response.json()
            status = data.get("status")
            
            if status == "000":
                return data
            elif status == "013": # No data found
                log.info(f"No data found for url: {url} with params {params}")
                return {"list": []}
            elif status == "020": # Max calls exceeded
                raise Exception("OpenDART API max calls exceeded limit.")
            else:
                log.warning(f"DART API returned status {status}: {data.get('message')}")
                if status == "800": # Temporary server error
                    time.sleep(delay * 5)
                    continue
                return data # Return anyway for other errors to handle upstream
                
        except requests.exceptions.RequestException as e:
            log.warning(f"Attempt {attempt+1}/{max_retries} failed for {url}: {e}")
            time.sleep(delay * (2 ** attempt))
            
    raise Exception(f"Failed to fetch from DART API after {max_retries} attempts.")


def _get_corp_code(symbol: str) -> str | None:
    """Maps a 6-digit stock symbol to an 8-digit DART corp_code using cached XML."""
    if not CORP_CODE_CACHE_PATH.exists():
        DART_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        log.info("Downloading and caching DART corpCode.xml...")
        url = f"{OPENDART_BASE_URL}/corpCode.xml"
        res = requests.get(url, params={"crtfc_key": _get_api_key()})
        res.raise_for_status()
        
        with zipfile.ZipFile(io.BytesIO(res.content)) as z:
            xml_data = z.read("CORPCODE.xml")
            
        root = ET.fromstring(xml_data)
        mapping = {}
        for list_node in root.findall('list'):
            stock_code = list_node.find('stock_code').text
            corp_code = list_node.find('corp_code').text
            if stock_code and stock_code.strip():
                mapping[stock_code.strip()] = corp_code.strip()
                
        with open(CORP_CODE_CACHE_PATH, 'w') as f:
            json.dump(mapping, f)
            
    with open(CORP_CODE_CACHE_PATH, 'r') as f:
        mapping = json.load(f)
        
    return mapping.get(symbol)


def fetch_financial_statements(symbol: str, target_year: str, report_code: str) -> list[dict]:
    """
    OpenDART '단일회사 주요계정' API를 호출하여 CFS/OFS 및 operating_income/net_income/eps 데이터를 수집합니다.
    (API는 CFS/OFS가 있으면 모두 반환합니다.)
    """
    corp_code = _get_corp_code(symbol)
    if not corp_code:
        log.warning(f"No DART corp_code found for symbol {symbol}")
        return []
        
    url = f"{OPENDART_BASE_URL}/fnlttSinglAcnt.json"
    params = {
        "crtfc_key": _get_api_key(),
        "corp_code": corp_code,
        "bsns_year": target_year,
        "reprt_code": report_code
    }
    
    data = _rate_limit_and_retry(url, params)
    
    # Extract operating_income, net_income
    # EPS is typically not in 'fnlttSinglAcnt.json', but for now we map what we can. 
    # Proper EPS requires reading 'fnlttSinglAcntAll.json' or computing it. 
    # For this implementation scope, we parse standard fields.
    results = []
    
    # Mapping DART account names to our metrics
    target_accounts = {
        "영업이익": "operating_income",
        "당기순이익": "net_income",
        "기본주당이익": "eps", # May not exist in SinglAcnt, fallback handles it
        "자산총계": "total_assets",
        "자본총계": "total_equity",
        "매출액": "revenue"
    }
    
    for item in data.get("list", []):
        act_nm = item.get("account_nm", "")
        for k, v in target_accounts.items():
            if k == act_nm or (k in act_nm and k not in ["영업이익", "당기순이익"]): # exact match for BS items is safer if possible, but DART varies. Let's just use exact match for Assets/Equity to avoid '유동자산' matching '자산'
                # wait, let's just use 'in' but exclude some known bad matches, or just use precise list
                pass
        
        # better matching logic
        # ... actually, let's rewrite the loop

    for item in data.get("list", []):
        act_nm = item.get("account_nm", "").strip()
        matched_metric = None
        
        if "영업이익" in act_nm: matched_metric = "operating_income"
        elif "당기순이익" in act_nm: matched_metric = "net_income"
        elif "기본주당이익" in act_nm: matched_metric = "eps"
        elif act_nm == "자산총계": matched_metric = "total_assets"
        elif act_nm == "자본총계": matched_metric = "total_equity"
        elif act_nm == "매출액": matched_metric = "revenue"
        
        if matched_metric:
            try:
                amount = float(item.get("thstrm_amount", "0").replace(",", ""))
            except ValueError:
                amount = None
                
            if amount is not None:
                results.append({
                    "symbol": symbol,
                    "target_year": target_year,
                    "report_code": report_code,
                    "metric": matched_metric,
                    "value": amount,
                    "basis": item.get("fs_div"), # 'CFS' or 'OFS'
                    "rcept_dt": item.get("rcept_no", "")[:8],
                    "original_rcept_dt": None
                })
    return results

def fetch_issued_shares(symbol: str, target_year: str, report_code: str) -> list[dict]:
    """OpenDART '주식의 총수 등' API를 호출하여 발행주식수를 수집합니다."""
    corp_code = _get_corp_code(symbol)
    if not corp_code: return []
    
    results = []
    url = f"{OPENDART_BASE_URL}/stockTotqySttus.json"
    params = {
        "crtfc_key": _get_api_key(),
        "corp_code": corp_code,
        "bsns_year": target_year,
        "reprt_code": report_code
    }
    
    data = _rate_limit_and_retry(url, params)
    for item in data.get("list", []):
        if item.get("se") == "보통주":
            issued = item.get("istc_totqy", "0").replace(",", "")
            try:
                val = float(issued)
                results.append({
                    "symbol": symbol,
                    "target_year": target_year,
                    "report_code": report_code,
                    "metric": "issued_shares",
                    "value": val,
                    "basis": "CFS",
                    "rcept_dt": item.get("rcept_no", "")[:8],
                    "original_rcept_dt": None
                })
            except ValueError: pass
            
    return results

def fetch_gross_profit(symbol: str, target_year: str, report_code: str) -> list[dict]:
    """OpenDART '단일회사 전체재무제표' API를 호출하여 매출총이익을 수집합니다."""
    corp_code = _get_corp_code(symbol)
    if not corp_code: return []
    
    results = []
    url = f"{OPENDART_BASE_URL}/fnlttSinglAcntAll.json"
    params = {
        "crtfc_key": _get_api_key(),
        "corp_code": corp_code,
        "bsns_year": target_year,
        "reprt_code": report_code,
        "fs_div": "CFS"
    }
    
    try:
        data = _rate_limit_and_retry(url, params)
        for item in data.get("list", []):
            act_nm = item.get("account_nm", "").strip()
            if act_nm == "매출총이익":
                try:
                    val = float(item.get("thstrm_amount", "0").replace(",", ""))
                    results.append({
                        "symbol": symbol,
                        "target_year": target_year,
                        "report_code": report_code,
                        "metric": "gross_profit",
                        "value": val,
                        "basis": "CFS",
                        "rcept_dt": item.get("rcept_no", "")[:8],
                        "original_rcept_dt": None
                    })
                except ValueError: pass
    except Exception as e:
        if "max calls exceeded" in str(e).lower():
            raise
        if "No data found" not in str(e):
            log.warning(f"Failed to fetch gross profit for {symbol}: {e}")

    return results

def fetch_cash_flow_statement(symbol: str, target_year: str, report_code: str) -> list[dict]:
    """
    OpenDART '단일회사 전체재무제표' API를 호출하여 영업활동현금흐름(OCF) 데이터를 
    config와 무관하게 CFS/OFS 모두 수집합니다.
    """
    corp_code = _get_corp_code(symbol)
    if not corp_code:
        return []
        
    url = f"{OPENDART_BASE_URL}/fnlttSinglAcntAll.json"
    results = []
    
    # 전체재무제표 API는 fs_div를 파라미터로 명시해야 하므로 CFS, OFS를 각각 호출
    for basis in ["CFS", "OFS"]:
        params = {
            "crtfc_key": _get_api_key(),
            "corp_code": corp_code,
            "bsns_year": target_year,
            "reprt_code": report_code,
            "fs_div": basis
        }
        
        data = _rate_limit_and_retry(url, params)
        for item in data.get("list", []):
            if item.get("sj_div") == "CF": # 현금흐름표
                account_id = item.get("account_id", "")
                account_nm = item.get("account_nm", "")
                
                # '영업활동현금흐름'의 IFRS 식별자 혹은 한글명 매칭
                if "ifrs-full_CashFlowsFromUsedInOperatingActivities" in account_id or "영업활동현금흐름" in account_nm:
                    try:
                        amount = float(item.get("thstrm_amount", "0").replace(",", ""))
                    except ValueError:
                        amount = None
                        
                    if amount is not None:
                        results.append({
                            "symbol": symbol,
                            "target_year": target_year,
                            "report_code": report_code,
                            "metric": "operating_cash_flow",
                            "value": amount,
                            "basis": basis,
                            "rcept_dt": item.get("rcept_no", "")[:8],
                            "original_rcept_dt": None
                        })
                        break # Found it for this basis
                        
    return results


import sqlite3

def save_dart_report(metrics: list[dict]):
    """
    수집된 재무제표 및 현금흐름표 데이터를 `pead_dart_raw` DB 테이블에 저장합니다.
    """
    if not metrics:
        return
        
    db_path = Path(__file__).parent.parent.parent / "data" / "quant.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    records = []
    for m in metrics:
        # symbol, target_year, rcept_dt, report_code, metric, value, basis, is_correction, original_rcept_dt
        records.append((
            m["symbol"],
            m.get("target_year", ""),
            m.get("rcept_dt", ""),
            m["report_code"],
            m["metric"],
            m["value"],
            m["basis"],
            0, # is_correction
            m.get("original_rcept_dt")
        ))
        
    cursor.executemany("""
        INSERT OR IGNORE INTO pead_dart_raw 
        (symbol, target_year, rcept_dt, report_code, metric, value, basis, is_correction, original_rcept_dt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    
    conn.commit()
    conn.close()

def save_normalized_report(normalized_metrics: list[dict]):
    """
    환산된 단일 분기 데이터를 `pead_quarterly_normalized` DB 테이블에 저장합니다.
    """
    if not normalized_metrics:
        return
        
    db_path = Path(__file__).parent.parent.parent / "data" / "quant.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    records = []
    for m in normalized_metrics:
        records.append((
            m["symbol"],
            m["target_year"],
            m["rcept_dt"],
            m["report_code"],
            m["metric"],
            m["basis"],
            m["quarterly_value"],
            int(m["is_estimable"])
        ))
        
    cursor.executemany("""
        INSERT OR REPLACE INTO pead_quarterly_normalized
        (symbol, target_year, rcept_dt, report_code, metric, basis, quarterly_value, is_estimable)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    
    conn.commit()
    conn.close()


def _atomic_write_json(path: Path, data: dict):
    """임시 파일에 쓴 뒤 원자적으로 rename하여, 쓰기 도중 강제종료되어도 체크포인트 파일이
    손상되거나 반쯤 쓰인 상태로 남지 않게 합니다."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, 'w') as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def batch_fetch_with_checkpoint(
    symbols: list[str],
    target_year: str,
    report_codes: list[str]
):
    """
    여러 종목과 보고서 코드를 순회하며 OpenDART 데이터를 수집합니다.
    일일 한도 초과(020 에러) 발생 시 진행 상황을 파일에 저장하고,
    다음 실행 시 중단된 지점부터 자동으로 재개(resume)합니다.
    (재무제표와 현금흐름표의 수집 상태를 별도로 추적하여 중복 호출 및 누락 방지)

    체크포인트는 종목 하나를 처리할 때마다 원자적으로(임시파일+rename) 저장되므로, 강제종료
    (SIGKILL/전원차단 등 finally가 실행되지 않는 상황)가 발생해도 그 시점까지의 진행 상황은
    보존됩니다. 또한 한 종목에서 예기치 못한 에러(020 한도초과 제외)가 발생해도 해당 종목만
    건너뛰고 나머지 종목은 계속 처리합니다 — 020은 모든 종목에 공통으로 적용되는 한도이므로
    이 경우에만 전체 배치를 중단합니다.
    """
    CHECKPOINT_FILE = DART_CACHE_DIR / "fetch_checkpoint.json"
    DART_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 체크포인트 로드
    checkpoint = {}
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, 'r') as f:
            checkpoint = json.load(f)

    fin_completed = set(checkpoint.get("fin_completed", []))
    cf_completed = set(checkpoint.get("cf_completed", []))
    shares_completed = set(checkpoint.get("shares_completed", checkpoint.get("extra_completed", [])))
    gp_completed = set(checkpoint.get("gp_completed", checkpoint.get("extra_completed", [])))
    log.info(f"Loaded checkpoint. FIN: {len(fin_completed)}, CF: {len(cf_completed)}, SHARES: {len(shares_completed)}, GP: {len(gp_completed)}")

    def _save_checkpoint():
        _atomic_write_json(CHECKPOINT_FILE, {
            "fin_completed": list(fin_completed),
            "cf_completed": list(cf_completed),
            "shares_completed": list(shares_completed),
            "gp_completed": list(gp_completed),
        })

    def _save_and_normalize(symbol, new_metrics):
        if not new_metrics:
            return
        save_dart_report(new_metrics)

        # DB에서 해당 종목의 전체 raw 데이터를 다시 읽어와서 정규화 수행
        db_path = Path(__file__).parent.parent.parent / "data" / "quant.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pead_dart_raw WHERE symbol=?", (symbol,))
        all_raw = [dict(row) for row in cursor.fetchall()]
        conn.close()

        save_normalized_report(normalize_to_quarterly(all_raw))

    try:
        for symbol in symbols:
            try:
                for report_code in report_codes:
                    key = f"{symbol}_{target_year}_{report_code}"

                    # 1. 재무제표 수집
                    if key not in fin_completed:
                        log.info(f"Fetching Financial Statements for {key}...")
                        fin_metrics = fetch_financial_statements(symbol, target_year, report_code)
                        _save_and_normalize(symbol, fin_metrics)
                        fin_completed.add(key)

                    # 2. 현금흐름표 수집
                    if key not in cf_completed:
                        log.info(f"Fetching Cash Flow for {key}...")
                        cf_metrics = fetch_cash_flow_statement(symbol, target_year, report_code)
                        _save_and_normalize(symbol, cf_metrics)
                        cf_completed.add(key)
                        
                    # 3. 주식수 수집
                    if key not in shares_completed:
                        log.info(f"Fetching Issued Shares for {key}...")
                        shares_metrics = fetch_issued_shares(symbol, target_year, report_code)
                        _save_and_normalize(symbol, shares_metrics)
                        shares_completed.add(key)
                        
                    # 4. 매출총이익 수집
                    if key not in gp_completed:
                        log.info(f"Fetching Gross Profit for {key}...")
                        gp_metrics = fetch_gross_profit(symbol, target_year, report_code)
                        _save_and_normalize(symbol, gp_metrics)
                        gp_completed.add(key)

            except Exception as e:
                if "max calls exceeded" in str(e).lower():
                    raise
                log.error(f"Skipping {symbol} after unexpected error: {e}")
            finally:
                _save_checkpoint()

    except Exception as e:
        if "max calls exceeded" in str(e).lower():
            log.warning("OpenDART daily limit reached. Saving checkpoint...")
        else:
            log.error(f"Unexpected error during batch fetch: {e}")
    finally:
        _save_checkpoint()
        log.info("Checkpoint saved.")


def normalize_to_quarterly(raw_records: list[dict]) -> list[dict]:
    """
    DART API에서 수집된 원본 YTD(누적) 데이터 리스트를 단일 분기 데이터로 환산합니다.
    환산에 사용되는 이전 분기 데이터는 반드시 현재 처리 중인 보고서의 rcept_dt 이전에 가용했던 
    가장 최신 정정본을 사용하도록 point-in-time 필터링을 거칩니다.
    
    주의: 자산총계, 자본총계, 발행주식수는 누적이 아닌 스냅샷(snapshot) 지표이므로 이전 분기값을 빼지 않습니다.
    """
    from collections import defaultdict
    
    # 누적 지표 목록 (YTD에서 분기값을 구하기 위해 이전 분기값을 빼야 하는 지표들)
    ytd_metrics = {"operating_income", "net_income", "eps", "revenue", "operating_cash_flow", "gross_profit"}
    
    # group_key: (symbol, target_year, metric, basis) -> list of records
    groups = defaultdict(list)
    for r in raw_records:
        if 'target_year' not in r:
            continue
        key = (r['symbol'], r['target_year'], r['metric'], r['basis'])
        groups[key].append(r)
        
    normalized = []
    
    report_deps = {
        "11013": None,      # 1Q (No subtraction)
        "11012": "11013",   # Half (Subtract 1Q)
        "11014": "11012",   # 3Q (Subtract Half)
        "11011": "11014"    # Annual (Subtract 3Q)
    }
    
    def _get_pit_value(records, target_code, current_rcept_dt):
        """특정 report_code에 대해 current_rcept_dt 시점에 가용했던 최신값 반환"""
        valid = [r for r in records if r['report_code'] == target_code and r['rcept_dt'] <= current_rcept_dt]
        if not valid:
            return None
        latest = max(valid, key=lambda x: x['rcept_dt'])
        return latest['value']

    for (symbol, year, metric, basis), records in groups.items():
        for r in records:
            report_code = r['report_code']
            val = r['value']
            rcept_dt = r['rcept_dt']
            
            prev_code = report_deps.get(report_code)
            
            quarterly_val = None
            is_estimable = True
            
            if prev_code is None or metric not in ytd_metrics:
                quarterly_val = val
            else:
                prev_val = _get_pit_value(records, prev_code, rcept_dt)
                if prev_val is not None:
                    quarterly_val = val - prev_val
                else:
                    is_estimable = False
                    
            normalized.append({
                "symbol": symbol,
                "target_year": year,
                "rcept_dt": rcept_dt,
                "report_code": report_code,
                "metric": metric,
                "basis": basis,
                "quarterly_value": quarterly_val,
                "is_estimable": is_estimable
            })
            
    return normalized

