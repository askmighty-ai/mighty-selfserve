#!/usr/bin/env python3
"""Benchmark field discovery preprocessing token reduction across fixtures."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mighty.field_discovery_preprocess import estimate_tokens, prepare_discovery_input

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def main() -> None:
    names = sorted(
        f for f in os.listdir(FIXTURES)
        if f.endswith((".txt", ".html"))
    )
    print(f"{'Fixture':<30} {'Raw':>7} {'Final':>7} {'Saved':>7} {'Raw tok':>8} {'Final tok':>10}")
    print("-" * 80)

    total_raw = total_final = 0
    for name in names:
        path = os.path.join(FIXTURES, name)
        with open(path) as fh:
            raw = fh.read()
        result = prepare_discovery_input(raw)
        raw_tok = estimate_tokens(raw)
        final_tok = estimate_tokens(result.text)
        saved = raw_tok - final_tok
        pct = result.stats.reduction_ratio * 100
        print(
            f"{name:<30} {result.stats.raw_chars:>7} {result.stats.final_chars:>7} "
            f"{pct:>6.1f}% {raw_tok:>8} {final_tok:>10}"
        )
        total_raw += raw_tok
        total_final += final_tok

    print("-" * 80)
    total_saved = total_raw - total_final
    total_pct = (1 - total_final / total_raw) * 100 if total_raw else 0
    print(
        f"{'TOTAL':<30} {'':>7} {'':>7} {total_pct:>6.1f}% "
        f"{total_raw:>8} {total_final:>10}  (saved ~{total_saved} tokens)"
    )


if __name__ == "__main__":
    main()
