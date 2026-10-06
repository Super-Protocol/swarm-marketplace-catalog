#!/usr/bin/env python3
"""Measure what a model endpoint actually produces, single-stream and under load.

    charts/tests/smoke/model-throughput.py --base-url http://127.0.0.1:8000/v1 \
        --key … --model … [--concurrency 16] [--max-tokens 256]

Two numbers, because they answer different questions. Single-stream decode is
what one person waiting for an answer feels. Aggregate throughput under
concurrency is what the deployment is worth per hour, and on a card with a large
KV cache it is several times the single-stream figure.

Deliberately not a benchmark suite: it exists so that "tokens/s" in a listing's
README is a measurement somebody can repeat, not a number from a vendor slide.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROMPT = (
    "Write a short, self-contained Python function that merges two sorted lists "
    "into one sorted list, with a docstring and three doctests."
)


def generate(base: str, key: str, model: str, max_tokens: int, prompt: str) -> tuple[float, int, int]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        # No early stop: the point is to produce max_tokens every time, so the
        # rate is comparable between runs and between models.
        "ignore_eos": True,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=600) as response:
        payload = json.load(response)
    elapsed = time.monotonic() - started
    usage = payload.get("usage") or {}
    return elapsed, usage.get("completion_tokens", 0), usage.get("prompt_tokens", 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    generate(base, args.key, args.model, 16, "Say hi.")  # warm the graphs

    print(f"single stream, {args.repeats} runs of {args.max_tokens} tokens")
    rates = []
    for _ in range(args.repeats):
        elapsed, completion, prompt_tokens = generate(
            base, args.key, args.model, args.max_tokens, PROMPT)
        rates.append(completion / elapsed)
        print(f"  {completion:4d} tokens in {elapsed:6.2f}s  = {completion / elapsed:7.1f} tok/s"
              f"   (prompt {prompt_tokens})")
    print(f"  median {statistics.median(rates):.1f} tok/s")

    print(f"\n{args.concurrency} concurrent streams of {args.max_tokens} tokens")
    # Distinct prompts, so prefix caching does not turn this into one request
    # answered sixteen times.
    prompts = [f"{PROMPT} Name it merge_{i}." for i in range(args.concurrency)]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(
            lambda p: generate(base, args.key, args.model, args.max_tokens, p), prompts))
    wall = time.monotonic() - started
    total = sum(completion for _, completion, _ in results)
    per_stream = [completion / elapsed for elapsed, completion, _ in results]
    print(f"  {total} tokens in {wall:.2f}s  = {total / wall:.1f} tok/s aggregate")
    print(f"  per stream: median {statistics.median(per_stream):.1f} tok/s, "
          f"slowest {min(per_stream):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
