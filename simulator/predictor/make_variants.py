"""Generate the ablation variants of policies/policy.py (SPEC §14.3).

Each variant is a full standalone copy (the isolation gate forbids importing
policy.py), differing only in the flags line and, for the modem variant, the
compiled model trained WITH the router-exported modem features.
"""
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "policies", "policy.py")
FLAGS = "USE_MODEM, USE_P1, USE_P2 = False, False, True"

VARIANTS = {
    "variant_modem.py": ("USE_MODEM, USE_P1, USE_P2 = True, False, True", "ours_modem", "model_modem.pyfrag"),
    "variant_with_p1.py": ("USE_MODEM, USE_P1, USE_P2 = False, True, True", "ours_with_p1", None),
}


def main():
    src = open(SRC).read()
    assert FLAGS in src, "flags line changed; update make_variants.py"
    for fname, (flags, name, frag) in VARIANTS.items():
        out = src.replace(FLAGS, flags).replace('name = "ours_p1p2"', f'name = "{name}"')
        out = out.replace('"""policy.py — EDITABLE', f'"""{fname} — GENERATED ablation variant of policy.py; do not edit.\n\npolicy.py — EDITABLE', 1)
        if frag:
            code = open(os.path.join(HERE, "predictor", frag)).read()
            out = re.sub(r"(# >>> MODEL.*?\n).*?(# <<< MODEL)", lambda m: m.group(1) + code + "\n" + m.group(2),
                         out, flags=re.S)
        open(os.path.join(HERE, "policies", fname), "w").write(out)
        print("wrote", fname)


if __name__ == "__main__":
    main()
