"""Read-only session telemetry. Does not capture audio or change volume."""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audio import AudioController


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--process", default="SCUM")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("Duration must be between 1 and 600 seconds")
    directory = Path(__file__).resolve().parents[1] / "logs" / "sound_tests"
    directory.mkdir(parents=True, exist_ok=True)
    safe_phase = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.phase)
    path = directory / f"{datetime.now():%Y%m%d-%H%M%S}-{safe_phase}.csv"
    audio = AudioController()
    start = time.monotonic()
    rows = []
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time", "elapsed_s", "phase", "session_peak", "mixer_percent"])
        while time.monotonic() - start < args.seconds:
            peak = audio.get_peak(args.process)
            volume = audio.get_volume(args.process)
            writer.writerow([datetime.now().isoformat(timespec="milliseconds"),
                             round(time.monotonic() - start, 3), args.phase, peak, volume])
            stream.flush()
            if peak is not None:
                rows.append(peak)
            time.sleep(0.08)
    summary = {"phase": args.phase, "csv": str(path), "samples": len(rows),
               "note": "Statistics of sampled session peaks, NOT waveform RMS or LUFS."}
    if rows:
        ordered = sorted(rows)
        summary.update(mean_peak=statistics.mean(rows), max_peak=max(rows),
                       p50=ordered[int((len(rows)-1)*.5)],
                       p95=ordered[int((len(rows)-1)*.95)],
                       peak_envelope_rms=math.sqrt(statistics.mean(x*x for x in rows)))
    path.with_suffix(".json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
