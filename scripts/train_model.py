"""Sample model training scaffold."""

from __future__ import annotations

import argparse


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for future model training."""
    parser = argparse.ArgumentParser(
        description="Train and serialize a sample scikit-learn model.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/model.joblib",
        help="Path where the serialized model will be written.",
    )
    return parser.parse_args()


def main() -> None:
    """Entrypoint for the future sample model training script."""
    args = parse_args()
    raise NotImplementedError(
        (
            "Sample model training is intentionally omitted in the scaffold phase. "
            f"Planned artifact output path: {args.output}"
        ),
    )


if __name__ == "__main__":
    main()
