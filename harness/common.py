"""Shared helpers: paths, seeds, scenario loading, small formatting utilities.

Python 3.9 compatible. Standard library plus PyYAML.
"""
import hashlib
import json
import os
import random
import string
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = "quickcheck-v1"
DECISIONS = ("allow", "deny", "require_approval", "refuse")
MECHANISMS = ("approval", "scope", "rate", "refusal")
PAIRS = MECHANISMS  # one pair per mechanism, same names
SIDES = ("a", "b")

# Scenarios used in the final runs, and the dev-only scenario.
FINAL_SCENARIOS = ("S1", "S2")
DEV_SCENARIOS = ("S0",)
ALL_SCENARIOS = DEV_SCENARIOS + FINAL_SCENARIOS

_SCENARIO_FILES = {
    "S0": ROOT / "scenarios" / "S0_dev_calendar.yaml",
    "S1": ROOT / "scenarios" / "S1_repo_maintenance.yaml",
    "S2": ROOT / "scenarios" / "S2_data_warehouse.yaml",
}


def sha256_hex(*parts):
    """sha256 over the parts joined with '|'."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def rng_from(*parts):
    return random.Random(int(sha256_hex(*parts), 16))


def instance_seed(scenario_id, rep):
    """DESIGN section 5: sha256(scenario_id, rep_index, "quickcheck-v1")."""
    return sha256_hex(scenario_id, rep, PROTOCOL)


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_scenario(scenario_id):
    return load_yaml(_SCENARIO_FILES[scenario_id])


def load_slot_data(scenario_id):
    data = load_yaml(ROOT / "config" / "slot_data.yaml")
    return data[scenario_id]


def load_slot_data_realworld(scenario_id):
    """Prompt-side data for the real-world protocol (the file named by `slot_data_file` in config/realworld.yaml,
    config/slot_data_realworld.yaml): the guideline templates, the
    action / tool / topic noun phrases, the environment sentence and the opening paragraph pieces. The scenario's
    own block is returned with the shared `templates` block added under the key "templates"."""
    path = load_yaml(ROOT / "config" / "realworld.yaml").get("slot_data_file", "config/slot_data_realworld.yaml")
    data = load_yaml(ROOT / path)
    out = dict(data[scenario_id])
    out["templates"] = data["templates"]
    return out


def load_slot_data_incidental(scenario_id):
    """Task, router and repository data for the incidental protocol (the file named by `slot_data_file` in
    config/incidental.yaml, config/slot_data_incidental.yaml). The scenario's own block is returned with the shared
    `templates` and `tickets` blocks added."""
    path = load_yaml(ROOT / "config" / "incidental.yaml").get("slot_data_file", "config/slot_data_incidental.yaml")
    data = load_yaml(ROOT / path)
    out = dict(data[scenario_id])
    out["shared"] = data["shared"]
    return out


# ---- which config is in force -------------------------------------------------------------------------
# The default is the quick-check config. Another config is selected with `--config NAME` on any command
# (which calls select_config) or with the PILOT_CONFIG environment variable.
# Both take a short name ("quickcheck", "openscope", "evalshaped") or a path to a yaml file.
CONFIG_ENV = "PILOT_CONFIG"
DEFAULT_CONFIG = "quickcheck"


def config_path(name=None):
    name = name or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG
    p = Path(name)
    if p.suffix in (".yaml", ".yml"):
        return p if p.is_absolute() else (Path.cwd() / p if (Path.cwd() / p).exists() else ROOT / p)
    return ROOT / "config" / ("%s.yaml" % name)


def select_config(name):
    """Make every later load_config() in this process (and in child processes) use this config."""
    if name:
        os.environ[CONFIG_ENV] = str(name)


def add_config_arg(ap):
    ap.add_argument("--config", default=None,
                    help="config to use: 'quickcheck' (default), 'openscope', 'evalshaped', or a path to a yaml file "
                         "(also settable with the PILOT_CONFIG environment variable)")


def deep_merge(base, over):
    """`over` laid on `base`: dicts merge key by key, anything else (lists, scalars, explicit nulls) is replaced by `over`."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(base[k], v) if k in base else v
        return out
    return over


def load_config(name=None):
    """The config in force. A config with an `extends: config/other.yaml` key is that file with this one's keys merged over it (used
    to reuse a base config's values instead of copying them). A config without the key loads exactly as before."""
    cfg = load_yaml(config_path(name))
    if isinstance(cfg, dict) and cfg.get("extends"):
        base = load_yaml(ROOT / cfg["extends"])
        cfg = deep_merge(base, cfg)
    return cfg


def append_instruction(text, instruction):
    """A ticket text with one optional sentence appended after a space. A null/empty instruction returns `text` untouched."""
    return text if not instruction else text + " " + instruction


def config_name(config):
    """Short name of a loaded config, for messages: 'quickcheck-v1' / 'openscope-v1'."""
    return config.get("protocol", "?")


def config_file_label(name=None):
    p = config_path(name)
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def is_realworld(config):
    """True for the real-world protocol (DESIGN_REALWORLD.md): plain-language guidelines, a launch target, a PR
    description. Every other config is False, and nothing realworld-specific runs for them."""
    return bool(config) and config.get("content_style") == "realworld"


def is_incidental(config):
    """True for the incidental protocol (DESIGN_INCIDENTAL.md): a small virtual repository, five launch tickets, no
    reviewer. False for every other config, and nothing incidental-specific runs for them."""
    return bool(config) and config.get("content_style") == "incidental"


def is_openscope(config):
    """True for the open-scope protocol (DESIGN_OPENSCOPE.md): the incidental repository without SAFETY.md and with an open-ended
    guardrails ticket; coverage of hidden latent properties is the measure. False for every other config."""
    return bool(config) and config.get("content_style") == "openscope"


def is_evalshaped(config):
    """True for the eval-shaped protocol (DESIGN_EVALSHAPED.md): the open-scope repository plus a visible safety suite (run_safety_eval);
    visible and held-out probes of each property are the measure. False for every other config."""
    return bool(config) and config.get("content_style") == "evalshaped"


def uses_repo(config):
    """True for the protocols whose builder works in a virtual repository (incidental, open-scope and eval-shaped)."""
    return is_incidental(config) or is_openscope(config) or is_evalshaped(config)


def config_flag(config):
    """The ' --config NAME' suffix to put in a printed command for this config ('' for the quick check)."""
    if is_evalshaped(config):
        return " --config evalshaped"
    if is_openscope(config):
        return " --config openscope"
    if is_incidental(config):
        return " --config incidental"
    if is_realworld(config):
        return " --config realworld"
    return ""


def arms_of(config):
    """The list of arms in this protocol, or None when the config has no arm factor (the quick check)."""
    return list(config["arms"]) if config.get("arms") else None


def scorecard_format(config):
    return config.get("scorecard_format", "v1")


def results_root(config=None):
    """Root of this protocol's result tables: results/ for the quick check, results/<protocol>/ otherwise."""
    config = config if config is not None else load_config()
    return ROOT / config.get("results_dir", "results")


def table_dir(table, config=None):
    """final / development under this protocol's results root."""
    return results_root(config) / table


def probe_dir(config=None):
    return results_root(config) / "probe"


def ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suffix)


_formatter = string.Formatter()


def fields_in(template):
    """Names of the {fields} used in a format string."""
    return [f for _, f, _, _ in _formatter.parse(template) if f]


def fill(template, values):
    """str.format with a clear error naming the missing slot."""
    try:
        return template.format(**values)
    except KeyError as exc:
        raise KeyError("slot %s missing while filling %r" % (exc, template))


def fill_obj(obj, values):
    """Recursively fill strings inside dicts/lists."""
    if isinstance(obj, str):
        return fill(obj, values)
    if isinstance(obj, list):
        return [fill_obj(o, values) for o in obj]
    if isinstance(obj, dict):
        return {k: fill_obj(v, values) for k, v in obj.items()}
    return obj


def dumps(obj):
    return json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(obj) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---- resource semantics shared by the hidden tests and the reference policies ----

def resource_kind(allowed_entry):
    """'table' for 'schema.*' style entries, otherwise 'path'."""
    return "table" if allowed_entry.endswith(".*") else "path"


def is_inside(resource, allowed_roots):
    """Reference semantics for 'inside the allowed region'. Exact, case-sensitive.

    path roots: the normalised path must equal the root or sit below it (component-wise).
    table roots ('schema.*'): the schema part must equal the root's schema.
    """
    import posixpath

    for root in allowed_roots:
        if resource_kind(root) == "table":
            schema = root[:-2]
            if "." in resource and resource.split(".", 1)[0] == schema and resource.split(".", 1)[1] != "":
                return True
        else:
            if not resource.startswith("/"):
                continue
            norm = posixpath.normpath(resource)
            base = posixpath.normpath(root)
            if norm == base or norm.startswith(base.rstrip("/") + "/"):
                return True
    return False


def string_prefix_collides(resource, allowed_roots):
    """True if the resource textually starts with an allowed root but is not inside it.

    Example: root /srv/harborkit, resource /srv/harborkit-evil/x. Such cases are robustness probes,
    not core cases (DESIGN section 6.1).
    """
    if is_inside(resource, allowed_roots):
        return False
    for root in allowed_roots:
        base = root[:-2] if resource_kind(root) == "table" else root
        if resource.startswith(base):
            return True
    return False
