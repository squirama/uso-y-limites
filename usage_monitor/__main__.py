import argparse
import json
from dataclasses import asdict
import sys

from .codex import CodexClient
from .models import UsageError


def parse_providers(value):
    allowed = ("claude", "codex")
    selected = value.split(",")
    if not selected or any(item not in allowed for item in selected) or len(set(selected)) != len(selected):
        raise argparse.ArgumentTypeError("usa claude, codex o claude,codex")
    return [item for item in allowed if item in selected]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Monitor local de consumo de Codex y Claude")
    parser.add_argument("--probe-codex", action="store_true", help="Consultar únicamente las métricas de Codex")
    parser.add_argument("--providers", type=parse_providers,
                        help="Servicios visibles: claude, codex o claude,codex")
    args = parser.parse_args(argv)
    if args.probe_codex:
        client = CodexClient()
        try:
            print(json.dumps(asdict(client.fetch()), ensure_ascii=True, indent=2))
            return 0
        except UsageError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        finally:
            client.close()
    from .ui import run
    run(providers=args.providers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
