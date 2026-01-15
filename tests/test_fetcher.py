from datetime import datetime

from fetcher import _parse_departure_datetime, filter_train_codes_by_day


def test_parse_departure_datetime_accepts_date_and_datetime() -> None:
    assert _parse_departure_datetime("2024-01-02") == datetime(2024, 1, 2)
    assert _parse_departure_datetime("2024-01-02 03:04:05") == datetime(2024, 1, 2, 3, 4, 5)
    assert _parse_departure_datetime("") is None


def test_filter_train_codes_by_day_prefers_range() -> None:
    rows = [
        {"departure_date": "2024-06-01 08:00:00", "real_train_code": "T1"},
        {"departure_date": "2024-06-02 08:00:00", "real_train_code": "T2"},
        {"departure_date": "2024-06-03 08:00:00", "real_train_code": "T1"},
    ]
    codes = filter_train_codes_by_day(
        rows,
        "2024-06-01",
        departureDateStart="2024-06-01 00:00:00",
        departureDateEnd="2024-06-02 23:59:59",
    )
    assert codes == ["T1", "T2"]


def test_filter_train_codes_by_day_falls_back_to_day() -> None:
    rows = [
        {"departure_date": "2024-06-01", "real_train_code": "T1"},
        {"departure_date": "2024-06-01 12:00:00", "real_train_code": "T2"},
        {"departure_date": "2024-06-02", "real_train_code": "T3"},
    ]
    codes = filter_train_codes_by_day(rows, "2024-06-01")
    assert codes == ["T1", "T2"]
