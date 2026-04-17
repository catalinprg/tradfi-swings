from src import market_context


def test_fetch_handles_all_failures(monkeypatch):
    monkeypatch.setattr(market_context, "_latest_and_change", lambda *_a, **_k: None)
    result = market_context.fetch()
    assert result == {
        "vix": None,
        "dxy": None,
        "partial": True,
        "missing": ["vix", "dxy"],
    }


def test_fetch_handles_partial_vix_only(monkeypatch):
    def fake(yf_sym, av_sym):
        if yf_sym == market_context.VIX_SYMBOL:
            return {"value": 17.4, "change_24h_pct": -2.1, "source": "yfinance"}
        return None
    monkeypatch.setattr(market_context, "_latest_and_change", fake)
    result = market_context.fetch()
    assert result["vix"]["value"] == 17.4
    assert result["vix"]["source"] == "yfinance"
    assert result["dxy"] is None
    assert result["partial"] is True
    assert result["missing"] == ["dxy"]


def test_fetch_handles_happy_path(monkeypatch):
    def fake(yf_sym, av_sym):
        if yf_sym == market_context.VIX_SYMBOL:
            return {"value": 17.4, "change_24h_pct": -2.1, "source": "yfinance"}
        return {"value": 104.2, "change_24h_pct": 0.3, "source": "yfinance"}
    monkeypatch.setattr(market_context, "_latest_and_change", fake)
    result = market_context.fetch()
    assert result["vix"]["value"] == 17.4
    assert result["dxy"]["value"] == 104.2
    assert result["partial"] is False
    assert result["missing"] == []


def test_latest_and_change_uses_alphavantage_when_yfinance_empty(monkeypatch):
    monkeypatch.setattr(market_context, "_yfinance_latest_and_change", lambda s: None)
    monkeypatch.setattr(
        market_context,
        "_alphavantage_latest_and_change",
        lambda s: {"value": 20.0, "change_24h_pct": 1.5, "source": "alphavantage"},
    )
    result = market_context._latest_and_change("^VIX", "VIXY")
    assert result["source"] == "alphavantage"


def test_latest_and_change_skips_alphavantage_when_yfinance_succeeds(monkeypatch):
    monkeypatch.setattr(
        market_context,
        "_yfinance_latest_and_change",
        lambda s: {"value": 17.0, "change_24h_pct": 0.1, "source": "yfinance"},
    )
    called = []
    monkeypatch.setattr(
        market_context,
        "_alphavantage_latest_and_change",
        lambda s: called.append(s) or None,
    )
    result = market_context._latest_and_change("^VIX", "VIXY")
    assert result["source"] == "yfinance"
    assert called == []
