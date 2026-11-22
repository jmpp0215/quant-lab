---
recorded: 2026-11-22
stated: 2026-11-22
resolve_by: 2027-03-31
confidence: 70
scored: true
after_the_fact: false
status: open                  # open | resolved
outcome:                      # hit | miss | partial | void
mechanism:                    # right | partly | wrong | unknown
resolved:                     # YYYY-MM-DD
---

# 미국 연준이 2027년 3월 말까지 기준금리를 올린다

## 원문
> 지금 곧 미국 금리가 오를 것 같아서 그랬어

> 그거 남겨줘. + 한국 금리도 올릴거다로 같이 해줘 한국 금리 미국 금리 둘 다 70퍼 예측. 빅스텝은 30퍼

(맥락: 모멘텀 ISA의 고정 현금 15%를 현금(497880)에 둘지 미국 30년 국채(484790)에 둘지 정하던 중. 현금으로 정했다. `reviews/2026-11-review.md` 2절)

## 예측
- **주장:** 연준이 2026-11-22 이후 2027-03-31까지 열리는 FOMC에서 연방기금금리 목표 범위를 한 번 이상 올린다.
- **기대 경로(메커니즘):** 사용자가 따로 밝히지 않았다. 판정 때 `mechanism: unknown`으로 둔다.
- **판정 조건:** 연준 FOMC 성명(federalreserve.gov press releases)에서 2026-11-23 ~ 2027-03-31 사이 목표 범위 상단이 지금(4.00%)보다
  높아지면 hit. 인상 없이 기한이 지나면 miss.
- **기한:** 2027-03-31
- **확신도:** 70%

## 기록 시점 상황
- 목표 범위 3.75~4.00%. 2026-09-16에 0.25%p 올렸고(12-0), 10-28에는 동결했다.
  [FOMC 성명 2026-09-16](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm)
- 10월 회의록(11-18 공개): 거의 모든 참석자가 인플레이션을 2%로 되돌리려면 추가 인상이 적절할 것이라고 봤다. 10월에 인상을 주장한
  소수의견(해맥, 쿡)도 있었다. 9월 점도표는 2026년 한 차례 추가 인상 뒤 2027년 동결.
  [Yahoo Finance, 2026-11-19](https://finance.yahoo.com/economy/policy/article/fed-minutes-officials-favored-holding-rates-steady-in-october-but-they-see-hikes-ahead-195719301.html)
- 다음 회의 12-08~09. 12월 인상 확률의 시장 가격은 읽은 기사에서 확인하지 못했다.

## 논의
- 찬성: 회의록이 인상 쪽으로 기울어 있고, 점도표도 한 번 더 인상을 가리킨다. 기한 안에 회의가 세 번(12월, 1월, 3월) 있다.
- 반대: 이미 시장과 연준이 같은 방향을 말하고 있어, 틀릴 경로는 "물가 둔화나 경기·금융 충격으로 인상을 건너뛰는 것"이다.
  KOSPI가 6월 고점 대비 크게 하락한 것처럼 위험자산 조정이 미국으로 번지면 연준이 멈출 수 있다.
- Claude: 회의록과 점도표가 같은 방향이라 70%는 합리적이거나 조금 낮게 잡은 쪽으로 보인다. 이 예측의 정보량은 "컨센서스를 따랐을 때 얼마나
  맞나"에 가깝다.

## 판정
