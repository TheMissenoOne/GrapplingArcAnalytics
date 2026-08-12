from decision_vision.common import parse_timestamp


def test_parse_timestamp() -> None:
    assert parse_timestamp(12) == 12.0
    assert parse_timestamp("01:30") == 90.0
    assert parse_timestamp("1:02:03") == 3723.0
    assert parse_timestamp("1m23.5s") == 83.5
    assert parse_timestamp(None) is None
