---
name: kis-api-cheatsheet
description: >-
  Use this skill when integrating, debugging, or modifying the KIS (Korea Investment & Securities) Open API, especially for overseas stock balance, total assets, and cash inquiries.
---

# KIS API Cheatsheet

## Overseas Balance & Cash Inquiry
- **Endpoint to Use:** Use `CTRP6504R` (해외주식 체결기준현재잔고) to retrieve both stock positions and the cash balance in a single call.
- **Do NOT Use:** `TTTS3012R` (해외주식 잔고). It only provides position evaluations and entirely lacks cash (예수금) data.
- **Key Parameters for CTRP6504R:**
  - `WCRC_FRCR_DVSN_CD`: `"02"` (Returns stock position prices in USD)
  - `TR_MKET_CD`: `"00"` (Retrieves all markets, including NASD and NYSE)

## Data Inconsistencies & Quirks
- **Deceptive Field Names:** KIS API field names are highly inconsistent across endpoints.
  - *Example:* `tot_evlu_pfls_amt` means "Total Evaluation Amount" in `TTTS3012R`, but exactly the same field means "Total Profit/Loss" in `CTRP6504R`.
  - **Rule of Thumb:** Never blindly trust a field name without performing a mathematical sanity check against the raw response data.
- **Total Asset vs Cash Calculation:**
  - `output3.tot_asst_amt` (Total Assets) is provided in **KRW** and accurately reflects unsettled same-day trades.
  - To calculate cash reliably, do NOT use the raw cash field (`frcr_dncl_amt_2`). Instead, derive it to match the total: `Cash (KRW) = tot_asst_amt - evlu_amt_smtl_amt` (Total Assets - Stock Evaluation Amount).
- **Position Quantities:** Use `ccld_qty_smtl1` (체결수량합계) which includes today's executions, rather than `cblc_qty13` (전일잔고).
