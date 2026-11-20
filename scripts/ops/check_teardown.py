"""scripts/ops/check_teardown.py - after a teardown, proves nothing is left billing by the hour.

What: asks AWS for the eight kinds of resource the nightly teardown must
      remove - SageMaker endpoints, endpoint scaling targets, MSK clusters, ECS
      clusters, NAT gateways, Elastic IPs, and training or processing jobs still
      running - and prints what it finds. Persistent things (S3, ECR, the model
      registry, the Prometheus and Grafana workspaces, KMS keys) are not checked:
      they're meant to stay.
Why:  "the destroy workflow went green" is a claim; this is the evidence. One
      forgotten NAT gateway or endpoint costs more per day than everything that
      stays up all month.
Exit code: 1 if anything is still there.

Usage (repo root, venv active, `source ./aws-login.sh`):
  python scripts/ops/check_teardown.py
"""

from __future__ import annotations

import argparse
import sys


def leftovers(sagemaker, kafka, ecs, ec2, autoscaling) -> dict[str, list[str]]:
    """Name -> what's still there. Every list should be empty after a teardown."""
    nat_filter = [{"Name": "state", "Values": ["pending", "available"]}]
    return {
        "SageMaker endpoints": [e["EndpointName"] for e in sagemaker.list_endpoints()["Endpoints"]],
        "endpoint scaling targets": [
            t["ResourceId"]
            for t in autoscaling.describe_scalable_targets(ServiceNamespace="sagemaker")[
                "ScalableTargets"
            ]
        ],
        "MSK clusters": [c["ClusterName"] for c in kafka.list_clusters_v2()["ClusterInfoList"]],
        "ECS clusters": ecs.list_clusters()["clusterArns"],
        "NAT gateways": [
            n["NatGatewayId"] for n in ec2.describe_nat_gateways(Filters=nat_filter)["NatGateways"]
        ],
        "Elastic IPs": [a["PublicIp"] for a in ec2.describe_addresses()["Addresses"]],
        "training jobs running": [
            j["TrainingJobName"]
            for j in sagemaker.list_training_jobs(StatusEquals="InProgress")["TrainingJobSummaries"]
        ],
        "processing jobs running": [
            j["ProcessingJobName"]
            for j in sagemaker.list_processing_jobs(StatusEquals="InProgress")[
                "ProcessingJobSummaries"
            ]
        ],
    }


def main(argv: list[str] | None = None, clients: dict | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args(argv)

    if clients is None:
        import boto3

        session = boto3.Session(region_name=args.region)
        clients = {
            name: session.client(service)
            for name, service in {
                "sagemaker": "sagemaker",
                "kafka": "kafka",
                "ecs": "ecs",
                "ec2": "ec2",
                "autoscaling": "application-autoscaling",
            }.items()
        }

    found = leftovers(**clients)
    for name, items in found.items():
        print(f"{'LEFT' if items else 'ok  '}  {name}: {', '.join(items) if items else 'none'}")
    left = sum(len(items) for items in found.values())
    print("PASS: nothing left billing by the hour" if left == 0 else f"FAIL: {left} left over")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
