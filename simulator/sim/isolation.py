"""Policy isolation — SPEC §7.4 enforcement, lives OUTSIDE the editable files.

Review fix (honesty-and-leaks). Before this module the only gate was a
settable class attribute (``wants_instance``): any policy could ask for the
scenario instance (transition schedule, future samples), and the evaluator
``exec``'d policy files with no restriction, so a policy could also import
``sim.*`` or read traces off disk. Two layers now enforce §7.4:

1. **Load-time static gate** (``check_policy_source``), run by
   ``eval.evaluate.load_policy`` BEFORE the module is executed (so import-time
   side effects such as reading a CSV never run). It rejects:
     * any import outside ``policies.interface`` + a small pure-stdlib
       allowlist (no ``sim``, ``eval``, ``pandas``, ``numpy``, ``os``, ``sys``,
       ``importlib``, ``inspect``, ``pickle`` ...; numpy is excluded because
       ``np.load``/``np.loadtxt`` read files);
     * file / code / introspection primitives (``open``, ``exec``, ``eval``,
       ``compile``, ``__import__``, ``globals``, ``locals``, ``vars``,
       ``getattr``/``setattr``/``delattr``, ``breakpoint``, ``input``);
     * dunder access (``__class__``, ``__subclasses__``, ``__globals__``,
       ``__builtins__``, ``__dict__``, ``__module__`` ...) and frame/traceback
       walking attributes (``gi_frame``, ``f_back``, ``f_locals``,
       ``tb_frame`` ...), which are the standard routes from a callee back to
       the engine's local ``instance``;
     * any definition or mention of ``wants_instance`` / ``bind_instance``
       outside the frozen oracle file.

2. **Run-time privilege resolution** (``policy_privileges``), called by
   ``sim.engine.run`` for every run. The instance is bound only when the
   policy's class IS the frozen oracle class (object identity with the real
   ``policies.oracle.Oracle``, or a class that ``load_policy`` verified was
   loaded from the frozen ``policies/oracle.py`` path). Identity comes from
   the verified source file, never from an attribute the policy can set. A
   non-oracle policy that declares ``wants_instance`` or a ``bind_instance``
   method makes the run FAIL (``PolicyIsolationError``); ``bind_instance`` is
   never called. Likewise only the frozen baseline files may select a
   non-default gateway mechanism (``gateway_config``: MOBIKE migration /
   session roaming); an editable policy that tries is refused, so P1/P2 must
   win through the §7.3 Action channel alone, as SPEC §11 requires.

Scope, stated honestly: this is a strong guard against accidental and
loop-generated leaks and closes every path the review probe used, but an
in-process Python check is not a security sandbox against a determined
adversary. A hard guarantee would run the policy in a separate process that
receives only a serialised ``Obs`` (noted as future work in SPEC §12.5).
"""
from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass, field

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _p(rel: str) -> str:
    return os.path.realpath(os.path.join(_ROOT, rel))


# frozen files (SPEC §9, §11) — the only policies that may set gateway_config
FROZEN_BASELINE_FILES = frozenset(_p(f"policies/{n}.py") for n in (
    "static_documented", "mobike_roaming", "atsss_active_standby",
    "always_warm", "oracle"))
# the ONLY file whose policy may be bound to the scenario instance (§9.3)
PRIVILEGED_INSTANCE_FILES = frozenset({_p("policies/oracle.py")})
# frozen module names (for classes imported normally, e.g. in tests)
_FROZEN_MODULES = {
    "policies.static_documented": _p("policies/static_documented.py"),
    "policies.mobike_roaming": _p("policies/mobike_roaming.py"),
    "policies.atsss_active_standby": _p("policies/atsss_active_standby.py"),
    "policies.always_warm": _p("policies/always_warm.py"),
    "policies.oracle": _p("policies/oracle.py"),
}

DEFAULT_GATEWAY_CONFIG = {"sessions_roam": False,
                          "recovery_mode": "reactive_rehandshake"}

ALLOWED_IMPORTS = frozenset({
    "__future__", "policies.interface",
    # pure, IO-free standard library helpers
    "math", "collections", "dataclasses", "typing", "enum", "statistics",
    "functools", "itertools", "bisect", "heapq",
})
FORBIDDEN_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "breakpoint", "input",
    "memoryview", "importlib", "sys", "os", "builtins", "inspect", "gc",
})
FORBIDDEN_ATTRS = frozenset({
    "f_back", "f_locals", "f_globals", "f_builtins", "f_code", "f_trace",
    "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await",
    "ag_frame", "ag_code", "tb_frame", "tb_next", "with_traceback",
    "func_globals", "func_code",
})
ALLOWED_DUNDERS = frozenset({"__init__", "__post_init__", "__name__"})
PRIVILEGE_NAMES = frozenset({"wants_instance", "bind_instance"})


class PolicyIsolationError(RuntimeError):
    """A policy tried to reach something SPEC §7.4 forbids. The run fails."""


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def check_policy_source(path: str) -> None:
    """Static §7.4 gate. Raises PolicyIsolationError listing every violation.

    Called on the policy FILE before it is executed."""
    real = os.path.realpath(path)
    privileged = real in PRIVILEGED_INSTANCE_FILES
    with open(real, "r", encoding="utf-8") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src, filename=real)
    except SyntaxError as e:  # pragma: no cover
        raise PolicyIsolationError(f"{path}: does not parse: {e}") from e

    # The frozen, privileged oracle (SPEC §9.3/§13.4) is a trusted planner: it
    # legitimately imports numpy and the engine's handshake model to compute a
    # principled ceiling with full future knowledge. The isolation gate exists
    # to stop EDITABLE / loop-generated policies from leaking engine state or
    # the future, so it does not constrain the one file that is defined to have
    # the future. A byte-identical copy of the oracle at any OTHER path is NOT
    # privileged and is still checked in full (and rejected for these imports),
    # so this exemption cannot be abused to smuggle a leaky policy in.
    if privileged:
        return

    bad = []

    def flag(node, msg):
        bad.append(f"{os.path.basename(real)}:{getattr(node, 'lineno', '?')}: {msg}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_IMPORTS:
                    flag(node, f"import '{a.name}' not allowed (only "
                               "policies.interface + pure stdlib helpers)")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level and node.level > 0:
                flag(node, "relative import not allowed")
            elif mod not in ALLOWED_IMPORTS:
                flag(node, f"import from '{mod}' not allowed (only "
                           "policies.interface + pure stdlib helpers)")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                flag(node, f"use of '{node.id}' not allowed")
            elif _is_dunder(node.id) and node.id not in ALLOWED_DUNDERS:
                flag(node, f"dunder name '{node.id}' not allowed")
            elif node.id in PRIVILEGE_NAMES and not privileged:
                flag(node, f"'{node.id}' is reserved for the frozen oracle")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                flag(node, f"attribute '.{node.attr}' (frame/traceback "
                           "introspection) not allowed")
            elif _is_dunder(node.attr) and node.attr not in ALLOWED_DUNDERS:
                flag(node, f"dunder attribute '.{node.attr}' not allowed")
            elif node.attr in PRIVILEGE_NAMES and not privileged:
                flag(node, f"'.{node.attr}' is reserved for the frozen oracle")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in PRIVILEGE_NAMES and not privileged:
                flag(node, f"defining '{node.name}' is reserved for the "
                           "frozen oracle")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if (v in PRIVILEGE_NAMES and not privileged) or \
                    (_is_dunder(v) and v not in ALLOWED_DUNDERS):
                flag(node, f"string constant '{v}' not allowed")
    if bad:
        raise PolicyIsolationError(
            "policy rejected by the SPEC §7.4 isolation gate:\n  "
            + "\n  ".join(bad))


# classes verified by load_policy: class object -> real source path
_VERIFIED_CLASS_FILES = {}


def register_loaded_policy_class(cls, path: str) -> None:
    """Record the verified source file of a class load_policy just loaded."""
    _VERIFIED_CLASS_FILES[cls] = os.path.realpath(path)


def _class_file(cls):
    """Verified source file of a policy class, or None if unknown. Never
    trusts an attribute the policy can set: only the load_policy registry,
    or object identity with the class in the genuinely imported frozen
    module."""
    if cls in _VERIFIED_CLASS_FILES:
        return _VERIFIED_CLASS_FILES[cls]
    for modname, fpath in _FROZEN_MODULES.items():
        mod = sys.modules.get(modname)
        if mod is None:
            continue
        for v in vars(mod).values():
            if v is cls:
                return fpath
    return None


@dataclass
class Privileges:
    source_file: object
    may_bind_instance: bool
    gateway_config: dict = field(default_factory=dict)


def policy_privileges(policy) -> Privileges:
    """Resolve what this policy may receive. Raises PolicyIsolationError if a
    non-privileged policy asks for the instance or a non-frozen policy asks
    for a non-default gateway mechanism."""
    cls = type(policy)
    src = _class_file(cls)
    privileged = src is not None and src in PRIVILEGED_INSTANCE_FILES
    frozen = src is not None and src in FROZEN_BASELINE_FILES

    asks_instance = bool(getattr(policy, "wants_instance", False)) or \
        callable(getattr(policy, "bind_instance", None))
    if asks_instance and not privileged:
        raise PolicyIsolationError(
            f"policy {cls.__name__!r} (source {src or 'unverified'}) asked for "
            "the scenario instance; only the frozen oracle "
            "(policies/oracle.py) may receive it (SPEC §7.4, §9.3)")

    gconf = dict(getattr(policy, "gateway_config", {}) or {})
    if not frozen:
        diff = {k: v for k, v in gconf.items()
                if DEFAULT_GATEWAY_CONFIG.get(k, object()) != v}
        if diff:
            raise PolicyIsolationError(
                f"policy {cls.__name__!r} tried to select gateway mechanism "
                f"{diff}; only the frozen baselines may (SPEC §11). Editable "
                "policies run on the pessimistic default and act through the "
                "§7.3 Action channel only")
        gconf = dict(DEFAULT_GATEWAY_CONFIG)
    # bind only when the policy is privileged AND actually defines the hook, so
    # a helper class that merely shares the privileged source file (e.g. the
    # oracle's internal schedule-replay policy) is not handed the instance.
    may_bind = privileged and callable(getattr(policy, "bind_instance", None))
    return Privileges(source_file=src, may_bind_instance=may_bind,
                      gateway_config=gconf)
