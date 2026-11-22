"""Tests for fraudstream.simulator.drift - the runtime drift switch.

What: scenarios change messages as intended and never the caller's copy; the
      switch re-reads its flag at most once per refresh, and keeps its current
      scenario when the flag can't be read or names something unknown; run()
      sends drifted messages.
Why:  the switch is the lever for every Phase 6 test - if it silently did
      nothing, "no drift detected" would look like a monitoring result.
"""

import io
import json

import pytest

from fraudstream.generator.dataset import DatasetParams, generate_dataset
from fraudstream.simulator.__main__ import PrintSink, Settings, run
from fraudstream.simulator.drift import AMOUNT_INFLATION, SCENARIOS, DriftSwitch, fixed
from fraudstream.simulator.events import Pacer
from fraudstream.storage import save_version

MESSAGE = {"transaction_id": "T1", "amount": 20.0, "channel": "online"}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeFlag:
    """A flag whose value the test changes; counts how often it's read."""

    def __init__(self, value="none"):
        self.value = value
        self.reads = 0

    def __call__(self):
        self.reads += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_none_leaves_the_message_alone():
    assert SCENARIOS["none"](MESSAGE) == MESSAGE


def test_amount_inflation_raises_amounts_without_touching_the_original():
    drifted = SCENARIOS["amount_inflation"](MESSAGE)
    assert drifted["amount"] == round(20.0 * AMOUNT_INFLATION, 2)
    assert drifted["transaction_id"] == "T1" and drifted["channel"] == "online"
    assert MESSAGE["amount"] == 20.0  # the caller's message is unchanged


def test_switch_reads_the_flag_at_most_once_per_refresh():
    flag, clock = FakeFlag("amount_inflation"), FakeClock()
    switch = DriftSwitch(flag, refresh_seconds=60, clock=clock)
    assert switch.current() == "amount_inflation"
    flag.value = "none"
    clock.now = 59
    assert switch.current() == "amount_inflation"  # not re-read yet
    clock.now = 60
    assert switch.current() == "none"
    assert flag.reads == 2


@pytest.mark.parametrize("bad", [RuntimeError("throttled"), "price_crash"])
def test_switch_keeps_its_scenario_when_the_flag_is_unreadable_or_unknown(bad, caplog):
    flag, clock = FakeFlag("amount_inflation"), FakeClock()
    switch = DriftSwitch(flag, refresh_seconds=60, clock=clock)
    switch.current()
    flag.value, clock.now = bad, 60
    assert switch.current() == "amount_inflation"
    assert "keeping amount_inflation" in caplog.text


def test_settings_choose_a_live_or_fixed_switch():
    base = {"DRY_RUN": "1"}
    assert Settings.from_env(base).drift_switch().current() == "none"
    assert Settings.from_env(base | {"DRIFT_SCENARIO": "amount_inflation"}).drift_scenario == (
        "amount_inflation"
    )
    assert Settings.from_env(base | {"DRIFT_PARAMETER": "/fs/simulator/drift"}).drift_parameter
    with pytest.raises(ValueError, match="DRIFT_SCENARIO"):
        Settings.from_env(base | {"DRIFT_SCENARIO": "price_crash"})


def test_run_sends_drifted_messages(tmp_path):
    dataset = generate_dataset(DatasetParams(seed=3, n_cards=200, n_merchants=80, days=2))
    save_version(dataset, tmp_path, "v1")
    env = {
        "DRY_RUN": "1",
        "DATASET_URI": str(tmp_path / "transactions" / "v1"),
        "START_DATE": "2026-11-07",
        "MAX_MESSAGES": "20",
    }
    pacer = Pacer(1000, sleep=lambda s: None)

    def amounts(switch):
        out = io.StringIO()
        run(Settings.from_env(env), PrintSink(out), pacer, drift=switch)
        return [json.loads(line)["amount"] for line in out.getvalue().splitlines()]

    normal = amounts(DriftSwitch(fixed("none")))
    inflated = amounts(DriftSwitch(fixed("amount_inflation")))
    assert inflated == [round(a * AMOUNT_INFLATION, 2) for a in normal]
