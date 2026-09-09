from gisdep.clock import FakeClock, RealClock


def test_fake_clock_advances_on_sleep():
    c = FakeClock(start=100.0)
    assert c.now() == 100.0
    c.sleep(2.5)
    assert c.now() == 102.5
    assert c.sleeps == [2.5]


def test_fake_clock_negative_sleep_is_zero():
    c = FakeClock()
    c.sleep(-5)
    assert c.now() == 0.0
    assert c.sleeps == [0.0]


def test_fake_clock_advance_does_not_record():
    c = FakeClock()
    c.advance(3.0)
    assert c.now() == 3.0
    assert c.sleeps == []


def test_real_clock_monotonic_nonzero():
    c = RealClock()
    a = c.now()
    c.sleep(0)  # no-op
    b = c.now()
    assert b >= a
