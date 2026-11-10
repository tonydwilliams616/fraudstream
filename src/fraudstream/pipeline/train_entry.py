"""fraudstream.pipeline.train_entry - the training step, run INSIDE SageMaker's XGBoost container.

What: reads the prepared train and validation days (CSV, from the prepare step),
      fits the model with the package's own train() (same settings, class weight
      and early stopping as the Phase 1 baseline), and saves only the trees early
      stopping chose as /opt/ml/model/xgboost-model. SageMaker packs that folder
      into model.tar.gz - exactly the layout the serving container loads.
Why the serving container: training in SageMaker's built-in XGBoost 3.0-5 image
      means the model is trained by the very XGBoost build that will serve it.
      Training-serving parity holds by construction, not just by a version pin.
Runs as a SageMaker "script mode" entry point: SageMaker passes hyperparameters
      as --name value arguments and tells the script where things are through
      environment variables (SM_CHANNEL_DATA, SM_MODEL_DIR). The pipeline ships
      this file together with the fraudstream package source.
Hyperparameters: none are needed - the defaults are XGB_PARAMS in training/train.py.
      --n_estimators / --max_depth / --learning_rate override them, which is how
      the gate is tested with a deliberately weak model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

from fraudstream.training.data import TrainingData
from fraudstream.training.train import best_booster, train

MODEL_FILE = "xgboost-model"  # the name SageMaker's XGBoost container loads


def overrides(argv) -> dict:
    """Hyperparameters passed by SageMaker that change the default settings."""
    p = argparse.ArgumentParser()
    p.add_argument("--n_estimators", type=int)
    p.add_argument("--max_depth", type=int)
    p.add_argument("--learning_rate", type=float)
    args, _unknown = p.parse_known_args(argv)  # ignore anything else SageMaker passes
    return {k: v for k, v in vars(args).items() if v is not None}


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    env = os.environ if env is None else env
    data_dir = Path(env.get("SM_CHANNEL_DATA", "/opt/ml/input/data/data"))
    model_dir = Path(env.get("SM_MODEL_DIR", "/opt/ml/model"))
    params = overrides(sys.argv[1:] if argv is None else argv)

    train_days = pd.read_csv(data_dir / "train.csv")
    valid_days = pd.read_csv(data_dir / "valid.csv")
    data = TrainingData(train_days, valid_days, valid_days.iloc[0:0], info={})
    model, record = train(data, params)

    model_dir.mkdir(parents=True, exist_ok=True)
    # Raw JSON bytes: the same format the Phase 1 package step writes.
    (model_dir / MODEL_FILE).write_bytes(bytes(best_booster(model).save_raw("json")))
    print(json.dumps({"training": record}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
