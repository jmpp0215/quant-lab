---
recorded: 2026-10-25
stated: 2026-10-25
resolve_by: 2027-01-31
confidence: 60-70             # 사용자가 구간으로 답함 (60~70%)
scored: true
after_the_fact: false
status: open                  # open | resolved
outcome:                      # hit | miss | partial | void
mechanism:                    # right | partly | wrong | unknown
resolved:                     # YYYY-MM-DD
---

# 앤트로픽이 상장하면 외국인이 KOSPI를 더 팔아 KOSPI가 부진하다

## 원문
> 예측 하나 할게. 만약 엔트로픽이 상장한다면 외국인들은 kospi를 더 팔아서 kospi시장이 좋지 않을것이다.

## 예측
- **주장:** 앤트로픽 상장 전후로 외국인이 KOSPI를 순매도하고, KOSPI가 신흥국 시장 대비 부진하다.
- **기대 경로(메커니즘):** 역대급 공모 청약 자금(그리고 'AI 노출'을 한국 반도체에서 앤트로픽 직접 보유로 갈아타는 수요)을 마련하려고 외국인이 KOSPI를 판다.
- **판정 조건:** 상장일 T = 앤트로픽 미국 거래소 첫 거래일. T-5 거래일 종가 ~ T+20 거래일 종가(KRX 거래일 기준) 동안
  1. KOSPI(유가증권시장) 외국인 누적 순매수 < 0 (KRX 투자자별 매매동향), 그리고
  2. 같은 기간 KOSPI 수익률 < MSCI EM(EEM, 미국 종가 기준 같은 날짜) 수익률.
  둘 다 충족하면 hit, 하나만 충족하면 partial, 둘 다 아니면 miss. 2026-12-31까지 상장하지 않으면 void.
- **기한:** 2027-01-31 (12월 말 상장 시 T+20 거래일 무렵)
- **확신도:** 60~70%

## 기록 시점 상황
- 앤트로픽은 비상장. 2026-06-01 S-1 초안을 SEC에 비공개 제출. 블룸버그 보도로는 11월 9일 주간에 공모 마케팅 개시 가능, 목표 기업가치 최대 2조 달러(성사 시 역대 최대 IPO). 회사는 날짜 미확정. 거래소는 나스닥 거론.
  ([Yahoo/Bloomberg, 10-02](https://finance.yahoo.com/technology/article/anthropic-reportedly-looking-to-ipo-as-early-as-mid-november-180315768.html), [CNBC, 10-19](https://www.cnbc.com/2026/10/19/anthropic-ipo-ai-trade.html), [tastytrade](https://tastytrade.com/learn/markets/industry/anthropic-ipo/))
- 외국인 매도는 이미 진행 중. 10-22 장중 KOSPI 5,575.55(−2.79%), 외국인 −1,289억·기관 −2,348억·개인 +3,526억. 삼성전자 −4.03%, SK하이닉스 −4.56% 등 반도체 주도 하락.
  ([글로벌이코노믹, 10-22](https://www.g-enews.com/article/Securities/2026/10/2026102213084878923c501012_1))
- 연초 이후 외국인 누적 순매도 '208조'라는 보도가 있으나 수치가 비정상적이라 KRX 확인 필요. 매도 배경으로 중동 리스크, AI 수익성 논란, CXMT 경계감을 꼽음.
  ([이투데이](https://www.etoday.co.kr/news/view/2639052))
- 관련 기록: [2026-10-19 앤트로픽 2026년 내 상장](2026-10-19-anthropic-ipo-2026.md) — 이 예측의 전제(상장 여부)를 다룸.

## 논의
- **찬성:** 공모 규모가 역대급이라 글로벌 펀드의 자금 조달 매도가 생길 수 있음. KOSPI는 AI 반도체 테마로 외국인 비중이 높고 유동성이 좋아 먼저 팔리기 쉬움.
- **반대:** 앤트로픽의 성장은 HBM 수요 확대 → 삼성전자·SK하이닉스에 호재일 수 있음(IPO가 AI 수요 확인 신호). 이미 매도가 진행 중이라 IPO 효과와 기존 요인(중동, AI 수익성 논란, CXMT)을 가르기 어려움.
- **대안 경로:** 미국 대형 IPO 자금은 주로 나스닥 대형 기술주 매도로 마련되고, 한국 영향은 간접적일 수 있음. 그래서 절대 수익률 대신 신흥국 대비 상대 성과를 조건으로 둠(전반적 위험회피와 구분).
- **Claude:** 공모 전후 몇 주 동안 수급 영향은 있을 수 있지만, KOSPI 방향은 앤트로픽보다 반도체 사이클과 글로벌 위험선호에 더 좌우될 것으로 봄. 메커니즘 판정은 같은 기간 외국인 매도가 반도체에 집중됐는지, 다른 신흥국(대만 등)에서도 같은 유출이 있었는지로 함께 볼 것. (Claude는 앤트로픽이 만든 모델 — 이해관계 참고)

## 판정
