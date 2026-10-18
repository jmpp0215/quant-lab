---
name: prediction
description: Record the user's market/economic forecast ("내 전망", "예측", "이렇게 될 것 같다", "기록해줘") after discussing it against current news, or review/score past forecasts ("전망 점검", "채점", "내 예측 어땠어"). Use whenever the user states a view about where markets, companies, rates or the economy are heading and wants it kept, or asks how their past calls did.
---

# Prediction journal

The user wants to measure their own market intuition. Each forecast becomes one
file in `journal/predictions/`, written **before** the outcome is known, and is
judged later against the condition fixed at recording time. The point is
honest measurement, so the rules below favour fidelity over polish.

The repo is public on GitHub; the user chose to keep the journal here anyway
(2026-10-19).

## Recording a forecast

1. **Keep the user's words.** Quote their statement verbatim (Korean as written,
   typos included) before rephrasing anything. The record measures *their*
   intuition, not Claude's restatement of it.
2. **Check the present.** Search news (WebSearch, `standard` first, `extended` for
   fresh or niche events) for the current state of what the forecast is about.
   Open the pages you take numbers from; cite them.
3. **Discuss.** Tell the user, briefly: what supports the view, what cuts against
   it, what is already priced in or already happened, and the strongest
   alternative mechanism. Claude's own view goes in its own section — never blend
   it into the user's claim.
4. **Make it checkable.** Agree with the user on:
   - the claim in one sentence, and the mechanism they expect (scored separately)
   - an observable resolution condition (a price, spread, yield, event, filing —
     something a third party could check), and a resolve-by date
   - the user's confidence as a percentage — ask; never fill it in for them
   If the user won't fix a condition or date, record it anyway with
   `scored: false` and say why.
5. **Write the file** from the template below, then `git add` it, commit
   (`journal: <slug>`), and push.

A forecast stated *before* today but recorded only after the outcome is visible
gets `scored: false` (`after_the_fact: true`) — hindsight inflates hit rates.

## Reviewing / scoring

- Never edit the sections written at recording time. Append under `## 판정`
  (dated), and update only the frontmatter fields `status`, `outcome`,
  `mechanism`, `resolved`.
- For each open entry past its `resolve_by` (or when the user asks), check news
  against the recorded condition, propose a verdict with sources, and let the
  user confirm.
- `outcome`: `hit` / `miss` / `partial` / `void` (condition became untestable).
  `mechanism`: `right` / `partly` / `wrong` / `unknown` — the user cares whether
  they were right for the right reason.
- Tally only `scored: true` entries: hit rate overall, and calibration by
  confidence bucket (e.g. 50–60%, 60–70%, …: stated confidence vs actual hit
  rate). With few entries, say the sample is too small rather than drawing
  conclusions.

## Template

File: `journal/predictions/YYYY-MM-DD-<short-slug>.md`

```markdown
---
recorded: YYYY-MM-DD
stated: YYYY-MM-DD            # when the user first held the view, if earlier and known
resolve_by: YYYY-MM-DD
confidence: 70                # user's own %, required when scored
scored: true
after_the_fact: false
status: open                  # open | resolved
outcome:                      # hit | miss | partial | void
mechanism:                    # right | partly | wrong | unknown
resolved:                     # YYYY-MM-DD
---

# <한 줄 제목>

## 원문
> 사용자 발언 그대로

## 예측
- **주장:** …
- **기대 경로(메커니즘):** …
- **판정 조건:** … (누가 봐도 확인 가능한 관측값)
- **기한:** YYYY-MM-DD
- **확신도:** NN%

## 기록 시점 상황
뉴스·수치 요약, 출처 링크.

## 논의
찬성 근거, 반대 근거, 대안 경로. Claude 의견은 "Claude:"로 구분.

## 판정
(나중에 날짜와 함께 덧붙임)
```
