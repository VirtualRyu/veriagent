"""Supported Codex registration and observed MCP process status."""
import atexit
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .authoring import atomic_json
from .state import resource_path


def find_codex():
    found = shutil.which("codex")
    if found:
        return found
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "OpenAI/Codex/bin"
    candidates = list(base.glob("*/codex.exe"))
    candidates += list((Path.home() / ".vscode/extensions").glob("openai.chatgpt-*/bin/windows-x86_64/codex.exe"))
    return str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None


def run(command):
    return subprocess.run(command, capture_output=True, text=True, timeout=30,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def server_command(home):
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).parent / "mcp/VeriAgentMCP.exe"
        if not executable.is_file():
            raise ValueError("Packaged MCP executable is missing. Reinstall the complete bundle.")
        return [str(executable), "--home", str(home)]
    return [sys.executable, str(resource_path("packaging/server_entry.py")), "--home", str(home)]


def registration_command(home):
    codex = find_codex()
    if not codex:
        raise ValueError("Codex CLI is not available. Install or open the supported Codex client first.")
    return [codex, "mcp", "add", "veriagent", "--env", f"VERIAGENT_HOME={home}", "--", *server_command(home)]


def register(home):
    result = run(registration_command(home))
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Codex registration failed")
    return "Configured. Reopen/restart the Codex client once to load the server. Rule Apply then refreshes automatically."


def status(home):
    codex = find_codex()
    data = {"configuration": "Setup required", "backend": "No running MCP process observed", "last_request": None,
            "state_directory": str(home), "codex": codex, "processes": []}
    if codex:
        try:
            result = run([codex, "mcp", "get", "veriagent", "--json"])
            if result.returncode == 0:
                config = json.loads(result.stdout)
                transport = config.get("transport") or {}
                explicit = (transport.get("env") or {}).get("VERIAGENT_HOME")
                args = transport.get("args") or []
                if not explicit and "--home" in args:
                    explicit = args[args.index("--home") + 1]
                if explicit:
                    data["configuration"] = "Configured" if Path(explicit).resolve() == home.resolve() else "Attention required: configured server uses a different state directory"
                else:
                    data["configuration"] = "Attention required: existing registration has no explicit state; review/update it"
                    if "-File" in args and args.index("-File") + 1 < len(args):
                        script = Path(args[args.index("-File") + 1])
                        suggested = script.parent / "runtime"
                        if script.name == "start_server.ps1" and (suggested / "demo.sqlite3").is_file():
                            data["suggested_existing_state"] = str(suggested.resolve())
                if config.get("enabled") is False:
                    data["configuration"] = "Attention required: VeriAgent registration is disabled"
            else:
                data["configuration"] = "Setup required: VeriAgent is not registered"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            data["configuration"] = f"Attention required: {exc}"
    else:
        data["configuration"] = "Setup required: supported Codex CLI not found; no client installed automatically"
    for path in (home / "sessions").glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["home"] == str(home.resolve()) and time.time() - record["heartbeat"] < 15:
                data["processes"].append(record)
        except (OSError, ValueError, KeyError):
            continue
    if data["processes"]:
        data["backend"] = "Backend available: running MCP process observed (client loading not assumed)"
        requests = [p["last_request"] for p in data["processes"] if p.get("last_request")]
        data["last_request"] = max(requests, key=lambda r: r["time"]) if requests else None
    return data


class Session:
    """Heartbeat for this actual stdio process; never starts a probe server."""
    def __init__(self, home):
        self.path = home / "sessions" / f"{os.getpid()}.json"
        self.data = {"pid": os.getpid(), "home": str(home.resolve()), "last_request": None}
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.flush()
        threading.Thread(target=self.loop, daemon=True).start()
        atexit.register(self.close)

    def flush(self):
        with self.lock:
            self.data["heartbeat"] = time.time()
            atomic_json(self.path, self.data)

    def received(self, tool):
        with self.lock:
            self.data["last_request"] = {"tool": tool, "time": time.time()}
        self.flush()

    def loop(self):
        while not self.stopped.wait(5):
            try:
                self.flush()
            except OSError:
                pass

    def close(self):
        self.stopped.set()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
