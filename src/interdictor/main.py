from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .client import SapientEffectorClient
from .config import load_config


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="INTERDICTOR BSI Flex 335 v2 SAPIENT effector node")
    p.add_argument("--config", default="config/interdictor.yaml")
    p.add_argument(
        "--node-id",
        default=None,
        help=(
            "Override node.node_id from the config file. Only needed when running "
            "multiple interdictor instances in parallel against the same Fusion Node "
            "(each needs a distinct, stable UUID) - a single instance should keep "
            "the node_id from its config file across restarts."
        ),
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    if args.node_id is not None:
        cfg["node"]["node_id"] = args.node_id

    reg_path = Path(cfg["node"]["registration_file"])
    if not reg_path.is_absolute():
        cfg["node"]["registration_file"] = str(reg_path.resolve())

    level = getattr(logging, cfg.get("logging", {}).get("level", "INFO").upper())
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(SapientEffectorClient(cfg).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
