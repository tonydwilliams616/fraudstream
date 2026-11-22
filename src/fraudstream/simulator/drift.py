"""fraudstream.simulator.drift - switch the simulated world into a drift scenario while it runs.

What: DriftSwitch reads the name of the current scenario from a runtime flag (an
      SSM parameter, read at most once a minute) and apply() changes each
      outgoing message to match it. "none" leaves messages untouched.
Why:  Phase 6 needs to change the world on demand and watch the monitoring
      notice. Changing an environment variable would restart the simulator,
      and a restarted simulator replays the day's transactions from midnight
      (its seed comes from the date) - duplicate IDs and doubled card history.
      A flag read while running changes the world without a restart:
        aws ssm put-parameter --name /fs/simulator/drift --value amount_inflation --overwrite
Concepts:
  - RUNTIME FLAG (feature flag): behaviour controlled by a value read while the
    program runs, not baked in at start-up. Flip it, and within a minute the
    program behaves differently - no deploy.
  - FAIL SAFE: if the flag can't be read, or holds a name we don't know, keep
    the scenario we already have and log a warning. A monitoring hiccup must
    never crash the stream or flip the world back and forth.
  - Drift is applied to the MESSAGE (what the bank's systems see), never to
    the labels: whether a purchase was fraud doesn't change because prices rose.
Scenarios:
  none              the world as in training
  amount_inflation  every amount x1.5 - prices rise; nobody commits more fraud.
                    DATA (covariate) drift: log_amount moves for good;
                    amount_vs_card_mean moves too, then settles as each card's
                    history fills with the new, higher amounts.
Prior shift (more fraud, same patterns) needs no switch: FRAUD_RATE does it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

log = logging.getLogger("fraudstream.simulator.drift")

AMOUNT_INFLATION = 1.5


def _unchanged(message: dict) -> dict:
    return message


def _inflate_amounts(message: dict) -> dict:
    changed = dict(message)  # a copy: never edit the caller's message in place
    if changed.get("amount") is not None:
        changed["amount"] = round(changed["amount"] * AMOUNT_INFLATION, 2)
    return changed


SCENARIOS: dict[str, Callable[[dict], dict]] = {
    "none": _unchanged,
    "amount_inflation": _inflate_amounts,
}


class DriftSwitch:
    """The current drift scenario, re-read from `read` at most every `refresh_seconds`.

    `read` is any function returning a scenario name - the SSM parameter in the
    real program, a fixed name for local runs, a fake in tests (dependency
    injection: the switch doesn't care where the name comes from).
    """

    def __init__(
        self,
        read: Callable[[], str],
        refresh_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._read = read
        self.refresh_seconds = refresh_seconds
        self._clock = clock
        self._next_read: float | None = None
        self.scenario = "none"

    def current(self) -> str:
        now = self._clock()
        if self._next_read is not None and now < self._next_read:
            return self.scenario
        self._next_read = now + self.refresh_seconds
        try:
            wanted = self._read().strip()
        except Exception as err:  # noqa: BLE001 - any read failure: keep going as we are
            log.warning("can't read the drift flag (%s) - keeping %s", err, self.scenario)
            return self.scenario
        if wanted not in SCENARIOS:
            log.warning("unknown drift scenario %r - keeping %s", wanted, self.scenario)
        elif wanted != self.scenario:
            log.info("drift scenario: %s -> %s", self.scenario, wanted)
            self.scenario = wanted
        return self.scenario

    def apply(self, message: dict) -> dict:
        return SCENARIOS[self.current()](message)


def fixed(scenario: str) -> Callable[[], str]:
    """A reader that always returns the same name (local runs, DRIFT_SCENARIO)."""
    return lambda: scenario


def ssm_parameter(name: str, region: str) -> Callable[[], str]:
    """A reader for the SSM parameter that holds the scenario name."""
    import boto3

    client = boto3.client("ssm", region_name=region)
    return lambda: client.get_parameter(Name=name)["Parameter"]["Value"]
