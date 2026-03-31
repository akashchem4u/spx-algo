#!/usr/bin/env python3
"""
Run a lightweight validation gate for spx-algo and write durable review artifacts.

Outputs:
- Codex/validation-artifacts/validation-YYYYMMDD-HHMMSS.{json,md}
- optionally Codex/session-reviews/session-review-YYYYMMDD-HHMMSS.md

This script is intentionally external to the Streamlit app so the validation flow
does not depend on importing app.py or touching live market providers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pytz


ROOT = Path(__file__).resolve().parents[1]
CODEX_DIR = ROOT / "Codex"
VALIDATION_DIR = CODEX_DIR / "validation-artifacts"
SESSION_DIR = CODEX_DIR / "session-reviews"
EST = pytz.timezone("America/Chicago")


@dataclass
class CommandResult:
    label: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run spx-algo validation and write review artifacts"
    )
    parser.add_argument(
        "--profile",
        choices=["local", "behavior", "release"],
        default="local",
        help=(
            "local=syntax gate only, "
            "behavior=syntax gate plus manual backtest evidence note, "
            "release=syntax gate plus stronger release/backtest note"
        ),
    )
    parser.add_argument(
        "--write-session-review",
        action="store_true",
        help="Also write a dated session-review note in Codex/session-reviews",
    )
    parser.add_argument("--summary", default="", help="One-line session summary")
    parser.add_argument(
        "--evidence-note",
        action="append",
        default=[],
        help="Freeform note about backtests, manual checks, or validation evidence",
    )
    parser.add_argument(
        "--done",
        action="append",
        default=[],
        help="Item to record as done in the session review",
    )
    parser.add_argument(
        "--partial",
        action="append",
        default=[],
        help="Item to record as partial in the session review",
    )
    parser.add_argument(
        "--open",
        action="append",
        default=[],
        help="Item to record as open in the session review",
    )
    parser.add_argument(
        "--recent-commits",
        type=int,
        default=6,
        help="How many recent commits to include in the artifact",
    )
    return parser.parse_args(argv)


def _run_command(label: str, command: list[str]) -> CommandResult:
    proc = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        label=label,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout.strip(),
        stderr=proc.stderr.strip(),
    )


def _git_output(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _read_app_metadata() -> dict[str, str]:
    app_text = (ROOT / "app.py").read_text(encoding="utf-8")
    model_match = re.search(r'_model_ver\s*=\s*"([^"]+)"', app_text)
    gap_match = re.search(r"GAP_THRESHOLD\s*=\s*([0-9.]+)", app_text)
    return {
        "model_version": model_match.group(1) if model_match else "unknown",
        "gap_threshold": gap_match.group(1) if gap_match else "unknown",
    }


def _artifact_stamp(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S")


def _ensure_dirs() -> None:
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def _lines(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- none"


def _profile_expectation(profile: str) -> str:
    if profile == "local":
        return "syntax-only gate; no network-backed market validation required"
    if profile == "behavior":
        return "syntax gate plus backtest/report evidence note for behavior changes"
    return "release candidate gate; requires durable backtest/report evidence before trust claims"


def _default_open_items(profile: str) -> list[str]:
    if profile == "local":
        return []
    if profile == "behavior":
        return ["attach walk-forward/backtest evidence for the behavior change"]
    return ["attach release-grade backtest artifact before calling the build live-safe"]


def _run_backtest_export(days: int = 60) -> tuple[dict | None, CommandResult]:
    """
    Invoke scripts/backtest_export.py as a subprocess and return the parsed JSON
    result alongside the raw CommandResult.  Returns (None, result) if stdout
    cannot be parsed.
    """
    cmd = _run_command(
        "backtest_export",
        [sys.executable, "scripts/backtest_export.py", "--days", str(days)],
    )
    parsed: dict | None = None
    if cmd.stdout:
        try:
            parsed = json.loads(cmd.stdout)
        except Exception as exc:
            parsed = {"ok": False, "error": f"JSON parse failed: {exc}", "raw": cmd.stdout[:400]}
    else:
        parsed = {"ok": False, "error": cmd.stderr or "no stdout from backtest_export"}
    return parsed, cmd


def build_artifact_payload(args: argparse.Namespace) -> tuple[dict, list[CommandResult]]:
    now = datetime.now(EST)
    syntax_cmd = _run_command("py_compile", [sys.executable, "-m", "py_compile", "app.py"])
    # Commands that gate the overall ok flag
    gate_commands: list[CommandResult] = [syntax_cmd]

    # --- Backtest export (behavior + release profiles) -----------------------
    backtest_result: dict | None = None
    backtest_cmd: CommandResult | None = None
    if args.profile in ("behavior", "release"):
        backtest_result, backtest_cmd = _run_backtest_export(days=60)
        # For release profile the backtest must pass to gate ok.
        # For behavior profile we attach it as evidence but don't hard-fail.
        if args.profile == "release" and backtest_cmd is not None:
            gate_commands.append(backtest_cmd)

    git_status = _git_output("status", "--short")
    recent_commits = _git_output("log", "--oneline", f"-n{args.recent_commits}")
    head_short = _git_output("rev-parse", "--short", "HEAD")
    head_long = _git_output("rev-parse", "HEAD")
    metadata = _read_app_metadata()

    all_commands = gate_commands[:]
    if backtest_cmd is not None and backtest_cmd not in gate_commands:
        all_commands.append(backtest_cmd)

    payload = {
        "generated_at": now.isoformat(),
        "timezone": "America/Chicago",
        "project": str(ROOT),
        "profile": args.profile,
        "profile_expectation": _profile_expectation(args.profile),
        "head_short": head_short,
        "head_long": head_long,
        "worktree_clean": not bool(git_status.strip()),
        "git_status": git_status.splitlines() if git_status else [],
        "recent_commits": recent_commits.splitlines() if recent_commits else [],
        "commands": [
            {
                "label": item.label,
                "command": item.command,
                "returncode": item.returncode,
                "ok": item.ok,
                "stdout": item.stdout,
                "stderr": item.stderr,
            }
            for item in all_commands
        ],
        "ok": all(item.ok for item in gate_commands),
        "app_metadata": metadata,
        "evidence_notes": args.evidence_note,
        "artifact_checks": {
            "ablation_report_present": (CODEX_DIR / "ablation-report.md").exists(),
            "shadow_ledger_present": (CODEX_DIR / "shadow-ledger.csv").exists(),
        },
        "summary": args.summary.strip(),
        "backtest_result": backtest_result,
    }
    return payload, all_commands


def write_validation_artifacts(payload: dict) -> tuple[Path, Path]:
    _ensure_dirs()
    stamp = _artifact_stamp(datetime.fromisoformat(payload["generated_at"]))
    json_path = VALIDATION_DIR / f"validation-{stamp}.json"
    md_path = VALIDATION_DIR / f"validation-{stamp}.md"

    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    command_lines = []
    for item in payload["commands"]:
        status = "PASS" if item["ok"] else "FAIL"
        command_lines.append(f"- `{status}` `{item['label']}`: `{' '.join(item['command'])}`")
        if item["stdout"] and item["label"] != "backtest_export":
            # backtest stdout is a large JSON blob — summarised separately below
            command_lines.append(f"  - stdout: `{item['stdout']}`")
        if item["stderr"]:
            command_lines.append(f"  - stderr: `{item['stderr']}`")

    # ── Backtest section (behavior / release profiles) ─────────────────────
    bt = payload.get("backtest_result")
    if bt is not None:
        bt_status = "PASS" if bt.get("ok") else "FAIL"
        bt_acc    = bt.get("accuracy", "n/a")
        bt_hits   = bt.get("hits", "n/a")
        bt_total  = bt.get("total", "n/a")
        bt_thresh = bt.get("threshold", "n/a")
        bt_regime = bt.get("regime", "n/a")
        bt_vix    = bt.get("vix_last", "n/a")
        bt_err    = bt.get("error", "")
        bt_recent = bt.get("recent_results", [])
        bt_lines: list[str] = [
            "## Backtest Export",
            "",
            f"- status: `{bt_status}`",
            f"- accuracy: `{bt_acc}` (threshold: `{bt_thresh}`)",
            f"- hits / evaluated: `{bt_hits}` / `{bt_total}`",
            f"- VIX at eval: `{bt_vix}` → regime `{bt_regime}`",
        ]
        if bt_err:
            bt_lines.append(f"- error: `{bt_err}`")
        if bt_recent:
            bt_lines += [
                "",
                "### Recent Results (last 5 directional days)",
                "",
            ]
            for row in bt_recent:
                tick      = "✓" if row.get("correct") else "✗"
                direction = "BULL" if row.get("bull") else "BEAR"
                outcome   = "UP" if row.get("up") else "DN"
                bt_lines.append(
                    f"- `{row.get('date','?')}` score={row.get('score','?')} "
                    f"{direction}→{outcome} {tick}"
                )
        bt_lines.append("")
    else:
        bt_lines = []

    md = "\n".join(
        [
            "# Validation Artifact",
            "",
            f"Generated: `{payload['generated_at']}`",
            f"Profile: `{payload['profile']}`",
            f"Head: `{payload['head_short']}`",
            f"Result: `{'PASS' if payload['ok'] else 'FAIL'}`",
            "",
            "## Summary",
            "",
            payload["summary"] or "_No summary provided._",
            "",
            "## Validation Scope",
            "",
            f"- {payload['profile_expectation']}",
            "",
            "## Commands",
            "",
            *command_lines,
            "",
            *bt_lines,
            "## Repo State",
            "",
            f"- worktree clean: `{'yes' if payload['worktree_clean'] else 'no'}`",
            f"- model version: `{payload['app_metadata']['model_version']}`",
            f"- gap threshold: `{payload['app_metadata']['gap_threshold']}`",
            "",
            "## Artifact Checks",
            "",
            f"- `Codex/ablation-report.md`: `{'present' if payload['artifact_checks']['ablation_report_present'] else 'missing'}`",
            f"- `Codex/shadow-ledger.csv`: `{'present' if payload['artifact_checks']['shadow_ledger_present'] else 'missing'}`",
            "",
            "## Evidence Notes",
            "",
            *([f"- {note}" for note in payload["evidence_notes"]] or ["- none"]),
            "",
            "## Recent Commits",
            "",
            *([f"- `{line}`" for line in payload["recent_commits"]] or ["- none"]),
            "",
        ]
    )
    md_path.write_text(md + "\n", encoding="utf-8")
    return json_path, md_path


def write_session_review(
    args: argparse.Namespace,
    payload: dict,
    validation_md_path: Path,
) -> Path:
    _ensure_dirs()
    stamp = _artifact_stamp(datetime.fromisoformat(payload["generated_at"]))
    review_path = SESSION_DIR / f"session-review-{stamp}.md"
    open_items = list(args.open) + _default_open_items(args.profile)
    md = "\n".join(
        [
            "# Session Review",
            "",
            f"Generated: `{payload['generated_at']}`",
            f"Project: `{ROOT}`",
            f"Validation artifact: `{validation_md_path.relative_to(ROOT)}`",
            "",
            "## Summary",
            "",
            args.summary.strip() or "_No summary provided._",
            "",
            "## Validation",
            "",
            f"- profile: `{payload['profile']}`",
            f"- result: `{'PASS' if payload['ok'] else 'FAIL'}`",
            f"- worktree clean at validation time: `{'yes' if payload['worktree_clean'] else 'no'}`",
            *(
                [
                    f"- backtest accuracy: `{payload['backtest_result'].get('accuracy', 'n/a')}` "
                    f"({'PASS' if payload['backtest_result'].get('ok') else 'FAIL'})",
                    f"- backtest regime: `{payload['backtest_result'].get('regime', 'n/a')}`",
                ]
                if payload.get("backtest_result") is not None else []
            ),
            "",
            "## Done",
            "",
            _lines(args.done),
            "",
            "## Partial",
            "",
            _lines(args.partial),
            "",
            "## Open",
            "",
            _lines(open_items),
            "",
            "## Evidence Notes",
            "",
            _lines(payload["evidence_notes"]),
            "",
            "## Recent Commits",
            "",
            _lines([f"`{line}`" for line in payload["recent_commits"]]),
            "",
        ]
    )
    review_path.write_text(md + "\n", encoding="utf-8")
    return review_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload, _commands = build_artifact_payload(args)
    json_path, md_path = write_validation_artifacts(payload)
    session_path = None
    if args.write_session_review:
        session_path = write_session_review(args, payload, md_path)

    print(json.dumps(
        {
            "ok": payload["ok"],
            "validation_json": str(json_path),
            "validation_md": str(md_path),
            "session_review": str(session_path) if session_path else "",
        },
        indent=2,
    ))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
