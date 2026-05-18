#!/usr/bin/env python3
"""Find the camera's real frame layout by correlating bytes with a known stimulus.

You record a 10-15s continuous stream with step 09 while alternately covering /
uncovering the lens on a known schedule (e.g. cover at t=2s, uncover at t=4s,
cover at t=6s, ...). Real pixel bytes will swing with that pattern; metadata
bytes won't. This script:

  1. Auto-finds the frame period from stream autocorrelation
     (= the byte offset where every frame "repeats")
  2. Slices the stream into frames at that period
  3. Computes per-byte temporal std (or correlation against a stimulus mask)
  4. Plots the result as a 2-D heatmap reshaped at the discovered width 256
     so pixel regions become visually obvious

Run:

    python 10_analyze_stimulus.py captures/stim01.bin
    # or, with explicit stimulus timing in the sidecar JSON:
    python 10_analyze_stimulus.py captures/stim01.bin --correlate
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

WIDTH = 256       # known from APK (jtWidthValue) — used only for visualization
DTYPE = np.uint16


def _full_autocorr(samples: np.ndarray) -> np.ndarray:
    """Compute the normalized autocorrelation of `samples` at every integer lag in O(N log N).

    Uses FFT. Returns an array of length N where index k = autocorr at lag k,
    normalized so index 0 = 1.0.
    """
    x = samples.astype(np.float32) - samples.mean(dtype=np.float64)
    N = x.size
    # Pad to >= 2N (next power of two) to avoid circular wrap.
    M = 1 << int(np.ceil(np.log2(2 * N)))
    fx = np.fft.rfft(x, n=M)
    full = np.fft.irfft(fx * np.conj(fx), n=M)[:N]
    return full / full[0]


def find_frame_period(samples: np.ndarray, lo: int, hi: int, step: int) -> tuple[int, np.ndarray]:
    """Search lags in [lo, hi] for the one with the strongest autocorrelation.
    Returns (best_lag_in_samples, score_array_for_that_range_at_step).

    Implementation uses one global FFT-based autocorrelation, then samples
    values at the requested lags. Much faster than per-lag dot products.
    """
    acorr = _full_autocorr(samples)
    lags = np.arange(lo, hi, step)
    scores = acorr[lags]
    best_lag = int(lags[int(np.argmax(scores))])
    return best_lag, scores


def slice_into_frames(stream_u16: np.ndarray, frame_samples: int) -> np.ndarray:
    """Return a (n_frames, frame_samples) matrix from the 1-D stream."""
    n = stream_u16.size // frame_samples
    return stream_u16[: n * frame_samples].reshape(n, frame_samples)


def build_stimulus_mask(events: list[dict], n_frames: int, capture_s: float) -> np.ndarray | None:
    """Translate a list of {'t': sec, 'state': 'on'|'off'} into a per-frame 0/1 mask."""
    if not events:
        return None
    fps = n_frames / capture_s
    mask = np.zeros(n_frames)
    cur = 0.0
    for ev in events:
        t = float(ev["t"])
        state = ev["state"]
        idx = int(round(t * fps))
        if state == "on":
            cur = 1.0
        elif state == "off":
            cur = 0.0
        else:
            raise ValueError(f"unknown state: {state}")
        mask[idx:] = cur
    return mask


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stream", type=Path)
    ap.add_argument("--correlate", action="store_true",
                    help="if set, score bytes by correlation with stimulus mask in sidecar JSON")
    ap.add_argument("--min-frame-kb", type=int, default=80)
    ap.add_argument("--max-frame-kb", type=int, default=260)
    ap.add_argument("--frame-bytes", type=int, default=None,
                    help="skip autocorrelation; force a specific frame size in bytes")
    ap.add_argument("--fine-near", type=int, default=None,
                    help="do a fine 1-sample-step search in [N-2KB, N+2KB] around N bytes")
    args = ap.parse_args()

    raw = np.frombuffer(args.stream.read_bytes(), dtype=np.uint8)
    samples = np.frombuffer(raw.tobytes(), dtype=DTYPE)
    print(f"stream: {raw.size} bytes ({samples.size} uint16 samples)")

    # 1) Find the frame period (in uint16 samples) via autocorrelation, or use override.
    if args.fine_near is not None:
        center_samples = args.fine_near // 2
        # Search ± 2 KB = ±1024 samples at step 1.
        lo = max(1, center_samples - 1024)
        hi = min(center_samples + 1024, samples.size // 4)
        print(f"fine search: lags {lo*2}..{hi*2} bytes at 2-byte step")
        best_lag, scores = find_frame_period(samples, lo=lo, hi=hi, step=1)
        best_lag_bytes = best_lag * 2
        print(f"fine-search peak at {best_lag_bytes} bytes (offset from center: {best_lag_bytes - args.fine_near:+d})")
    elif args.frame_bytes is not None:
        if args.frame_bytes % 2:
            raise SystemExit("--frame-bytes must be even (uint16 samples)")
        best_lag = args.frame_bytes // 2
        best_lag_bytes = args.frame_bytes
        scores = np.array([])
        print(f"frame size forced to {best_lag_bytes} bytes (no autocorr search)")
    else:
        lo = (args.min_frame_kb * 1024) // 2
        hi = min((args.max_frame_kb * 1024) // 2, samples.size // 4)
        print(f"searching autocorrelation lags {lo*2}..{hi*2} bytes (in 256-byte steps)")
        best_lag, scores = find_frame_period(samples, lo=lo, hi=hi, step=128)
        best_lag_bytes = best_lag * 2
        print(f"strongest autocorrelation at lag {best_lag} samples = {best_lag_bytes} bytes")
        print(f"  (suggests frame size {best_lag_bytes} B = {best_lag_bytes/256/2:.2f} rows at 256-wide uint16)")

    frames = slice_into_frames(samples, best_lag)
    print(f"  -> {frames.shape[0]} whole frames, {frames.shape[1]} samples each")

    # 2) Per-byte temporal statistic.
    sidecar = args.stream.with_suffix(".json")
    if args.correlate and sidecar.exists():
        info = json.loads(sidecar.read_text())
        events = info.get("stimulus_events") or []
        mask = build_stimulus_mask(events, frames.shape[0], info["elapsed_s"])
        if mask is None:
            print("WARNING: no stimulus_events in sidecar; falling back to std")
            score = frames.std(axis=0)
        else:
            # Pearson correlation between each byte position and the stimulus.
            f = frames.astype(np.float64)
            f -= f.mean(axis=0, keepdims=True)
            m = mask - mask.mean()
            num = (f * m[:, None]).sum(axis=0)
            den = np.sqrt((f * f).sum(axis=0)) * np.sqrt((m * m).sum()) + 1e-9
            score = np.abs(num / den)
            print(f"using stimulus correlation (mask events: {len(events)})")
    else:
        score = frames.std(axis=0)
        print("using per-byte temporal std across frames")

    # 3) Reshape to (rows, WIDTH) for visualization.
    rows = score.size // WIDTH
    score_2d = score[: rows * WIDTH].reshape(rows, WIDTH)
    print(f"score map shaped as ({rows}, {WIDTH})")

    # 4) Plot.
    if scores.size:
        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        step_samples = 1 if args.fine_near is not None else 128
        xs = (lo + np.arange(scores.size) * step_samples) * 2  # bytes
        axes[0].plot(xs, scores)
        axes[0].axvline(best_lag_bytes, color="r", linestyle="--", label=f"peak @ {best_lag_bytes} B")
        axes[0].set_xlabel("candidate frame size (bytes)")
        axes[0].set_ylabel("normalized autocorrelation")
        axes[0].set_title("frame-period search")
        axes[0].legend()
        ax_map = axes[1]
    else:
        fig, ax_map = plt.subplots(1, 1, figsize=(7, 6))

    im = ax_map.imshow(score_2d, cmap="viridis", aspect="auto")
    ax_map.set_title(f"per-byte stimulus response @ frame size {best_lag_bytes} B\n(bright = real pixel, dark = static)")
    plt.colorbar(im, ax=ax_map)

    out_png = args.stream.with_suffix(".analysis.png")
    plt.tight_layout()
    plt.savefig(out_png, dpi=120)
    print(f"wrote {out_png}")
    plt.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
