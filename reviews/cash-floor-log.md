# Cash floor log (`config.CASH_FLOOR`, kis-isa)

Rules: RUNBOOK.md "Cash floor". Range 10-20%, at most ±5%p per change, decided before the
month's first trading day, **this row first**, then `config.py`, committed together.

Scoring (at each review): replay the live period twice with `backtest.run_tranched` — once with the
floors below on their dates, once with a fixed 15% — and compare return, max drawdown and worst
12 months. Positive = the changes helped. Too few changes to judge before ~2 years.

| 결정일 | 적용 시작 (첫 tranche) | 이전 → 새 값 | 이유 | 예상 (무엇이 일어날 것 같은가) |
|---|---|---|---|---|
| 2026-11-22 | 2026-12-01 | 0% → 15% | 11월 재검토. 견딜 수 있는 낙폭을 줄이려고 (백테스트 2008-2025 최대 낙폭 −28.7% → 약 −24.6%, CAGR 약 −0.8%p/년) | 기준값. 비교의 출발점 |
