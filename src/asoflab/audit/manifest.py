"""Run manifests: a machine-readable record of what a run used and produced.

A manifest supports reproducibility and makes accidental or unrecorded changes
to files detectable. It is not proof that the data, the availability times, or
the conclusions are correct.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from asoflab.audit.environment import describe_environment, utc_now

MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
HASH_NOTE = (
    "Hashes identify content and detect accidental or unrecorded changes. "
    "They are not evidence that the content, the availability times, or any conclusion is correct."
)


def file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(document: Any) -> str:
    """SHA-256 of a JSON-serialisable value in canonical form."""
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def write_json(path: str | Path, document: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, default=str) + "\n")
    return target


def build_manifest(
    *,
    run_id: str,
    kind: str,
    directory: Path,
    synthetic: bool,
    identity: dict[str, Any],
    details: dict[str, Any],
    output_files: list[str],
) -> dict[str, Any]:
    """Assemble a manifest. `identity` is what defines the run; `details` is everything else."""
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "kind": kind,
        "created_at": utc_now(),
        "synthetic_data": synthetic,
        "identity": identity,
        **details,
        "environment": describe_environment(),
        "outputs": {
            name: {
                "sha256": file_hash(directory / name),
                "bytes": (directory / name).stat().st_size,
            }
            for name in sorted(output_files)
            if (directory / name).exists()
        },
        "note": HASH_NOTE,
    }


def read_manifest(directory: str | Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((Path(directory) / MANIFEST_NAME).read_text())
    return document
