from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    out_dir = tempfile.mkdtemp(prefix="imaging-monitor-")
    try:
        cmd = [
            sys.executable,
            "-m",
            "imaging_monitor.cli",
            "run",
            "--fixture-dir",
            "tests/fixtures",
            "--out-dir",
            out_dir,
            "--dry-run",
            "--skip-push",
            "--no-ai",
        ]
        result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr)
            return result.returncode
        with open(os.path.join(out_dir, "events.json"), encoding="utf-8") as f:
            events = json.load(f)
        with open(os.path.join(out_dir, "report.json"), encoding="utf-8") as f:
            report = json.load(f)
        with open(os.path.join(out_dir, "quality_report.json"), encoding="utf-8") as f:
            quality = json.load(f)
        assert events, "events.json should not be empty"
        assert report["top5"], "report top5 should not be empty"
        required = {"id", "title", "title_zh", "title_original", "region", "language", "brand", "category", "summary_zh", "source_url", "evidence", "push_decision"}
        missing = [required - set(e) for e in events]
        assert not any(missing), f"missing required fields: {missing}"
        assert any(e["region"] == "domestic" for e in events), "should include domestic events"
        assert any(e["region"] == "global" for e in events), "should include global events"
        assert any(e["language"] == "zh" for e in events), "should include zh events"
        assert any(e["language"] == "en" for e in events), "should include en events"
        assert all(e["evidence"] for e in events), "events should have evidence"
        assert "events_without_evidence" in quality, "quality report should include evidence metric"
        assert report["sections"], "report should include grouped sections"
        # value-domain checks
        assert all(1 <= int(e["importance_score"]) <= 5 for e in events), "importance_score out of [1,5]"
        assert all(0.0 <= float(e["confidence_score"]) <= 1.0 for e in events), "confidence_score out of [0,1]"
        # cross-run state fields are present and first run marks everything new
        assert all("is_new" in e and "first_seen" in e for e in events), "events missing is_new/first_seen"
        assert "battlecards" in report and "trend" in report, "report missing battlecards/trend"
        print("smoke ok", result.stdout)
        return 0
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
