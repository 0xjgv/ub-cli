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


LEGACY_GIT_CHECK = '''# ub:begin
(
    ub_check_root=$(git rev-parse --show-toplevel) || exit $?
    "$ub_check_root/.ub/check" </dev/null
) || exit $?
# ub:end
'''
# The guard runs from the installed ub, not .ub/check, which the pushed commits can edit.
# Each pushed branch is compared with the remote's default branch, as CI compares it; failing
# that, with the remote's copy of the branch or its upstream. Only a remote with no known
# branches skips the guard. Git's ref list on stdin is replayed for the rest of the hook.
GIT_CHECK = '''# ub:begin
ub_refs=$(mktemp) || exit $?
cat > "$ub_refs" || exit $?
(
    ub_check_root=$(git rev-parse --show-toplevel) || exit $?
    ub_default=$(git rev-parse --verify -q "refs/remotes/$1/HEAD") || ub_default=
    ub_known=$(git for-each-ref --count=1 --format=known "refs/remotes/$1/")
    while read -r ub_local ub_sha ub_remote ub_remote_sha; do
        case $ub_remote in refs/heads/*) ;; *) continue ;; esac
        case $ub_sha in *[!0]*) ;; *) continue ;; esac
        ub_base=$ub_default
        case $ub_remote_sha in *[!0]*) ub_base=${ub_base:-$ub_remote_sha} ;; esac
        [ -n "$ub_base" ] || ub_base=$(git rev-parse --verify -q "$ub_local@{upstream}") || ub_base=
        if [ -z "$ub_base" ] && [ -n "$ub_known" ]; then
            echo "ub: no base to guard $ub_remote; run: git remote set-head $1 --auto" >&2
            exit 2
        elif [ -z "$ub_base" ]; then
            echo "ub: $1 has no known branches; skipping the .ub/ tamper guard." >&2
            continue
        fi
        ub ratchet --root "$ub_check_root" --guard --base "$ub_base" --head "$ub_sha" </dev/null || exit $?
    done < "$ub_refs"
    "$ub_check_root/.ub/check" </dev/null
) || { ub_status=$?; rm -f "$ub_refs"; exit $ub_status; }
exec < "$ub_refs"
rm -f "$ub_refs"
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
        if original.count(LEGACY_GIT_CHECK) == 1:
            write_file(hook, original.replace(LEGACY_GIT_CHECK, GIT_CHECK), 0o755)
            return
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
            # int() reads the byte strings directly.
            table = subprocess.run(["ps", "-e", "-o", "pid=", "-o", "ppid="], capture_output=True, check=True)
            parents = {int(pid): int(parent) for pid, parent in
                       (line.split() for line in table.stdout.splitlines())}
            descendants = {process.pid}
            while True:
                children = {pid for pid, parent in parents.items() if parent in descendants}
                if not children - descendants:
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


UB_CHANGED = "ub config has uncommitted changes: review .ub/ before committing."


def stop_decision(payload, timeout: int) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Expected a hook event JSON object.")
    if payload.get("stop_hook_active") is True:
        return {"systemMessage": "UB continuation guard: report any unresolved check failures."}
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("Hook event is missing cwd.")
    code, output = check_output(repository(Path(cwd)), timeout)
    if code == 0:
        return {}
    return {"decision": "block",
            "reason": f"UB checks failed (exit {code}). Fix failures within the task scope or report the blocker.\n{output}"}


def ub_changed(payload) -> bool:
    """Whether .ub/ has staged, unstaged, or untracked changes, or files that differ from HEAD.

    Unknown counts as no.
    """

    from ub_cli.ratchet import uncommitted

    try:
        root = repository(Path(payload["cwd"]))
        return bool(git(root, "status", "--porcelain", "--", ".ub/") or uncommitted(root))
    except (KeyError, TypeError, OSError, ValueError):
        return False


def run_stop(timeout: int) -> int:
    payload = changed = None
    try:
        payload = json.load(sys.stdin)
        # Before the check runs, which could rewrite .ub/.
        changed = ub_changed(payload)
        decision = stop_decision(payload, timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        decision = {"decision": "block", "reason": f"UB checks could not run: {exc}"}
    if changed:
        # Warn the human only: the pre-push guard and CI enforce.
        decision["systemMessage"] = " ".join(filter(None, [decision.get("systemMessage"), UB_CHANGED]))
    print(json.dumps(decision))
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
