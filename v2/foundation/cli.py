"""Lab migration and redacted status tools."""

import argparse
import json

from .db import OWNERS, Store, migrate
from .messaging import Broker, replay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate", "health", "inspect", "replay"])
    parser.add_argument("--owner", choices=OWNERS, default="lab_producer")
    parser.add_argument("--parked-id")
    parser.add_argument("--actor", default="")
    args = parser.parse_args()
    if args.action == "migrate":
        migrate()
        print("Owner-local schema versions 1–2 applied to 14 isolated lab contexts")
    elif args.action == "replay":
        event = replay(Store(args.owner), args.parked_id, args.actor)
        broker = Broker()
        broker.publish(event)
        broker.close()
        print(json.dumps({"replayed_event_id": event["event_id"]}))
    else:
        print(json.dumps(Store(args.owner).metrics()))


if __name__ == "__main__":
    main()
