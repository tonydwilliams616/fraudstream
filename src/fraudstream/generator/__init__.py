"""fraudstream.generator - creates the synthetic data the project runs on.

What: makes imaginary cardholders and shops (entities.py), their normal
      spending (transactions.py) and fraud mixed into it (fraud.py).
      dataset.py runs all of it from one seed and fingerprints the result.
Why:  real card data can't be used, and synthetic data lets us control exactly
      how people behave - including changing that behaviour on purpose later
      to test whether the system notices (drift).
Concept: everything is driven by one random seed, so the same seed always
      produces exactly the same data. That makes every dataset reproducible.
"""

from fraudstream.generator.dataset import Dataset, DatasetParams, fingerprint, generate_dataset

__all__ = ["Dataset", "DatasetParams", "fingerprint", "generate_dataset"]
