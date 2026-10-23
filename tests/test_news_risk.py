import news_risk


class TestClean:
    def test_keeps_only_our_holdings_and_http_sources(self):
        report = {"summary": "s", "risks": [{
            "title": "t", "holdings": ["아마존닷컴", "모르는회사"], "why": "w",
            "severity": "high", "date": "2026-10-23",
            "sources": ["https://a.example/x", "javascript:alert(1)", "ftp://b"]}]}
        out = news_risk.clean(report, {"아마존닷컴"})
        assert out["risks"][0]["holdings"] == ["아마존닷컴"]
        assert out["risks"][0]["sources"] == ["https://a.example/x"]

    def test_caps_count_and_lengths_and_unknown_severity(self):
        risks = [{"title": "x" * 500, "holdings": [], "why": "", "severity": "extreme",
                  "date": "2026-10-23T00:00", "sources": []}] * 9
        out = news_risk.clean({"summary": "", "risks": risks}, set())
        assert len(out["risks"]) == news_risk.MAX_RISKS
        assert len(out["risks"][0]["title"]) == 120
        assert out["risks"][0]["severity"] == "low"
        assert out["risks"][0]["date"] == "2026-10-23"


class TestPrompt:
    def test_sends_names_and_shares_only(self):
        brief = [{"name": "아마존닷컴", "symbol": "AMZN", "kind": "미국 개별주 직접투자", "share": 22.7}]
        text = news_risk.prompt(brief, "2026-10-23")
        assert "아마존닷컴 (AMZN)" in text and "22.7%" in text and "원" not in text.replace("전체의", "")
