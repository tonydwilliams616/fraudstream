"""tests/test_deploy_guard.py - checks the plan guard that blocks outage-causing replacements.

What: in-place updates and creates of protected resources pass (a model
      promotion, a rebuild); a replacement or destroy of one fails; unprotected
      resources may be replaced freely (endpoint configs are always replaced);
      ALLOW_REPLACE=true lets a deliberate replacement through.
Why:  the guard exists because a reviewed plan once replaced the endpoint and
      stopped scoring. If it let that through again, or blocked every rebuild,
      it would be worse than nothing.
"""

import io
import json

from fraudstream.deploy_guard import dangerous, main


def plan(*changes):
    return {
        "resource_changes": [
            {"address": a, "change": {"actions": list(acts)}} for a, acts in changes
        ]
    }


def run(p, env=None):
    return main(io.StringIO(json.dumps(p)), env or {})


ENDPOINT = "aws_sagemaker_endpoint.fraud"


def test_promotion_and_rebuild_pass():
    assert run(plan((ENDPOINT, ["update"]))) == 0  # model promotion: in-place canary
    assert run(plan((ENDPOINT, ["create"]), ("aws_msk_cluster.this", ["create"]))) == 0  # rebuild
    assert run(plan((ENDPOINT, ["no-op"]))) == 0


def test_replacing_or_destroying_a_protected_resource_fails():
    assert run(plan((ENDPOINT, ["delete", "create"]))) == 1
    assert run(plan((ENDPOINT, ["create", "delete"]))) == 1  # create_before_destroy
    assert run(plan(("aws_msk_cluster.this", ["delete"]))) == 1


def test_unprotected_replacements_are_fine():
    config = "aws_sagemaker_endpoint_configuration.fraud"
    assert dangerous(plan((config, ["create", "delete"]))) == []


def test_a_deliberate_replacement_can_be_allowed():
    replace = plan((ENDPOINT, ["delete", "create"]))
    assert run(replace, {"ALLOW_REPLACE": "true"}) == 0
    assert run(replace, {"ALLOW_REPLACE": "yes"}) == 1  # only the exact value counts
