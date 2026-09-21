from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.adapters.base import CardRequest
from app.adapters.registry import build_adapters, load_config
from app.devtools import detect_platform, dump_for_inspection
from app.http import HttpxClient


def _http() -> HttpxClient:
    return HttpxClient("mtgcardtracker/0.1 (+local tool)")


def cmd_explore(args):
    http = _http()
    platform = detect_platform(args.url, http)
    print(f"platform: {platform}")
    path = dump_for_inspection(args.url, http, args.out)
    print(f"dumped:   {path}")


def cmd_scaffold(args):
    stores = Path("config/stores.yaml")
    text = stores.read_text()
    entry = (
        f"  - id: {args.store_id}\n"
        f"    name: {args.store_id.title()}\n"
        f"    zone: singapore\n"
        f"    adapter: app.adapters.{args.store_id}:{args.store_id.title()}Adapter\n"
        f"    base_url: {args.base_url}\n"
        f"    enabled: false\n"
    )
    stores.write_text(text.rstrip() + "\n" + entry)
    print(f"Added disabled entry for {args.store_id} to {stores}")


def cmd_probe(args):
    configs = load_config("config/stores.yaml")
    adapters = build_adapters(configs, _http())
    adapter = next((a for a in adapters if a.store_id == args.store_id), None)
    if adapter is None:
        raise SystemExit(f"store {args.store_id!r} not found or disabled")
    card = CardRequest(name=args.card)
    offers = adapter.search(card)
    print(json.dumps([o.to_dict() for o in offers], indent=2))


def cmd_validate(args):
    from app.adapters.shopify_parse import parse_finish
    print("validate: fixture replay is exercised by pytest contract tests")
    print("run: pytest -v")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cli")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("explore")
    p.add_argument("url")
    p.add_argument("--out", default="fixtures/inspect")
    p.set_defaults(func=cmd_explore)

    p = sub.add_parser("scaffold")
    p.add_argument("store_id")
    p.add_argument("--base-url", required=True)
    p.add_argument("--platform", choices=["shopify", "custom"], default="shopify")
    p.set_defaults(func=cmd_scaffold)

    p = sub.add_parser("probe")
    p.add_argument("store_id")
    p.add_argument("card")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("validate")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
