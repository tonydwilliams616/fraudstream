"""tests/test_check_teardown.py - checks the "nothing left billing" audit.

What: with every AWS answer empty the script passes; one leftover NAT gateway
      or endpoint is listed by name and fails it. AWS is faked.
Why:  a check that can't fail is worse than none - this proves it does.
"""

import runpy
from pathlib import Path


def script():
    path = Path(__file__).parents[1] / "scripts" / "ops" / "check_teardown.py"
    return runpy.run_path(str(path))


class Fake:
    """Answers every call with the canned reply for that method name."""

    def __init__(self, **replies):
        self.replies = replies

    def __getattr__(self, name):
        return lambda **_kwargs: self.replies[name]


def clients(endpoints=(), nats=()):
    return {
        "sagemaker": Fake(
            list_endpoints={"Endpoints": [{"EndpointName": e} for e in endpoints]},
            list_training_jobs={"TrainingJobSummaries": []},
            list_processing_jobs={"ProcessingJobSummaries": []},
        ),
        "kafka": Fake(list_clusters_v2={"ClusterInfoList": []}),
        "ecs": Fake(list_clusters={"clusterArns": []}),
        "ec2": Fake(
            describe_nat_gateways={"NatGateways": [{"NatGatewayId": n} for n in nats]},
            describe_addresses={"Addresses": []},
        ),
        "autoscaling": Fake(describe_scalable_targets={"ScalableTargets": []}),
    }


def test_clean_account_passes(capsys):
    assert script()["main"]([], clients=clients()) == 0
    assert "PASS" in capsys.readouterr().out


def test_leftovers_are_named_and_fail(capsys):
    assert script()["main"]([], clients=clients(endpoints=["fs-fraud"], nats=["nat-1"])) == 1
    out = capsys.readouterr().out
    assert "fs-fraud" in out and "nat-1" in out and "FAIL: 2 left over" in out
