#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mission_ground.common.packet import HealthTelemetry, decode_health, decode_space_packet, encode_health

parser = argparse.ArgumentParser()
parser.add_argument("--frames", type=int, default=100_000)
parser.add_argument("--minimum-fps", type=float, default=20_000)
args = parser.parse_args()

sample = HealthTelemetry(time.time_ns(), 28.4, 31.2, 1200, 1, 0)
start = time.perf_counter()
for sequence in range(args.frames):
    raw = encode_health(sequence & 0x3FFF, sample)
    decode_health(decode_space_packet(raw))
elapsed = time.perf_counter() - start
fps = args.frames / elapsed
print(f"frames={args.frames} elapsed_s={elapsed:.3f} codec_frames_per_second={fps:,.0f}")
if fps < args.minimum_fps:
    raise SystemExit(f"FAIL: throughput below {args.minimum_fps:,.0f} frames/s")
print("PASS: packet codec throughput target")
