from src import market_context


def test_fetch_handles_all_failures(monkeypatch):
    monkeypatch.setattr(market_context, "_latest_and_change", lambda *_: None)
    result = market_context.fetch()
    assert result == {
        "vix": None,
        "dxy": None,
        "partial": True,
        "missing": ["vix", "dxy"],
    }


def test_fetch_handles_partial_vix_only(monkeypatch):
    def fake(sym):
        if sym == market_context.VIX_SYMBOL:
            return {"value": 17.4, "change_24h_pct": -2.1}
        return None
    monkeypatch.setattr(market_context, "_latest_and_change", fake)
    result = market_context.fetch()
    assert result["vix"] == {"value": 17.4, "change_24h_pct": -2.1}
    assert result["dxy"] is None
    assert result["partial"] is True
    assert result["missing"] == ["dxy"]


def test_fetch_handles_happy_path(monkeypatch):
    monkeypatch.setattr(
        market_context, "_latest_and_change",
        lambda sym: {"value": 17.4, "change_24h_pct": -2.1}
        if sym == market_context.VIX_SYMBOL
        else {"value": 104.2, "change_24h_pct": 0.3},
    )
    result = market_context.fetch()
    assert result["vix"] == {"value": 17.4, "change_24h_pct": -2.1}
    assert result["dxy"] == {"value": 104.2, "change_24h_pct": 0.3}
    assert result["partial"] is False
    assert result["missing"] == []
