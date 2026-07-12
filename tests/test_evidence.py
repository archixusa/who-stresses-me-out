"""Tests for analyze.evidence_level — sample size / coverage / CI / confounder mapping."""
import analyze
import config


def test_too_few_samples_is_insufficient():
    # n < 3 -> INSUFFICIENT regardless of a clean CI
    assert analyze.evidence_level(2, 1.0, (5.0, 10.0), 0.0) == analyze.INSUFFICIENT


def test_low_coverage_is_insufficient():
    # coverage < MIN_COVERAGE -> INSUFFICIENT even with a large n
    low = config.MIN_COVERAGE - 0.1
    assert analyze.evidence_level(10, low, (5.0, 10.0), 0.0) == analyze.INSUFFICIENT


def test_small_sample_is_weak():
    # 3..4 samples with good coverage -> WEAK
    assert analyze.evidence_level(3, 1.0, (2.0, 5.0), 0.0) == analyze.WEAK
    assert analyze.evidence_level(4, 1.0, (2.0, 5.0), 0.0) == analyze.WEAK


def test_consistent_when_ci_excludes_zero_and_narrow_and_unconfounded():
    # n>=5, narrow CI that excludes 0, low confounding -> CONSISTENT
    hi = 2.0 + config.WIDE_CI_BPM * 0.25  # width < WIDE_CI_BPM
    assert analyze.evidence_level(5, 1.0, (2.0, hi), 0.0) == analyze.CONSISTENT


def test_emerging_when_ci_includes_zero():
    # n>=5, narrow CI that spans 0 -> EMERGING
    edge = config.WIDE_CI_BPM * 0.2
    assert analyze.evidence_level(5, 1.0, (-edge, edge), 0.0) == analyze.EMERGING


def test_heavy_confounding_caps_consistent_down_to_weak():
    # Same input that would be CONSISTENT, but heavy confounding caps it to WEAK
    hi = 2.0 + config.WIDE_CI_BPM * 0.25
    heavy = config.CONFOUNDER_FRAC + 0.1
    assert analyze.evidence_level(5, 1.0, (2.0, hi), heavy) == analyze.WEAK


def test_wide_ci_is_weak():
    # A CI wider than WIDE_CI_BPM downgrades to WEAK even with n>=5
    wide = (0.5, 0.5 + config.WIDE_CI_BPM + 5)
    assert analyze.evidence_level(6, 1.0, wide, 0.0) == analyze.WEAK
