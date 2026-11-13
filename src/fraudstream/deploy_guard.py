"""python -m fraudstream.deploy_guard - refuse plans that would REPLACE critical resources.

What: reads a Terraform plan as JSON (terraform show -json tfplan) on stdin and
      fails if it would delete - on its own or as part of a replacement - any
      resource in PROTECTED. Creating them (a rebuild after the nightly teardown)
      is fine. Set ALLOW_REPLACE=true to let one deliberate replacement through.
Why:  a reviewed plan still caused an outage. Adding the rollback block to the
      endpoint showed "# forces replacement" in the PR's plan; nobody noticed,
      the endpoint was deleted and recreated, and scoring stopped for ~3.5
      minutes (nothing was lost - Kafka buffered it - but customers' fraud
      checks were delayed by up to ~4.5 minutes). People skim long plans; the
      pipeline doesn't. This makes "would cause an outage" a hard stop.
Protected:
  aws_sagemaker_endpoint.fraud  replacing it = no scoring until the new one is up
                                (~4-10 min); model updates must go through the
                                in-place blue/green canary instead
  aws_msk_cluster.this          replacing it = ~25 min outage and every buffered
                                message lost
The nightly teardown uses `terraform destroy`, not this check, so it's unaffected.
"""

from __future__ import annotations

import json
import os
import sys

PROTECTED = {
    "aws_sagemaker_endpoint.fraud": "the fraud endpoint (scoring stops until it is recreated)",
    "aws_msk_cluster.this": "the MSK cluster (~25 min outage, buffered messages lost)",
}


def dangerous(plan: dict) -> list[tuple[str, list[str]]]:
    """Protected resources this plan would delete: [(address, actions)]."""
    found = []
    for change in plan.get("resource_changes", []):
        actions = change.get("change", {}).get("actions", [])
        if change.get("address") in PROTECTED and "delete" in actions:
            found.append((change["address"], actions))
    return found


def main(stdin=None, env=None) -> int:
    stdin = sys.stdin if stdin is None else stdin
    env = os.environ if env is None else env
    found = dangerous(json.load(stdin))
    if not found:
        print("Plan guard: OK - no protected resource is replaced or destroyed")
        return 0
    for address, actions in found:
        verb = "REPLACE" if "create" in actions else "DESTROY"
        print(f"Plan guard: this plan would {verb} {address} - {PROTECTED[address]}")
    if env.get("ALLOW_REPLACE") == "true":
        print("Plan guard: allowed by ALLOW_REPLACE=true (a deliberate, approved replacement)")
        return 0
    print(
        "Plan guard: REFUSED. Change the code so the resource is updated in place, or run "
        "Terraform Deploy by hand with allow_replace=true if the outage is intended."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
