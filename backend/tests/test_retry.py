from app.retry import backoff_delay


def test_delay_stays_inside_the_exponential_ceiling():
    for attempt, ceiling in [(1, 0.5), (2, 1.0), (3, 2.0), (4, 3.0), (10, 3.0)]:
        for _ in range(200):
            assert 0 <= backoff_delay(attempt) <= ceiling


def test_delay_is_jittered():
    assert len({round(backoff_delay(3), 6) for _ in range(50)}) > 1
