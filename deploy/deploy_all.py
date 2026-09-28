"""
deploy/deploy_all.py

Runs all four deploy steps in order. Equivalent to running:
    python step1_s3_and_roles.py
    python step2_opensearch_and_kb.py
    python step3_lambdas.py
    python step4_api_gateway.py
one after another. Prefer running them individually the first time so you
can see each stage succeed (step2 in particular takes several minutes and
is the one most likely to need a retry).
"""

import runpy

STEPS = [
    "step1_s3_and_roles",
    "step2_opensearch_and_kb",
    "step3_lambdas",
    "step4_api_gateway",
]

if __name__ == "__main__":
    for step in STEPS:
        print(f"\n{'=' * 60}\nRunning {step}\n{'=' * 60}")
        runpy.run_module(step, run_name="__main__")
