"""
deploy/state.py

Deploy steps depend on IDs/ARNs created by earlier steps (bucket ARN,
IAM role ARNs, OpenSearch collection ARN, Knowledge Base ID, etc). Rather
than making you copy-paste values between scripts, each step reads/writes
them here, to deploy/state.json.
"""

import json
import os

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


def load():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save(new_values: dict):
    current = load()
    current.update(new_values)
    with open(STATE_FILE, "w") as f:
        json.dump(current, f, indent=2)
