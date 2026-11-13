import pandas as pd
import pytest

from vaneck.config import SPEC, Spec
from vaneck.tax import Lot, TaxLedger, select_lots

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


def test_lots_bought_at_the_same_price_are_relieved_oldest_first():
    """Per-unit gains that differ only in their last bits are a tie; rounding must not decide the order.

    The study has such a pair: HAP bought on 2021-08-02 and on 2021-12-01 at the same adjusted price. Which
    lot went first on 2022-02-01 depended on the CPU's arithmetic kernels and changed later tax years.
    """
    now, cost = T("2022-02-01"), 40.66001116735793
    for nudge in (-1.0, 0.0, 1.0):
        older = Lot(T("2021-08-02"), 6862.559345786264, 6862.559345786264 * cost * (1.0 + nudge * 4e-16))
        newer = Lot(T("2021-12-01"), 34187.683185089765, 34187.683185089765 * cost)
        for method in ("TAXOPT", "HIFO"):
            assert select_lots([older, newer], 5000.0, method, 44.066906, now) == [(0, 5000.0)], (method, nudge)
        # the same holds when both lots sit at a loss
        assert select_lots([older, newer], 5000.0, "TAXOPT", 38.0, now) == [(0, 5000.0)], nudge
    # a real price difference still decides
    cheap, dear = Lot(T("2021-08-02"), 100.0, 4000.0), Lot(T("2021-12-01"), 100.0, 4100.0)
    assert select_lots([cheap, dear], 50.0, "TAXOPT", 44.0, now) == [(1, 50.0)]      # smaller gain first
    assert select_lots([cheap, dear], 50.0, "HIFO", 44.0, now) == [(1, 50.0)]        # higher cost first
    assert select_lots([cheap, dear], 50.0, "TAXOPT", 39.0, now) == [(1, 50.0)]      # bigger loss first
    assert select_lots([cheap, dear], 50.0, "FIFO", 44.0, now) == [(0, 50.0)]


def test_a_sale_that_leaves_rounding_residue_closes_the_lot():
    """Dollars divided by price can come back a hair short of the units held; the lot must not survive as dust."""
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2023-01-03"), 73327.79709048141, 1778127.36)
    gain = led.sell("X", T("2023-06-01"), 73327.79709048141 - 3e-11, 1700000.0)
    assert led.lots["X"] == [] and led.units("X") == 0.0 and led.basis("X") == 0.0
    assert gain == pytest.approx(1700000.0 - 1778127.36)
    # a real remainder is kept
    led.buy("Y", T("2023-01-03"), 100.0, 1000.0)
    led.sell("Y", T("2023-06-01"), 99.5, 995.0)
    assert led.units("Y") == pytest.approx(0.5)


def test_wash_reversal_after_settlement_is_charged_in_the_purchase_year():
    """SPEC section 6: a reversal triggered after its loss year was settled is charged in the following year.

    A loss realised in December is settled at the first rebalance of January; a repurchase on that day used to
    book the reversal to the closed year, where it was never taxed.
    """
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2018-01-02"), 100, 2000.0)
    led.buy("Y", T("2018-01-02"), 100, 1000.0)
    led.sell("X", T("2018-12-03"), 100, 1000.0)          # -1000 loss in 2018
    led.sell("Y", T("2018-12-03"), 100, 3000.0)          # +2000 gain in 2018
    assert led.settle(T("2019-01-02")) == pytest.approx(0.37 * 1000.0)   # 2018 settled on its net gain
    led.buy("X", T("2019-01-02"), 100, 1000.0)           # 30 days after the loss: the loss is disallowed
    assert led.realized_by_year[2018] == pytest.approx(1000.0)          # the closed year is not reopened
    assert led.realized_by_year[2019] == pytest.approx(1000.0)          # the reversal is charged in 2019
    assert led.accrued_liability() == pytest.approx(0.37 * 1000.0)
    assert led.lots["X"][0].basis == pytest.approx(2000.0)              # and the basis still carries it
    assert led.settle(T("2020-01-02")) == pytest.approx(0.37 * 1000.0)


def test_same_day_purchase_counts_on_both_sides_of_the_wash_sale_rule():
    """Within 30 days before or after includes the day of the sale itself (substantially-identical group)."""
    spec = Spec(wash_sale_groups=(("X", "XX"),))
    led = TaxLedger(spec, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 2000.0)
    led.sell("X", T("2020-06-01"), 100, 1000.0)          # -1000 loss
    led.buy("XX", T("2020-06-01"), 100, 1000.0)          # bought the same day, after the sale
    assert led.realized_by_year[2020] == pytest.approx(0.0)
    assert led.basis("XX") == pytest.approx(2000.0)
    led2 = TaxLedger(spec, taxed=True)
    led2.buy("X", T("2020-01-02"), 100, 2000.0)
    led2.buy("XX", T("2020-06-01"), 100, 1000.0)         # bought the same day, before the sale
    assert led2.sell("X", T("2020-06-01"), 100, 1000.0) == pytest.approx(0.0)
    assert led2.basis("XX") == pytest.approx(2000.0)


def test_wash_window_boundary_is_thirty_days_on_both_sides():
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 2000.0)
    led.sell("X", T("2020-06-01"), 100, 1000.0)
    led.buy("X", T("2020-07-01"), 100, 1000.0)           # day 30: disallowed
    assert led.realized_by_year[2020] == pytest.approx(0.0)
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 2000.0)
    led.sell("X", T("2020-06-01"), 100, 1000.0)
    led.buy("X", T("2020-07-02"), 100, 1000.0)           # day 31: allowed
    assert led.realized_by_year[2020] == pytest.approx(-1000.0)
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 2000.0)
    led.buy("X", T("2020-05-02"), 100, 1200.0)           # 30 days before the sale: replacement shares
    assert led.sell("X", T("2020-06-01"), 100, 1000.0) == pytest.approx(0.0)
    led = TaxLedger(SPEC, taxed=True)
    led.buy("X", T("2020-01-02"), 100, 2000.0)
    led.buy("X", T("2020-05-01"), 100, 1200.0)           # 31 days before: not
    assert led.sell("X", T("2020-06-01"), 100, 1000.0) == pytest.approx(-1000.0)
