from app.analysis import analyse


def tx(sender, receiver, amount, status="SUCCESS"):
    return {"sender": sender, "receiver": receiver, "amount": float(amount), "status": status}


NAMES = {k: k.upper() for k in "abcdefgh"}


def by_id(result):
    return {a["account_id"]: a for a in result["accounts"]}


def test_quiet_history_flags_nobody():
    out = analyse([tx("a", "b", 500), tx("b", "c", 700)], [], set(), NAMES)
    assert out["accounts"] == []
    assert out["summary"]["transactions"] == 2


def test_pass_through_mule_is_found():
    history = [tx("a", "m", 80_000), tx("m", "z", 78_000)]
    out = by_id(analyse(history, [], set(), NAMES))
    assert "m" in out
    assert any("forwarded" in r for r in out["m"]["reasons"])


def test_fan_in_hub():
    history = [tx(s, "hub", 20_000) for s in "abc"]
    out = by_id(analyse(history, [], set(), NAMES))
    assert any("received money from 3" in r for r in out["hub"]["reasons"])


def test_round_trip_cycle():
    history = [tx("a", "b", 90_000), tx("b", "c", 88_000), tx("c", "a", 86_000)]
    out = analyse(history, [], set(), NAMES)
    assert out["summary"]["cycles"] >= 1
    assert {"a", "b", "c"} <= set(by_id(out))


def test_small_mutual_payments_are_not_a_cycle():
    out = analyse([tx("a", "b", 500), tx("b", "a", 450)], [], set(), NAMES)
    assert out["summary"]["cycles"] == 0


def test_neighbour_of_blocklisted_account_even_when_payment_was_blocked():
    out = by_id(analyse([tx("a", "bad", 1_000, "REJECTED")], [], {"bad"}, NAMES))
    assert any("1 step" in r for r in out["a"]["reasons"])
    assert "bad" not in out or out["bad"]["blocked"]


def test_structuring_just_under_the_ceiling():
    history = [tx("a", x, 85_000) for x in "bcd"]
    out = by_id(analyse(history, [], set(), NAMES, ceiling=100_000))
    assert any("just under" in r for r in out["a"]["reasons"])


def test_database_alerts_raise_the_score_and_score_is_capped():
    alerts = [{"sender": "a", "transaction_id": str(i), "action_taken": "BLOCK"} for i in range(6)]
    out = by_id(analyse([tx("a", "b", 10)], alerts, set(), NAMES))
    assert 0 < out["a"]["score"] <= 1.0


def test_accounts_are_sorted_riskiest_first():
    history = [tx("a", "m", 80_000), tx("m", "z", 78_000)]
    alerts = [{"sender": "q", "transaction_id": "1", "action_taken": "FLAG"}]
    scores = [a["score"] for a in analyse(history, alerts, set(), NAMES)["accounts"]]
    assert scores == sorted(scores, reverse=True)
