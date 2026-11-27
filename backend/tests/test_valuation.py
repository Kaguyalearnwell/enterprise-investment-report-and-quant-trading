"""Stage 7.2 valuation tools: closed-form checks (illustrative inputs, no company data)."""
import math

import pytest

from backend.valuation.models import (DCFInput, dcf, historical_percentile, margin_of_safety, reverse_dcf_growth,
                                      reverse_dcf_margin, scenario_value)


def base(**kw):
    d = dict(revenue0=1000.0, growth=[0.05] * 5, operating_margin=[0.2] * 5, tax_rate=0.2, reinvestment_rate=[0.05] * 5,
             wacc=0.09, terminal_growth=0.02, net_cash=100.0, shares=10.0)
    d.update(kw)
    return DCFInput(**d)


def test_dcf_equals_gordon_when_growth_is_constant():
    g, r = 0.02, 0.09
    x = base(growth=[g] * 5, terminal_growth=g, net_cash=0.0)
    fcff1 = 1000 * (1 + g) * (0.2 * 0.8 - 0.05)
    assert dcf(x)["enterprise_value"] == pytest.approx(fcff1 / (r - g), rel=1e-12)


def test_reverse_dcf_recovers_inputs():
    x = base()
    price = dcf(x)["value_per_share"]
    assert reverse_dcf_growth(price, x) == pytest.approx(0.05, abs=1e-8)
    assert reverse_dcf_margin(price, x) == pytest.approx(0.2, abs=1e-8)
    assert math.isnan(reverse_dcf_growth(1e9, x))        # nothing in range justifies an absurd price


def test_validation_errors():
    with pytest.raises(ValueError):
        dcf(base(wacc=0.02, terminal_growth=0.03))
    with pytest.raises(ValueError):
        dcf(base(growth=[0.05] * 4))


def test_scenarios_margin_and_percentile():
    s = scenario_value({"bear": (80.0, 0.25), "base": (100.0, 0.5), "bull": (130.0, 0.25)})
    assert s["weighted_value"] == pytest.approx(102.5)
    with pytest.raises(ValueError):
        scenario_value({"bear": (80.0, 0.5), "bull": (130.0, 0.4)})
    assert margin_of_safety(100.0, 80.0)["discount_to_fair_value"] == pytest.approx(0.2)
    assert historical_percentile([10.0] * 100, 12.0) is None          # less than 3 years: no percentile
    hist = [float(i % 30 + 5) for i in range(252 * 3)]
    assert historical_percentile(hist, 20.0) == pytest.approx(sum(v <= 20 for v in hist) / len(hist))
