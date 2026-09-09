from elymaker.units import fmt_num, parse_suffixed, fmt_suffixed


def test_fmt_scientific_three_digit_exponent():
    assert fmt_num(1.5e-8) == "1.5e-008"
    assert fmt_num(7e-10) == "7e-010"
    assert fmt_num(2e-6) == "2e-006"
    assert fmt_num(6e-5) == "6e-005"
    assert fmt_num(2.5e-8) == "2.5e-008"


def test_fmt_decimal_passthrough():
    assert fmt_num(617.797) == "617.797"
    assert fmt_num(-6.79084) == "-6.79084"
    assert fmt_num(180.0) == "180"
    assert fmt_num(0.0) == "0"
    assert fmt_num(20.0) == "20"


def test_fmt_positive_exponent_signed():
    assert fmt_num(1e5) == "100000"       # %g keeps this decimal
    assert fmt_num(1e7) == "1e+007"       # %g switches to sci here


def test_parse_suffixed():
    assert parse_suffixed("1.5e-008 m") == 1.5e-8
    assert parse_suffixed("180 deg") == 180.0
    assert parse_suffixed("7e-010 A") == 7e-10
    assert parse_suffixed("42") == 42.0
    assert parse_suffixed("  -6.79084  ") == -6.79084


def test_fmt_suffixed():
    assert fmt_suffixed(1.5e-8, "A") == "1.5e-008 A"
    assert fmt_suffixed(180.0, "deg") == "180 deg"
    assert fmt_suffixed(2e-6, "m") == "2e-006 m"
