"""Install repository hooks and run the repository's shared quality command."""

import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile

from ub_cli.project import git, local_file, repository, write_file


GIT_CHECK = '''# ub:begin
(
    ub_check_root=$(git rev-parse --show-toplevel) || exit $?
    "$ub_check_root/.ub/check" </dev/null
) || exit $?
# ub:end
'''
AGENT_FILES = {"codex": ".codex/hooks.json", "claude": ".claude/settings.json"}


def hook_path(root: Path) -> Path:
    path = Path(git(root, "rev-parse", "--git-path", "hooks/pre-push"))
    return path if path.is_absolute() else root / path


def install_git(root: Path) -> None:
    hook = hook_path(root)
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if not (hook.resolve().is_relative_to(root) or hook.resolve().is_relative_to(common)):
        raise ValueError("Git hooks directory is outside this repository. Integrate ./.ub/check in that hook manually.")
    if hook.is_symlink() or (hook.exists() and not hook.is_file()):
        raise ValueError(f"Cannot wrap a symlink or non-file hook: {hook}")
    original = hook.read_text() if hook.exists() else ""
    if "# ub:begin" in original or "# ub:end" in original:
        if original.count(GIT_CHECK) != 1:
            raise ValueError(f"Edited UB hook block needs review: {hook}")
        hook.chmod(0o755)
        return
    backup = hook.with_name("pre-push.ub-original")
    if backup.exists() or backup.is_symlink():
        raise ValueError(f"Existing hook backup needs review: {backup}")
    script = "#!/bin/sh\n" + GIT_CHECK
    if hook.exists() and os.access(hook, os.X_OK):
        first, _, rest = original.partition("\n")
        interpreter = shlex.split(first[2:]) if first.startswith("#!") else []
        if interpreter and Path(interpreter[0]).name == "env":
            interpreter = interpreter[1:]
        if not interpreter or Path(interpreter[0]).name not in {"sh", "bash", "dash", "ksh", "zsh"}:
            raise ValueError(f"Existing hook is not a supported shell script: {hook}. Add ./.ub/check to it manually.")
        # The original still runs at its original path, with its argv and stdin intact.
        script = first + "\n" + GIT_CHECK + rest
    if hook.exists():
        os.link(hook, backup)
    write_file(hook, script, 0o755)


def install_agent(root: Path, target: str, timeout: int) -> None:
    path = local_file(root, AGENT_FILES[target])
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected a hooks object in {path}")
    groups = hooks.setdefault("Stop", [])
    if not isinstance(groups, list):
        raise ValueError(f"Expected a Stop array in {path}")
    command = (f"ub hooks run stop --timeout {timeout} || "
               "{ printf '%s\\n' 'UB hook could not start. Ensure ub is installed and on PATH.' >&2; exit 2; }")
    handler = {"type": "command", "command": command, "timeout": timeout + 10}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            raise ValueError(f"Invalid Stop hook group in {path}")
        for existing in group["hooks"]:
            if not isinstance(existing, dict):
                raise ValueError(f"Invalid Stop hook handler in {path}")
            existing_command = existing.get("command")
            if existing.get("type") == "command" and isinstance(existing_command, str) and existing_command.startswith("ub hooks run stop --timeout "):
                if existing != handler:
                    raise ValueError(f"UB hook already differs in {path}; edit it directly.")
                return
    groups.append({"hooks": [handler]})
    write_file(path, json.dumps(data, indent=2) + "\n")


def install(options) -> int:
    root = repository(options.root)
    check = local_file(root, ".ub/check")
    if not check.is_file() or not os.access(check, os.X_OK):
        raise ValueError("Run ub init at the repository root before installing hooks.")
    for target in dict.fromkeys(options.target or ["git"]):
        if target == "git":
            install_git(root)
        else:
            install_agent(root, target, options.timeout)
        print(f"Ready: {target} hook in {root}")
    if any(target in AGENT_FILES for target in options.target or []):
        print("Restart the agent and review its /hooks configuration; Codex requires hook trust.")
        print("Stop hooks run .ub/check on completion. Repeated stop-hook continuations are skipped.")
    return 0


def terminate_check(process) -> None:
    """Stop the check and descendants, including tests that start new sessions."""
    stopped = set()
    try:
        os.killpg(process.pid, signal.SIGSTOP)
        while True:
            table = subprocess.run(["ps", "-e", "-o", "pid=", "-o", "ppid="],
                                   capture_output=True, text=True, check=True)
            parents = {int(pid): int(parent) for pid, parent in
                       (line.split() for line in table.stdout.splitlines())}
            descendants = {process.pid}
            while True:
                children = {pid for pid, parent in parents.items() if parent in descendants}
                if children <= descendants:
                    break
                descendants.update(children)
            new = descendants - stopped
            if not new:
                break
            for pid in new:
                try:
                    os.kill(pid, signal.SIGSTOP)
                except ProcessLookupError:
                    pass
            stopped.update(new)
    finally:
        for pid in stopped - {process.pid}:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def check_output(root: Path, timeout: int) -> tuple[int, str]:
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen([str(root / ".ub/check")], cwd=root, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=output, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_check(process)
            return 2, f"UB check exceeded {timeout}s. Run ./.ub/check manually and report the result."
        output.seek(max(0, output.tell() - 8000))
        return code, output.read().decode(errors="replace")


def run_stop(timeout: int) -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Expected a hook event JSON object.")
        if payload.get("stop_hook_active") is True:
            print(json.dumps({"systemMessage": "UB continuation guard: report any unresolved check failures."}))
            return 0
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("Hook event is missing cwd.")
        root = repository(Path(cwd))
        code, output = check_output(root, timeout)
        if code == 0:
            print("{}")
            return 0
        reason = f"UB checks failed (exit {code}). Fix failures within the task scope or report the blocker.\n{output}"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = f"UB checks could not run: {exc}"
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ub hooks", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install", help="Install a Git pre-push or agent Stop hook")
    setup.add_argument("--root", type=Path, default=Path.cwd())
    setup.add_argument("--target", choices=["git", *AGENT_FILES], action="append", help="Repeat to select targets; default: git")
    setup.add_argument("--timeout", type=int, default=120, help="Agent check timeout in seconds (default: 120)")
    execute = commands.add_parser("run", help="Hook entry point (called by installed hooks)")
    runners = execute.add_subparsers(dest="event", required=True)
    stop = runners.add_parser("stop")
    stop.add_argument("--timeout", type=int, default=120)
    try:
        options = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        if getattr(options, "timeout", 1) < 1:
            raise ValueError("--timeout requires a positive integer")
        if options.command == "install":
            return install(options)
        return run_stop(options.timeout)
    except (OSError, ValueError) as exc:
        print(f"ub hooks: {exc}", file=sys.stderr)
        return 1
