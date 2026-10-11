import json
import re

_RANGE_PREFIX = re.compile(r"^[\^~>=<v\s]+")


def _norm(version: str) -> str:
    return _RANGE_PREFIX.sub("", version.strip()) or version


def parse_manifest(path: str, text: str) -> dict[str, str]:
    """name -> version from package.json (declared) or package-lock.json (resolved)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    if path.endswith("package-lock.json"):
        for key, meta in (data.get("packages") or {}).items():
            if key.startswith("node_modules/") and isinstance(meta, dict) and meta.get("version"):
                out[key.rsplit("node_modules/", 1)[-1]] = str(meta["version"])
        return out
    for section in ("dependencies", "devDependencies"):
        for name, ver in (data.get(section) or {}).items():
            out[name] = _norm(str(ver))
    return out


def diff_manifests(
    pre: dict[str, str], post: dict[str, str]
) -> list[tuple[str, str | None, str | None]]:
    """(name, old_version, new_version) for every added, removed or bumped dependency."""
    return [
        (name, pre.get(name), post.get(name))
        for name in sorted(pre.keys() | post.keys())
        if pre.get(name) != post.get(name)
    ]


def package_identity(text: str) -> tuple[str, str] | None:
    """(name, version) of the package a package.json describes, if both are present."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("name") and data.get("version"):
        return str(data["name"]), str(data["version"])
    return None
