"""fraudstream.consumer.archive - collects scored records into one-minute files in S3.

What: ArchiveBuffer.add() keeps scored records in memory; due() says when a
      file should be written (60 seconds since the first record, or 5,000
      records); flush() writes them as one gzipped JSON-lines file to
      s3://<scored bucket>/scored/dt=<date>/hour=<hh>/<time>-<consumer>.jsonl.gz
Why:  S3 is the permanent record of every scoring decision - the input to
      retraining, drift analysis and DR replay. One file a minute (instead of
      one per micro-batch, ~5 a second) keeps file counts and request costs
      sensible. The consumer commits Kafka offsets only after a flush succeeds,
      so a crash re-scores at most about a minute of transactions, never loses
      them. Duplicates are possible after a crash; readers de-duplicate on
      transaction_id.
Layout: dt=/hour= folders (by when records were scored) let query engines such
      as Athena read just the hours they need.
"""

from __future__ import annotations

import gzip
import json
import time
from datetime import UTC, datetime


class ArchiveBuffer:
    def __init__(
        self,
        s3,
        bucket: str,
        consumer_id: str,
        prefix: str = "scored",
        max_records: int = 5000,
        max_age_s: float = 60,
        clock=time.monotonic,
        now=lambda: datetime.now(UTC),
    ):
        self.s3 = s3
        self.bucket = bucket
        self.consumer_id = consumer_id
        self.prefix = prefix
        self.max_records = max_records
        self.max_age_s = max_age_s
        self.clock = clock
        self.now = now
        self.records: list[dict] = []
        self._first_at: float | None = None

    def add(self, records: list[dict]) -> None:
        if records and self._first_at is None:
            self._first_at = self.clock()
        self.records.extend(records)

    def due(self) -> bool:
        """True when there's something to write and it's big or old enough."""
        if not self.records:
            return False
        return (
            len(self.records) >= self.max_records or self.clock() - self._first_at >= self.max_age_s
        )

    def flush(self) -> str | None:
        """Write everything buffered as one file; return its key (None if nothing to write)."""
        if not self.records:
            return None
        when = self.now()
        key = (
            f"{self.prefix}/dt={when:%Y-%m-%d}/hour={when:%H}/"
            f"{when:%Y%m%dT%H%M%S%f}-{self.consumer_id}.jsonl.gz"
        )
        lines = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in self.records)
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=gzip.compress(lines.encode()),
            ContentType="application/gzip",
        )
        self.records = []
        self._first_at = None
        return key
