def test_insights(client, seeded):
    buys = client.get("/api/insights/buy-low").json()
    assert buys["results"][0]["player"]["id"] == seeded["buy"] and buys["results"][0]["reasons"]
    sells = client.get("/api/insights/sell-high", params={"position": "RB", "limit": 1}).json()["results"]
    assert [s["player"]["id"] for s in sells] == [seeded["sell"]]
    assert client.get("/api/insights/buy-low", params={"position": "K"}).status_code == 422


def test_trade_evaluate(client, seeded):
    body = client.post("/api/trade/evaluate", json={"give": [seeded["sell"]], "get": [seeded["buy"]],
                                                    "settings": {"scoring": "std"}}).json()
    assert body["verdict"] in {"fair", "slight_win", "slight_loss", "big_win", "big_loss"}
    assert body["give"]["players"][0]["player"]["id"] == seeded["sell"] and body["weeks_remaining"] == 12
    assert client.post("/api/trade/evaluate", json={"give": [], "get": [1]}).status_code == 422
    same = client.post("/api/trade/evaluate", json={"give": [seeded["buy"]], "get": [seeded["buy"]]})
    assert same.status_code == 422 and "both sides" in same.json()["detail"]
    unknown = client.post("/api/trade/evaluate", json={"give": [999999], "get": [seeded["buy"]]})
    assert unknown.status_code == 422 and "999999" in unknown.json()["detail"]
    bad_settings = client.post("/api/trade/evaluate", json={"give": [seeded["buy"]], "get": [seeded["sell"]],
                                                            "settings": {"teams": 40}})
    assert bad_settings.status_code == 422
    duplicated = client.post("/api/trade/evaluate", json={"give": [seeded["sell"]], "get": [seeded["buy"]] * 3})
    assert duplicated.status_code == 422


def test_player_profile_includes_signals(client, seeded):
    signals = client.get(f"/api/players/{seeded['sell']}").json()["signals"]
    assert signals["sell_high"] is True and signals["reasons"]
