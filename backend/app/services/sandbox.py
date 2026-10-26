"""Code evaluation for interview submissions.

Submissions are NEVER executed on the application host. With CODE_SANDBOX=docker each run uses a
throwaway container: no network, read-only root filesystem, dropped capabilities, unprivileged
user, memory/CPU/PID limits and a wall-clock timeout. With CODE_SANDBOX=disabled only static checks
run and the result is explicitly reported as "not_executed" (the score excludes test results).
"""

from __future__ import annotations

import ast
import json
import secrets
import shutil
import subprocess

from app.core.config import get_settings
from app.core.logging import get_logger, log_ctx
from app.schemas.agents import CodeExecutionResult

logger = get_logger(__name__)

FORBIDDEN_MODULES = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "ctypes",
    "importlib",
    "inspect",
    "pathlib",
    "multiprocessing",
    "threading",
    "signal",
    "resource",
    "builtins",
    "gc",
    "pickle",
    "marshal",
}
FORBIDDEN_CALLS = {
    "eval",
    "exec",
    "compile",
    "open",
    "__import__",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "breakpoint",
    "input",
}
MAX_CODE_CHARS = 20_000


def static_check(code: str, function: str) -> tuple[bool, list[str]]:
    """Return (ok_to_run, issues). Parse errors and forbidden constructs block execution."""
    issues: list[str] = []
    if len(code) > MAX_CODE_CHARS:
        return False, [f"Submission exceeds {MAX_CODE_CHARS} characters."]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, [f"Syntax error on line {exc.lineno}: {exc.msg}"]
    blocked = False
    defined = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            bad = [n for n in names if n.split(".")[0] in FORBIDDEN_MODULES]
            if bad:
                issues.append(f"Import of {', '.join(bad)} is not allowed in submissions.")
                blocked = True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            issues.append(f"Call to {node.func.id}() is not allowed in submissions.")
            blocked = True
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            issues.append(f"Access to dunder attribute {node.attr} is not allowed.")
            blocked = True
        elif isinstance(node, ast.FunctionDef) and node.name == function:
            defined = True
    if not defined:
        issues.append(f"Function `{function}` is not defined.")
        blocked = True
    return not blocked, issues


_HARNESS = r"""
import json as _json
def _run(_src, _tests, _fn, _cmp, _marker):
    _ns = {"__name__": "submission"}
    exec(compile(_src, "submission.py", "exec"), _ns)
    f = _ns[_fn]
    passed, failures = 0, []
    for i, t in enumerate(_tests):
        try:
            got = f(*_json.loads(_json.dumps(t["args"])))
            exp = t["expected"]
            ok = (sorted(got) == sorted(exp)) if _cmp == "sorted" else (got == exp)
        except Exception as e:
            ok, got = False, f"{type(e).__name__}: {e}"
        if ok:
            passed += 1
        else:
            failures.append({"test": i + 1, "got": repr(got)[:200]})
    print(_marker + _json.dumps({"passed": passed, "total": len(_tests), "failures": failures[:3]}))
_run(SRC, TESTS, FN, CMP, MARKER)
"""


def run_tests(code: str, spec: dict) -> CodeExecutionResult:
    settings = get_settings()
    fn = spec["function"]
    ok, issues = static_check(code, fn)
    total = len(spec.get("tests", []))
    if not ok:
        return CodeExecutionResult(
            status="error", passed=0, total=total, detail="Static checks failed.", static_issues=issues
        )
    if settings.code_sandbox != "docker":
        return CodeExecutionResult(
            status="not_executed",
            total=total,
            static_issues=issues,
            detail="Code execution is disabled (CODE_SANDBOX=disabled); only static checks ran. "
            "Tests were not executed, so correctness is excluded from the score.",
        )
    if shutil.which("docker") is None:
        return CodeExecutionResult(
            status="not_executed",
            total=total,
            static_issues=issues,
            detail="Docker is not available on the server; tests were not executed.",
        )
    marker = "@@RESULT-" + secrets.token_hex(8) + "@@"
    script = (
        f"SRC = {json.dumps(code)}\nTESTS = {json.dumps(spec['tests'])}\nFN = {json.dumps(fn)}\n"
        f"CMP = {json.dumps(spec.get('compare', 'exact'))}\nMARKER = {json.dumps(marker)}\n" + _HARNESS
    )
    cmd = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:size=16m",  # noqa: S108 - tmpfs inside the throwaway container
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "64",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65534:65534",
        settings.sandbox_image,
        "python",
        "-I",
        "-",
    ]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd,
            input=script,
            capture_output=True,
            text=True,  # noqa: S603 - fixed argv, no shell
            timeout=settings.sandbox_timeout_seconds + 10,
        )
    except subprocess.TimeoutExpired:
        return CodeExecutionResult(status="timeout", total=total, detail="Execution exceeded the time limit.")
    except OSError as exc:
        logger.warning("Sandbox launch failed", extra=log_ctx(error=str(exc)))
        return CodeExecutionResult(status="not_executed", total=total, detail="Sandbox could not be started.")
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(marker)), None)
    if line is None:
        err = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return CodeExecutionResult(status="error", total=total, detail=f"Runtime error: {err[0][:300]}")
    data = json.loads(line[len(marker) :])
    status = "passed" if data["passed"] == data["total"] else "failed"
    detail = "" if status == "passed" else f"Failing tests: {data['failures']}"
    return CodeExecutionResult(status=status, passed=data["passed"], total=data["total"], detail=detail[:500])
