from __future__ import annotations

import argparse
from pathlib import Path

from .site import render_index
from .update import ROOT, generate


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggiorna Motorsport Calendar")
    parser.add_argument("--offline", action="store_true", help="usa solo dati verificati inclusi nel repository")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    events = generate(args.root, online=not args.offline)
    render_index(args.root, events)


if __name__ == "__main__":
    main()
