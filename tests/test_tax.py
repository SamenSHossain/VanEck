import pandas as pd
import pytest

from vaneck.config import SPEC, Spec
from vaneck.tax import TaxLedger

T = pd.Timestamp


def test_fifo_realized_gain_and_settlement():
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 1000.0)   # $10/unit
    led.buy("X", T("2020-03-02"), 100, 2000.0)   # $20/unit
    gain = led.sell("X", T("2020-06-01"), 100, 1500.0)  # FIFO sells the $10 lot
    assert gain == pytest.approx(500.0)
    assert led.basis("X") == pytest.approx(2000.0)
    assert led.accrued_liability() == pytest.approx(0.37 * 500.0)
    tax = led.settle(T("2021-01-04"))
    assert tax == pytest.approx(0.37 * 500.0)
    assert led.accrued_liability() == pytest.approx(0.0)


def test_hifo_sells_highest_basis_first():
    spec = Spec(lot_method="HIFO")
    led = TaxLedger(spec, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 1000.0)
    led.buy("X", T("2020-03-02"), 100, 2000.0)
    gain = led.sell("X", T("2020-06-01"), 100, 1500.0)
    assert gain == pytest.approx(-500.0)


def test_loss_carryforward():
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 2000.0)
    led.sell("X", T("2020-06-01"), 100, 1000.0)     # -1000 loss (no replacement -> allowed)
    assert led.settle(T("2021-01-04")) == 0.0
    assert led.carryforward == pytest.approx(-1000.0)
    led.buy("Y", T("2021-02-01"), 100, 1000.0)
    led.sell("Y", T("2021-08-02"), 100, 2500.0)     # +1500 gain
    assert led.settle(T("2022-01-03")) == pytest.approx(0.37 * 500.0)


def test_wash_sale_purchase_before_loss_defers_loss_into_replacement_lot():
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 2000.0)      # $20
    led.buy("X", T("2020-05-20"), 100, 1200.0)      # $12, replacement bought within 30d before sale
    gain = led.sell("X", T("2020-06-01"), 100, 1000.0)  # FIFO: sells the $20 lot at $10 -> -1000 loss
    assert gain == pytest.approx(0.0)               # fully disallowed
    assert led.basis("X") == pytest.approx(1200.0 + 1000.0)
    assert led.realized_by_year[2020] == pytest.approx(0.0)


def test_wash_sale_purchase_after_loss_reverses_deduction():
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 2000.0)
    gain = led.sell("X", T("2020-06-01"), 100, 1000.0)
    assert gain == pytest.approx(-1000.0)
    led.buy("X", T("2020-06-20"), 50, 600.0)        # half the units repurchased within 30 days
    assert led.realized_by_year[2020] == pytest.approx(-500.0)
    assert led.lots["X"][0].basis == pytest.approx(600.0 + 500.0)


def test_no_wash_sale_outside_window():
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 2000.0)
    led.sell("X", T("2020-06-01"), 100, 1000.0)
    led.buy("X", T("2020-07-15"), 100, 1000.0)      # 44 days later
    assert led.realized_by_year[2020] == pytest.approx(-1000.0)


def test_distribution_raises_basis_and_accrues_income_tax():
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 1000.0)
    led.distribution("X", T("2020-03-31"), 50.0)
    assert led.basis("X") == pytest.approx(1050.0)
    assert led.accrued_liability() == pytest.approx(0.37 * 50.0)


def test_untaxed_ledger_has_no_liability():
    led = TaxLedger(SPEC, taxed=False)
    led.buy("X", T("2020-01-02"), 100, 1000.0)
    led.sell("X", T("2020-06-01"), 100, 5000.0)
    led.distribution("X", T("2020-03-31"), 50.0)
    assert led.accrued_liability() == 0.0
    assert led.settle(T("2021-01-04")) == 0.0
