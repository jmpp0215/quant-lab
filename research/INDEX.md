# 연구 기록 색인

**분할**: 이 폴더의 파일들은 2026-11-25에 `quant/pead/RESEARCH_LOG.md` 한 파일을 연구 트랙별로 나눈 것이다.
분할 커밋 `d052341`, 분할 직전 커밋 `50543d4`. 내용은 옮기기만 했고, 바뀐 글자는 두 번째 `## 73.`
(C5 기여 분해 초안)의 번호를 `## 73b.`로 바꾼 한 줄뿐이다.

**분할 이전의 사전 등록 시각은 옛 경로 `quant/pead/RESEARCH_LOG.md`의 git 이력으로 확인한다.**

```bash
git log -- quant/pead/RESEARCH_LOG.md                 # 사전 등록·결과 커밋의 시각
git show 50543d4:quant/pead/RESEARCH_LOG.md        # 분할 직전의 원본 전체
```

## 트랙 파일

- [pead_early.md](pead_early.md) — PEAD(§1~§5)와 초기 개별주 팩터(§6~§9). 원래 로그의 머리말이 이 파일 맨 위에 있다
- [pbr.md](pbr.md) — 저PBR 실전 전략 (토스 PBR Top 50): PBR 팩터 검증, 실전 PBR 규칙의 근거와 변경 시도
- [sector.md](sector.md) — 업종 밸류·로테이션: 업종 PBR 보정, 업종 로테이션, 업종 간 PBR 격차
- [momentum_etf.md](momentum_etf.md) — ISA 듀얼 모멘텀 (ETF 유니버스): 모멘텀 유니버스, 선택 시점, 무거래 구간
- [fear_losers.md](fear_losers.md) — 공포 때 급락주: 극단적 공포 때 급락주 반등, 전향 기록
- [market_regime.md](market_regime.md) — 시장 국면·타이밍: 시장 폭, 밸류에이션, 정치 일정과 이후 코스피
- [value_spread.md](value_spread.md) — 가치 스프레드: 가치 스프레드와 이후 PBR 성과, 입금 배분
- [cross.md](cross.md) — 여러 트랙에 걸친 기록: 전략 조합, 실거래 대 백테스트, 일괄 재실행, 세션 요약

## 분할 후 쓰는 법

아래 "0. 이 로그를 쓰는 법"은 분할 전 원문 그대로다. 그 규칙은 모두 유지하고, 파일에 관한 부분만 이렇게 읽는다.

- 새 절은 주제가 맞는 트랙 파일의 **끝에** 쓴다. 번호는 모든 트랙 파일을 통틀어 마지막 번호에 이어 붙인다(이 색인 표의 마지막 번호 + 1).
  맞는 트랙이 없으면 새 트랙 파일을 만들고 위 목록에 추가한다.
- 새 절을 쓰면 아래 "절 색인" 표에 한 줄을, 스크립트가 있으면 "섹션별 스크립트 색인"(원문 표 또는 그 아래 추가 표)에 한 줄을 넣는다.
- 원문의 "이 파일", "맨 위의 '연구 종료'"는 분할 전 한 파일을 가리킨다. "연구 종료"는 지금 [pead_early.md](pead_early.md) 맨 위에 있다.
- 사전 등록 커밋 규칙(먼저 커밋, 결과는 같은 절 아래)은 그대로다. 분할 이후 절의 등록 시각은 해당 트랙 파일의 git 이력으로 확인한다.

## 절 색인

| § | 파일 | 제목 |
|---|---|---|
| §1 | [pead_early.md](pead_early.md) | 가설 |
| §2 | [pead_early.md](pead_early.md) | 인프라 — 재사용 가능한 자산 |
| §3 | [pead_early.md](pead_early.md) | 검증 결과 |
| §4 | [pead_early.md](pead_early.md) | 방법론적 교훈 (다음 전략에도 적용할 것) |
| §5 | [pead_early.md](pead_early.md) | 향후 활용 가능성 |
| §6 | [pead_early.md](pead_early.md) | 추가 연구: 크로스섹셔널 모멘텀 및 시장 쏠림(Narrow Market) 분석 |
| §7 | [pead_early.md](pead_early.md) | 방법론적 교훈 (중요) |
| §8 | [pead_early.md](pead_early.md) | 다음 연구 방향 |
| §9 | [pead_early.md](pead_early.md) | 포트폴리오 레벨 검증 및 최종 기각 확정 |
| §10 | [pbr.md](pbr.md) | Value (PBR) & Quality (GPA) 팩터 최종 검증 (KOSPI ALL 완전체) |
| §11 | [pbr.md](pbr.md) | 리밸런싱 주기 장기화 검증 및 기각 |
| §12 | [pbr.md](pbr.md) | PBR 정교화 시도 종합 결론 (5회 독립 실험) — 전부 기각 |
| §12.1 | [pbr.md](pbr.md) | 실전/검증 룰 도입: 거래정지(Volume=0) 가드레일 |
| §13 | [pbr.md](pbr.md) | PBR 전략 확장성(Scalability) 한계 |
| §14 | [pbr.md](pbr.md) | 관리종목 필터 세분화 검토 및 기각 |
| §15 | [pbr.md](pbr.md) | 바이오 섹터 제외 검토 및 기각 — 근본 원인은 소급 수정주가 미적용 |
| §16 | [pbr.md](pbr.md) | PBR Point-in-time 정합성 가드(격리 구간) 실전 도입 |
| §17 | [pbr.md](pbr.md) | 백테스트 인프라 함정 2건 (PBR 리스크 오버레이 검증 중 발견) |
| §18 | [pbr.md](pbr.md) | 개별 종목 손절(Stop-loss) 오버레이 검토 및 기각 |
| §19 | [pbr.md](pbr.md) | 변동성 역가중(Inverse-Vol Weighting) 검토 — 트레이드오프 확인, 실전 도입은 보류 |
| §20 | [pbr.md](pbr.md) | 포트폴리오 레벨 드로다운 서킷브레이커 검토 및 기각 |
| §21 | [pbr.md](pbr.md) | `construct_target_portfolio`가 자체 stale 데이터 가드가 없음 — 위험도 점검 (수정은 TODO로 보류) |
| §22 | [pbr.md](pbr.md) | 생존편향(Survivorship Bias) 점검 — PBR 엣지 유지, 영향 미미 |
| §23 | [pbr.md](pbr.md) | PBR 백테스트 신호의 미래 정보 편향 발견 및 정정, 현금배당 반영 (2026-10-06) |
| §24 | [pbr.md](pbr.md) | 증분 갱신 종가 오염 — 네이버 종가 기준 변경 + 장중 값 잔존 (2026-10-06) |
| §25 | [pbr.md](pbr.md) | 정정 신호로 §22 → §10·§12 → §18 재검증 (2026-10-06) |
| §26 | [pbr.md](pbr.md) | 거래대금 안정 필터 사전 등록 검증 — 기각 (2026-10-06) |
| §27 | [pbr.md](pbr.md) | pbrcheck(PBR 자동 리밸런싱) 보류와 해제 조건 (2026-10-06 결정) |
| §28 | [pbr.md](pbr.md) | 변동성 역가중 정정 재검증 (§19, 2026-10-06) |
| §29 | [pbr.md](pbr.md) | 순위 경계 종목의 체결가 문제 검토 — 밴드 기각, 가격 상한 보류·기록만 시작, `--retry` 정정 (2026-10-21) |
| §30 | [sector.md](sector.md) | PBR 업종 보정 — 사전 등록 (2026-10-23, 결과 전 기록) |
| §31 | [sector.md](sector.md) | 업종 밸류 로테이션 1차 — 사전 등록 (2026-10-23, 결과 전 기록, 연구 전용) |
| §32 | [sector.md](sector.md) | PBR 바구니 업종 급증 시 대장주 혼합 — 사례 관찰만 (2026-10-23, 설명용·미등록) |
| §33 | [sector.md](sector.md) | 싸면서 반등을 시작한 업종 — 사전 등록 (2026-10-23, 결과 전 기록, 연구 전용) |
| §34 | [sector.md](sector.md) | 업종이 평소보다 싸졌을 때 대장주를 길게 보유 — 사전 등록 (2026-10-23, 결과 전 기록, 연구 전용) |
| §35 | [cross.md](cross.md) | 두 전략의 조합 비율과 계좌 배치(세금) — 관찰 (2026-10-23, 설명용·미등록) |
| §36 | [pbr.md](pbr.md) | PBR Top 50 표본 밖 검증 2005-09 ~ 2020-12 — 사전 등록 (2026-10-23, 결과 전 기록) |
| §37 | [momentum_etf.md](momentum_etf.md) | 모멘텀 유니버스 leave-one-out — 사전 등록 (2026-10-23, 결과 전 기록, 연구 전용) |
| §38 | [fear_losers.md](fear_losers.md) | 극단적 공포 때 많이 떨어진 종목 vs 덜 떨어진 종목 — 사전 등록 (2026-10-23, 결과 전 기록, 연구 전용) |
| §39 | [fear_losers.md](fear_losers.md) | §38의 베타 조정 재검증 — 사전 등록 (2026-10-23, 결과 전 기록, 연구 전용) |
| §40 | [fear_losers.md](fear_losers.md) | §38·§39 표본 밖 확인 2021-01 ~ 2026-09 — 사전 등록 (2026-10-23, 데이터 수집 전 기록, 연구 전용) |
| §41 | [fear_losers.md](fear_losers.md) | PBR Top 50 + 공포 슬리브(급락주) 조합 — 사전 등록 (2026-10-23, 결과 전 기록, 연구 전용) |
| §42 | [fear_losers.md](fear_losers.md) | 공포 슬리브 발전: 3개월 보유·시장 대비 급락주 — 사전 등록 (2026-10-24, 결과 전 기록, 연구 전용) |
| §43 | [market_regime.md](market_regime.md) | 시장 폭 기울기(200일선 위 종목 비율의 변화)와 이후 코스피 수익률 — 사전 등록 (2026-10-24, 결과 전 기록, 연구 전용) |
| §44 | [market_regime.md](market_regime.md) | 시장 폭 분기점 — 코스닥 주 판정 — 사전 등록 (2026-10-24, 데이터 수집 중·결과 전 기록, 연구 전용) |
| §45 | [market_regime.md](market_regime.md) | 싸고 겁먹었을 때 이후 1년 코스피 — 사전 등록 (2026-10-24, 결과 전 기록, 연구 전용) |
| §46 | [value_spread.md](value_spread.md) | 가치 스프레드가 클 때 이후 PBR 전략 성과 — 사전 등록 (2026-10-24, 결과 전 기록, 연구 전용) |
| §47 | [market_regime.md](market_regime.md) | 비싼데 좁은 장세 이후 코스피 — 사전 등록 (2026-10-24, 결과 전 기록, 연구 전용, **일부 오염**) |
| §48 | [value_spread.md](value_spread.md) | 가치 스프레드로 새 입금 나누기(ISA 모멘텀 vs 토스 PBR) — 사전 등록 (2026-10-24, 결과 전 기록, 연구 전용) |
| §49 | [fear_losers.md](fear_losers.md) | 공포 때 급락주 반등의 업종 분해 — 사전 등록 (2026-10-24, 결과 전 기록, 연구 전용) |
| §50 | [fear_losers.md](fear_losers.md) | 업종 대비 급락주 — 실전 기록으로만 판정하는 전향 시험 — 사전 등록 (2026-10-24, 기록 시작 전, 연구 전용) |
| §51 | [market_regime.md](market_regime.md) | 중간선거에서 야당이 의회를 가져간 뒤 자산 흐름 — 사전 등록 (2026-10-26, 결과 전 기록, 연구 전용) |
| §52 | [pbr.md](pbr.md) | PBR Top 50에서 가치 함정(망하는 종목) 제외 — 사전 등록 (2026-10-30, 결과 전 기록) |
| §53 | [pbr.md](pbr.md) | 중장기 하락장 한가운데서 PBR Top 50 vs 현금 — 사전 등록 (2026-11-03, 결과 전 기록, 연구 전용) |
| §54 | [pbr.md](pbr.md) | PBR Top 50 + KOSPI200 선물 매도 헤지 바스켓 — 사전 등록 (2026-11-05, 결과 전 기록, 연구 전용) |
| §55 | [pbr.md](pbr.md) | PBR Top 50 중간 점검 — 바구니 안 상대 부진 종목 정리 — 사전 등록 (2026-11-17, 결과 전 기록, 연구 전용) |
| §56 | [pbr.md](pbr.md) | §11 재검증 — PBR Top 50 리밸런싱 주기 20/60/120/250일, 정정 신호 — 사전 등록 (2026-11-17, 결과 전 기록, 연구 전용) |
| §57 | [value_spread.md](value_spread.md) | 가치 스프레드 효과의 통계적 강건성(§46 재검증) — 사전 등록 (2026-11-17, 결과 전 기록, 연구 전용) |
| §58 | [value_spread.md](value_spread.md) | 가치 스프레드 확대의 경제적 원인 — 사전 등록 (2026-11-17, 결과 전 기록, 연구 전용) |
| §59 | [value_spread.md](value_spread.md) | 스프레드 국면별 PBR·모멘텀 관계 — 사전 등록 (2026-11-17, 결과 전 기록, 연구 전용) |
| §60 | [pbr.md](pbr.md) | PBR 효과의 요인 분해 + 새 선택 시험 2개 — 사전 등록 (2026-11-17, 결과 전 기록, 연구 전용) |
| §61 | [value_spread.md](value_spread.md) | §58 재시도 — 연간 ΔROE로 과잉 매도 vs ROE 하락 비교 — 사전 등록 (2026-11-17, 결과 전 기록, 연구 전용) |
| §62 | [sector.md](sector.md) | 업종 간 PBR 격차 — 구조인가, 예측 정보인가 — 사전 등록 (2026-11-17, 결과 전 기록, 연구 전용) |
| §63 | [pbr.md](pbr.md) | 저PBR 바구니의 상장폐지와 거래정지 — 관찰 (2026-11-22, 설명용·미등록) |
| §64 | [momentum_etf.md](momentum_etf.md) | 나스닥100 선택 시점과 현금 대비 기여 — 사전 등록 (2026-11-22, 결과 전 기록, 연구 전용) |
| §65 | [pbr.md](pbr.md) | 저PBR 유지 종목 소액 주문 생략 — 사전 등록 (2026-11-22, 결과 전 기록, 연구 전용) |
| §66 | [pbr.md](pbr.md) | 저PBR 유지 종목 비중 조정 허용 밴드 — 사전 등록 (2026-11-22, 결과 전 기록, 연구 전용) |
| §67 | [pbr.md](pbr.md) | 저PBR 역변동성 가중과 장기 거래정지 노출 — 사전 등록 (2026-11-22, 결과 전 기록, 연구 전용) |
| §68 | [cross.md](cross.md) | 실거래 대 백테스트 대조 — 구현 차이 분해 — 사전 등록 (2026-11-22, 결과 전 기록, 연구 전용) |
| §69 | [pbr.md](pbr.md) | 종가가 그대로인 날과 저PBR 종목의 다음 달 변동성 — 사전 등록 (2026-11-22, 결과 전 기록, 연구 전용) |
| §70 | [pbr.md](pbr.md) | PBR Top 50 경계 종목 고르기 — 46~55위 중 5개를 조건으로 — 사전 등록 (2026-11-22, 결과 전 기록, 연구 전용) |
| §71 | [pbr.md](pbr.md) | §70 후속 — 경계 5개를 변동성으로 고르기 (높은 순 / 낮은 순) — 사전 등록 (2026-11-22, §70 결과를 본 뒤 기록, 결과 전, 연구 전용) |
| §72 | [pbr.md](pbr.md) | PBR 종목 수와 리밸런싱 주기 다시 보기 — 두 데이터 문제를 고친 뒤 — 사전 등록 (2026-11-22, 결과 전 기록, 연구 전용) |
| §73 | [momentum_etf.md](momentum_etf.md) | 모멘텀 유니버스 하나씩 넣기 — 나스닥 집중을 줄일 후보 — 사전 등록 (2026-11-23, 데이터 수집 전·결과 전 기록, 연구 전용) |
| §73b | [pbr.md](pbr.md) | C5 매수·제외 기여 분해 — 사전등록 검토 초안 (2026-11-23, 승인 대기) |
| §74 | [momentum_etf.md](momentum_etf.md) | 371160(항셍테크)을 일본·금으로 바꾸기 — 비열등성 판정 — 사전 등록 (2026-11-23, §73 결과를 본 뒤 기록, 결과 전, 연구 전용) |
| §75 | [momentum_etf.md](momentum_etf.md) | 점수 차이 무거래 구간 — 보유 종목을 조금 차이로는 안 바꾸기 — 사전 등록 (2026-11-23, 결과 전 기록, 연구 전용) |
| §76 | [fear_losers.md](fear_losers.md) | 미국판 업종 대비 급락주 — S&P 500, 실전 기록으로만 판정 — 사전 등록 (2026-11-24, 기록 시작 전·결과 전, 연구 전용) |
| §77 | [momentum_etf.md](momentum_etf.md) | 꺾인 1위의 비중을 3개월 모멘텀 1위로 옮기기 (+ `correction_half` 전향 기록) — 사전 등록 (2026-11-26, 결과 전 기록, 연구 전용) |
| §78 | [pbr.md](pbr.md) | 저PBR 바구니 종목 고르기 경연 — 숨긴 구간 채점, 규칙은 `competition/RULES.md` — 사전 등록 (2026-11-26, 숨긴 구간 채점 전, 연구 전용) |
| §79 | [pbr.md](pbr.md) | 경연 1위 점수(carbon four_pillar_orth)를 실전 PBR 규칙에 얹기 — 사전 등록 (2026-11-27, 결과 전 기록, 연구 전용) |
| §80 | [pbr.md](pbr.md) | §79 T1 틸트 전향 기록 — 실전 옆에 기록만 — 사전 등록 (2026-11-27, 기록 시작 전, 연구 전용) |
| — | [pead_early.md](pead_early.md) | 결론 |
| — | [value_spread.md](value_spread.md) | 57~60 종합 — §36·§46·§48 결론 수정 (2026-11-17) |
| — | [cross.md](cross.md) | 신주 발행 정정 일괄 재실행 (2026-11-22) |
| — | [cross.md](cross.md) | §37~§48 한눈에 (2026-10-23~24 세션 요약) |
| — | [pead_early.md](pead_early.md) | [TODO] PEAD 테스트(test_signal.py) 실패 항목 기록 |

## 섹션별 스크립트 색인 — 분할 때 추가한 줄

원문 표(아래 §0) 이후에 생긴 절이다. 본문이 인용한 `scratch/` 경로다.

| 섹션 | 스크립트·데이터 |
|---|---|
| §75 | `momproxy/s75.py`, `momproxy/out/s75_*.txt` |
| §76 | `us_fear_losers/ref.py` (참고 결과만, 전방 기록 없음) |
| §77 | `momproxy/s77.py`(백테스트), `momproxy/s77_fwd.py`(전향, 2027-12), `momproxy/out/s77_*.txt` |
| §79 | `pbr_carbon79/s79.py`, `pbr_carbon79/s79_out.txt` |

## 0. 이 로그를 쓰는 법 (2026-11-22 정리)

이 파일은 PEAD 연구(§1~§5)로 시작했지만, 지금은 이 레포의 모든 연구 기록이 여기에 쌓인다. 맨 위의
"연구 종료"는 PEAD에만 해당한다. 아래 규칙은 §30 이후 실제로 지켜 온 방식을 글로 옮긴 것이다.

**순서**
1. **사전 등록**: 결과를 보기 전에 새 섹션을 쓴다. 제목은
   `## N. 주제 — 사전 등록 (YYYY-MM-DD, 결과 전 기록, 연구 전용)`으로 하고, 번호는 마지막 번호에 이어 붙인다.
   관찰만 하는 섹션이면 `관찰 (날짜, 설명용·미등록)`이라고 적는다.
2. 사전 등록 섹션을 **먼저 커밋한 뒤** 스크립트를 실행한다. 커밋 시각이 "결과 전에 정했다"는 증거다.
3. **결과는 같은 섹션 아래에** `**결과 (날짜)**`로 덧붙인다. 위에 쓴 가설, 기준, 설정은 고치지 않는다.
   판정 기준을 바꾸고 싶으면 새 번호로 다시 등록한다(예: §61은 §58 재시도).
4. **재실행**(코드 수정이나 데이터 정정 뒤)도 원래 섹션 끝에 `**재실행 (날짜, 이유·커밋)**`으로 덧붙인다.
   이전 숫자와 새 숫자를 나란히 적고, 판정이 바뀌었는지 적는다(예: §35·§37의 2026-11-21 재실행).
5. 결과가 좋아도 실거래 규칙은 바뀌지 않는다. 바꿀지는 사람이 따로 정하고, 정하면 `HANDOFF.md`에 남긴다.
6. **전방 기록**(§50, §57-F처럼 앞으로 쌓이는 실제 데이터로 판정하는 것)은 정한 표본이나 기간이 차기 전에
   결과를 보지 않는다. `--force`로 미리 보지 않는다.

**사전 등록에 적을 것**
- 질문 한 줄, 그리고 왜 지금 묻는지
- 데이터: 출처, 기간, 유니버스, 표본 안과 밖의 구분
- 방법: 신호, 진입과 청산 시점, 비용, 비교 대상(동일가중, 현금, KOSPI 등)
- **판정 기준**: 숫자로 쓴 통과, 기각, 판단 불가 조건(예: "Sharpe 차이 +0.1 이상이고 하위 기간 3개 중 2개 이상")
- 스크립트 경로와 출력 파일 경로(아래 "재현" 참고)

**자주 걸린 함정** (재실행으로 숫자가 바뀐 원인들)
- 신호를 체결일 종가로 만들고 같은 종가에 체결하면 미래 정보가 들어간다. 신호는 직전 거래일까지의
  데이터로 만든다(§23, `0563373`).
- 재무 데이터는 그 날짜에 공시된 것만 쓴다. 이전 분기의 정정 공시가 최신 분기를 덮어쓰거나, 새 분기로
  한 번 더 세어지면 안 된다(`0563373`, `19742e2`).
- 현재 상장 종목만 쓰면 생존편향이 생긴다. 상장폐지 종목을 포함한다(§22).
- 종가 데이터 출처가 바뀌거나 장중 값이 남으면 수익률이 오염된다(§24).

**재현 (이 맥 기준)**
- 연구 스크립트와 출력은 레포의 `scratch/`에 있다. 이 폴더는 **gitignore되어 커밋되지 않고, 이 맥에만
  있으며, `backups.py`의 일일 백업 대상도 아니다**(백업 대상은 `data/quant.db`, `data/pbr_live.db`뿐이다).
  이 맥의 디스크를 잃으면 아래 스크립트도 함께 잃는다.
- 새 섹션은 스크립트를 `scratch/<주제>/sNN.py`에, 출력을 `scratch/<주제>/sNN_out.txt`에 두고, 두 경로를
  섹션 본문에 적는다(§57 이후 방식).
- 재실행 전에는 이전 출력을 `scratch/<주제>/out/pre_<커밋>/` 같은 곳에 옮겨 둔다(예: `scratch/momproxy/out/pre_0563373/`).
- 실행 환경은 레포의 `.venv`(Python 3.14)이고, 레포 루트에서 실행한다.
- **2026-11-22부터 모멘텀 신호에 고정 현금 비중(`config.CASH_FLOOR` 15%)이 들어간다.** `backtest.run_tranched`도 같은 `strategy.evaluate`를 쓰므로, 그 전의 모멘텀 연구 수치를 재현하려면 스크립트에서 `config.CASH_FLOOR = Decimal("0")`으로 둔다(`momproxy/s64.py`, `nov_review/cash_weight.py`는 그렇게 고쳤다).

**섹션별 스크립트 색인** (2026-11-22 기준, 본문이 인용한 `scratch/` 경로에서 뽑았다. 모두 이 맥에 있다.)

| 섹션 | 스크립트·데이터 |
|---|---|
| §1~§16, §21, §27 | 본문에 `scratch/` 경로가 없다. 레포 안 코드(`quant/pead/`, `quant/stock_factors/`)로 돌렸거나 경로를 남기지 않았다. |
| §17, §20 | `run_pbr_circuit_breaker_test.py`, `run_pbr_stoploss_test.py` |
| §18 | `run_pbr_stoploss_test.py` |
| §19 | `run_pbr_invvol_test.py`, `run_pbr_invvol_robustness.py` |
| §22 | `survivorship_backfill.py`, `survivorship_classify.py`, `survivorship_sim.py`, DB `_survivorship.db` |
| §23 | `pbr_dividend_sim.py` |
| §24 | `recollect_prices.py` |
| §25 | `reverify.py`, 로그 `_reverify_*.log` |
| §26 | `cv_filter_validation.py`, 로그 `_cv_validation.log` |
| §28 | `reverify_invvol.py`, 로그 `_reverify_invvol.log` |
| §29 | `pbr_band_test.py`, `pbr_boundary_cap_check.py`, `pbr_boundary_margin.py`, `pbr_retry_sim.py` |
| §30, §32 | `pbr_sector/` (`describe.py`, `sectors.py`, `validate.py`, `leader_events.py`) |
| §31, §33, §34 | `sector_pbr/` (`validate.py`, `validate_cm.py`, `leaders_long.py`, 데이터 `raw/`) |
| §35 | `combo/value_momentum.py`, 곡선 `_out_pbr_baseline_equity.csv`(§23 정정 전 신호, `HANDOFF.md` 3절 참고) |
| §36 | `pbr_oos/validate.py`, 데이터 `pbr_oos/data/` |
| §37 | `momproxy/build.py`, `momproxy/loo.py` |
| §38~§42, §49, §50 | `fear_reversal/` (`validate.py`, `beta.py`, `fetch_oos.py`, `combo.py`, `combo2.py`, `sector_split.py`, `s50_reference.py`) |
| §43~§48, §53, §54 | `market_map/` (`build.py`, `breadth_slope.py`, `fetch_kosdaq.py`, `stage2.py`, `deposit_rule.py`, `s53.py`, `s54.py`) |
| §51 | `midterm/s51.py` |
| §52 | `value_trap/validate.py` |
| §55 | `midcheck/validate.py` |
| §56 | `rebal_period/validate.py` |
| §57~§59, §61 | `value_spread/s57.py`, `s58.py`, `s59.py`, `s61.py` (출력 `sNN_out.txt`) |
| §60 | `pbr_factors/s60.py` |
| §62 | `sector_rel/fetch.py`, `sector_rel/s62.py` |
| §63 | `pbr_delist/s63.py`, `pbr_delist/s63_out.txt` |
| §64 | `momproxy/s64.py`, `momproxy/s64_out.txt` (A는 그대로, B는 `LONG_HISTORY=1`) |
| §65 | `pbr_small_orders/s65.py`, `pbr_small_orders/s65_out.txt`, 회차별 차이 장부 `YYYY-MM-DD.json` (전방 판정 중단) |
| §66 | `pbr_band66/s66.py`, `pbr_band66/s66_out.txt` |
| §67 | `pbr_delist/s67.py`, `pbr_delist/s67_out.txt` (§63 스크립트를 불러와 다시 씀) |
| §68 | `live_vs_bt/s68.py`, `live_vs_bt/s68_out.txt` |
| §69 | `pbr_flat_close/s69.py`, `pbr_flat_close/s69_out.txt` |
| §70 | `pbr_boundary70/s70.py`, `pbr_boundary70/s70_out.txt` |
| §71 | `pbr_boundary70/s70.py` (C5·C6), `pbr_boundary70/s71_out.txt` |
| §72 | `pbr_size_period72/s72.py`, `pbr_size_period72/s72_out.txt` |
| §73 | `momproxy/fetch_s73.py`, `momproxy/s73.py`, `momproxy/out/s73_*.txt` |
| §74 | `momproxy/s74.py`, `momproxy/out/s74_*.txt` |

경로는 모두 `scratch/` 기준이다. 새 섹션을 쓰면 이 표에 한 줄을 추가한다.
