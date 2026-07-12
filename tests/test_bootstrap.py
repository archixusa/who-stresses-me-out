"""Tests for analyze.bootstrap_ci — deterministic percentile bootstrap CI of the mean."""
import analyze


def test_returns_none_for_fewer_than_two_values():
    # Arrange / Act / Assert
    assert analyze.bootstrap_ci([]) is None
    assert analyze.bootstrap_ci([42.0]) is None


def test_same_seed_and_input_is_deterministic():
    # Arrange
    values = [1.0, 2.0, 3.0, 4.0, 5.0]

    # Act
    first = analyze.bootstrap_ci(values, seed=99)
    second = analyze.bootstrap_ci(values, seed=99)

    # Assert
    assert first == second


def test_default_seed_is_also_deterministic():
    values = [10.0, 12.0, 9.0, 11.0]
    assert analyze.bootstrap_ci(values) == analyze.bootstrap_ci(values)


def test_lo_less_than_or_equal_to_hi_and_rounded_to_one_decimal():
    # Arrange
    values = [3.0, 7.0, 5.0, 9.0, 4.0]

    # Act
    lo, hi = analyze.bootstrap_ci(values, seed=7)

    # Assert
    assert lo <= hi
    assert lo == round(lo, 1)
    assert hi == round(hi, 1)


def test_tight_cluster_yields_narrower_ci_than_wide_spread():
    # Arrange
    tight = [50.0, 50.2, 49.8, 50.1, 49.9]
    wide = [10.0, 90.0, 20.0, 80.0, 50.0]

    # Act
    tlo, thi = analyze.bootstrap_ci(tight, seed=1)
    wlo, whi = analyze.bootstrap_ci(wide, seed=1)

    # Assert
    assert (thi - tlo) < (whi - wlo)
    assert (thi - tlo) < 1.0
