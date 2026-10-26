"""Reproducible network audit of RecallPlug.

    python -m tools.net_audit --minutes 10

Starts "python -m app.main --demo" itself and, every second, records the
inet connections of that process and all its children (the HUD). Each
remote address is classified as loopback (127.0.0.0/8, ::1) or external.
Connections of any process whose name starts with "ollama" are listed
separately as informational. At the end the app is stopped, a summary is
printed and bench/net_audit/<timestamp>.txt is written (counts and
addresses only). Exit code 0 if the RecallPlug process tree made no
external connection, 1 if it did, 2 if psutil is missing.

Sampling once a second can miss a connection that opens and closes in
between; the report says so.
"""
from __future__ import annotations

import argparse
import ipaddress
import subprocess
import sys
import time
from pathlib import Path

try:                       # optional: pip install psutil
    import psutil
except Exception:          # pragma: no cover
    psutil = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "bench" / "net_audit"
GROUPS = ("recallplug", "ollama")
INSTALL_HINT = "psutil is needed for the network audit. Install it with:  python -m pip install psutil"


# ------------------------------------------------------------ pure parts
def classify(ip: str) -> str:
    """'loopback' for 127.0.0.0/8, ::1 (and ::ffff:127.x.x.x), else 'external'.
    Anything that is not a valid address counts as external."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return "external"
    mapped = getattr(addr, "ipv4_mapped", None)
    return "loopback" if (mapped or addr).is_loopback else "external"


def endpoint(ip: str, port: int) -> str:
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def summarise(samples: list[dict]) -> dict:
    """samples: one dict per second, {group: [(remote ip, remote port), ...]}.
    Returns per group: observations per class, unique remote addresses per
    class and the most connections seen in a single sample."""
    out = {"samples": len(samples)}
    for group in GROUPS:
        counts = {"loopback": 0, "external": 0}
        addrs: dict[str, set] = {"loopback": set(), "external": set()}
        peak = 0
        for sample in samples:
            conns = sample.get(group, [])
            peak = max(peak, len(conns))
            for ip, port in conns:
                kind = classify(ip)
                counts[kind] += 1
                addrs[kind].add(endpoint(ip, port))
        out[group] = {"counts": counts, "addresses": {k: sorted(v) for k, v in addrs.items()},
                      "peak": peak}
    return out


def exit_code(summary: dict) -> int:
    """0 if the RecallPlug process tree made no external connection, else 1."""
    return 1 if summary["recallplug"]["counts"]["external"] else 0


def format_report(summary: dict, minutes: float, started: str) -> str:
    """Counts and addresses only (no window titles, no model text)."""
    app, oll = summary["recallplug"], summary["ollama"]
    verdict = ("PASS: no external connection from the RecallPlug process tree"
               if exit_code(summary) == 0 else
               "FAIL: the RecallPlug process tree made an external connection")
    lines = [
        "RecallPlug network audit",
        f"started: {started}",
        f"planned minutes: {minutes:g}",
        f"samples (1 per second): {summary['samples']}",
        "",
        "RecallPlug process tree (app.main --demo and its children, e.g. the HUD):",
        f"  loopback observations: {app['counts']['loopback']}",
        f"  external observations: {app['counts']['external']}",
        f"  most connections in one sample: {app['peak']}",
        "  loopback addresses: " + (", ".join(app["addresses"]["loopback"]) or "none"),
        "  external addresses: " + (", ".join(app["addresses"]["external"]) or "none"),
        "",
        "Ollama processes (informational, not part of the verdict):",
        f"  loopback observations: {oll['counts']['loopback']}",
        f"  external observations: {oll['counts']['external']}",
        f"  most connections in one sample: {oll['peak']}",
        "  loopback addresses: " + (", ".join(oll["addresses"]["loopback"]) or "none"),
        "  external addresses: " + (", ".join(oll["addresses"]["external"]) or "none"),
        "",
        verdict,
        "Note: sampling once a second can miss a connection that opens and closes in between.",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------ psutil parts
def _remotes(proc) -> list[tuple[str, int]]:
    """Remote (ip, port) of every inet connection that has a remote end."""
    get = getattr(proc, "net_connections", None) or proc.connections   # psutil >= 6 / older
    try:
        conns = get(kind="inet")
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return []
    return [(c.raddr.ip, c.raddr.port) for c in conns if c.raddr]


def _tree(root) -> list:
    try:
        return [root] + root.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return [root]


def _ollama_procs() -> list:
    found = []
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower().startswith("ollama"):
            found.append(p)
    return found


def take_sample(root) -> dict:
    return {"recallplug": [r for p in _tree(root) for r in _remotes(p)],
            "ollama": [r for p in _ollama_procs() for r in _remotes(p)]}


def stop_tree(root) -> None:
    procs = _tree(root)
    for p in reversed(procs):              # children first
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=5)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def report_path(base: Path) -> Path:
    """bench/net_audit/<timestamp>.txt, never overwriting an earlier report."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = base / f"{stamp}.txt"
    n = 2
    while path.exists():
        path = base / f"{stamp}_{n}.txt"
        n += 1
    return path


def run_audit(minutes: float) -> int:
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    cmd = [sys.executable, "-m", "app.main", "--demo"]
    print(f"[net_audit] starting: {' '.join(cmd[1:])} for {minutes:g} min (Ctrl+C stops early)")
    child = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))
    root = psutil.Process(child.pid)
    samples: list[dict] = []
    end = time.time() + minutes * 60
    try:
        while time.time() < end and child.poll() is None:
            samples.append(take_sample(root))
            time.sleep(1)
    except KeyboardInterrupt:
        print("[net_audit] stopped early")
    finally:
        stop_tree(root)
    if child.poll() is None:
        child.wait(timeout=5)

    summary = summarise(samples)
    text = format_report(summary, minutes, started)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = report_path(OUT_DIR)
    path.write_text(text, encoding="utf-8")
    print()
    print(text)
    print(f"[net_audit] wrote {path}")
    return exit_code(summary)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RecallPlug network audit")
    ap.add_argument("--minutes", type=float, default=10, help="how long to watch (default 10)")
    args = ap.parse_args(argv)
    if psutil is None:
        print(INSTALL_HINT)
        return 2
    return run_audit(max(0.1, args.minutes))


if __name__ == "__main__":
    sys.exit(main())
