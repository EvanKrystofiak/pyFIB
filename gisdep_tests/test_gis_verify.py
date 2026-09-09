from gisdep.api.mock import MockSmartSEM
from gisdep.clock import FakeClock
from gisdep.gis.state import (
    NEEDLE_PARKED,
    NEEDLE_UNKNOWN,
    NEEDLE_INSERTED,
    VALVE_OPEN,
    VALVE_CLOSED,
)
from gisdep.gis.verify import GISStateMonitor, snapshot_text


def make():
    api = MockSmartSEM(clock=FakeClock())
    api.initialise()
    return api


def test_first_poll_is_baseline_no_changes():
    api = make()
    mon = GISStateMonitor(api)
    readings, changes = mon.poll()
    assert changes == []
    assert readings[1].is_safe and readings[2].is_safe


def test_poll_detects_needle_change():
    api = make()
    mon = GISStateMonitor(api)
    mon.poll()  # baseline
    api.set_gis_needle(1, NEEDLE_INSERTED)
    _, changes = mon.poll()
    assert len(changes) == 1
    ch = changes[0]
    assert ch.port == 1 and ch.field == "needle"
    assert ch.old_raw == NEEDLE_PARKED and ch.new_raw == NEEDLE_INSERTED


def test_poll_detects_valve_change_independent_of_port():
    api = make()
    mon = GISStateMonitor(api)
    mon.poll()
    api.set_gis_valve(2, VALVE_OPEN)
    _, changes = mon.poll()
    assert len(changes) == 1
    assert changes[0].port == 2 and changes[0].field == "valve"


def test_poll_reports_multiple_changes():
    api = make()
    mon = GISStateMonitor(api)
    mon.poll()
    api.set_gis_needle(1, NEEDLE_INSERTED)
    api.set_gis_valve(1, VALVE_OPEN)
    _, changes = mon.poll()
    fields = {(c.port, c.field) for c in changes}
    assert (1, "needle") in fields and (1, "valve") in fields


def test_no_change_between_identical_polls():
    api = make()
    mon = GISStateMonitor(api)
    mon.poll()
    _, changes = mon.poll()
    assert changes == []


def test_watch_captures_full_insert_cycle():
    # Simulate an operator driving GIS1 through a realistic transition sequence:
    # Parked -> Unknown (transit) -> Inserted, then valve Open, then Closed, then
    # Unknown (transit) -> Parked. The monitor must report each transition.
    api = make()
    mon = GISStateMonitor(api)
    clock = FakeClock()

    script = {
        1: (1, "needle", NEEDLE_UNKNOWN),
        2: (1, "needle", NEEDLE_INSERTED),
        3: (1, "valve", VALVE_OPEN),
        4: (1, "valve", VALVE_CLOSED),
        5: (1, "needle", NEEDLE_UNKNOWN),
        6: (1, "needle", NEEDLE_PARKED),
    }

    def hook(i):
        if i in script:
            port, field, value = script[i]
            if field == "needle":
                api.set_gis_needle(port, value)
            else:
                api.set_gis_valve(port, value)

    seen = []
    mon.watch(
        clock=clock, interval_s=0.5, on_change=lambda c: seen.append(c),
        hook=hook, max_iterations=8,
    )

    transitions = [(c.field, c.new_raw) for c in seen if c.port == 1]
    assert ("needle", NEEDLE_UNKNOWN) in transitions
    assert ("needle", NEEDLE_INSERTED) in transitions
    assert ("valve", VALVE_OPEN) in transitions
    assert ("valve", VALVE_CLOSED) in transitions
    assert ("needle", NEEDLE_PARKED) in transitions


def test_watch_stops_on_predicate():
    api = make()
    mon = GISStateMonitor(api)
    clock = FakeClock()
    count = {"n": 0}

    def hook(i):
        count["n"] += 1
        if i == 1:
            api.set_gis_needle(1, NEEDLE_INSERTED)

    # Stop as soon as GIS1 needle is inserted.
    mon.watch(
        clock=clock, interval_s=1.0, hook=hook,
        stop=lambda r: r[1].needle_inserted, max_iterations=50,
    )
    # Should have stopped well before max_iterations.
    assert count["n"] < 10


def test_snapshot_text_renders():
    api = make()
    mon = GISStateMonitor(api)
    readings, _ = mon.poll()
    text = snapshot_text(readings)
    assert "READ-ONLY" in text
    assert "GIS1" in text and "GIS2" in text


def test_monitor_never_executes_or_sets():
    api = make()
    mon = GISStateMonitor(api)
    mon.poll()
    api.set_gis_needle(1, NEEDLE_INSERTED)
    mon.poll()
    assert api.executed == []  # strictly read-only
