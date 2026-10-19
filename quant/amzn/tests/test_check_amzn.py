from quant.amzn.scripts.check_amzn import format_summary


def test_full_data_with_holding():
    msg = format_summary(
        rsi=42.0, per_pctl=18.0, pbr_pctl=22.0,
        per_samples=60, rsi_samples=60,
        holding={"qty": 5, "unrealized_pnl_pct": 3.2},
    )
    assert msg == "AMZN: 밸류 20%ile(저평가, PER18·PBR22) · RSI 42(중립) · 보유 5주 평가손익 +3.2%"


def test_insufficient_data_ramp_up():
    msg = format_summary(
        rsi=None, per_pctl=None, pbr_pctl=None,
        per_samples=9, rsi_samples=9,
        holding=None,
    )
    assert msg == "AMZN: 밸류 데이터부족(9/20일) · RSI 데이터부족(9/15일) · 보유 없음"


def test_no_holding_full_data():
    msg = format_summary(
        rsi=71.0, per_pctl=60.0, pbr_pctl=70.0,
        per_samples=90, rsi_samples=90,
        holding=None,
    )
    assert msg == "AMZN: 밸류 65%ile(중립, PER60·PBR70) · RSI 71(과매수) · 보유 없음"


def test_holdings_query_error():
    msg = format_summary(
        rsi=42.0, per_pctl=18.0, pbr_pctl=None,
        per_samples=60, rsi_samples=60,
        holding="error",
    )
    assert msg == "AMZN: 밸류 18%ile(저평가, PER18) · RSI 42(중립) · 보유조회 실패"


def test_holding_without_pnl_field():
    msg = format_summary(
        rsi=50.0, per_pctl=50.0, pbr_pctl=50.0,
        per_samples=60, rsi_samples=60,
        holding={"qty": 3, "unrealized_pnl_pct": None},
    )
    assert "보유 3주 손익정보없음" in msg


def test_only_pbr_available():
    msg = format_summary(
        rsi=None, per_pctl=None, pbr_pctl=40.0,
        per_samples=5, rsi_samples=5,
        holding=None,
    )
    assert "밸류 40%ile(중립, PBR40)" in msg


class TestCollectExtras:
    def test_news_failure_does_not_stop_sec_or_raise(self, monkeypatch, tmp_path):
        from quant.amzn.scripts import check_amzn

        def boom(*args, **kwargs):
            raise RuntimeError("kis down")

        sec_calls = []
        monkeypatch.setattr(check_amzn.storage, "DB_PATH", tmp_path / "t.db")
        monkeypatch.setattr(check_amzn.news_collector, "collect_headlines", boom)
        monkeypatch.setattr(check_amzn.segment_collector, "collect_new_filings",
                            lambda *a, **k: sec_calls.append(a) or 0)
        check_amzn.collect_extras(object(), check_amzn.AmznConfig(), object())
        assert len(sec_calls) == 1

    def test_sec_failure_does_not_raise(self, monkeypatch, tmp_path):
        from quant.amzn.scripts import check_amzn

        def boom(*args, **kwargs):
            raise RuntimeError("edgar 403")

        monkeypatch.setattr(check_amzn.storage, "DB_PATH", tmp_path / "t.db")
        monkeypatch.setattr(check_amzn.news_collector, "collect_headlines", lambda *a, **k: [])
        monkeypatch.setattr(check_amzn.segment_collector, "collect_new_filings", boom)
        check_amzn.collect_extras(object(), check_amzn.AmznConfig(), object())
