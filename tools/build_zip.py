#!/usr/bin/env python3
"""Build and validate the installable plugin ZIP.

Every check here mirrors FilaMan's own validation in
``backend/app/services/plugin_service.py`` (as of 2.0.5, the oldest release
this plugin supports), so mistakes surface at build time instead of during an
upload. It covers the rules this package can actually break; field lengths and
types of optional manifest fields are not mirrored. FilaMan's installer has the
last word, and the catalog CI runs it on every listed release.

Usage:
    python3 tools/build_zip.py             build dist/<key>-<version>.zip
    python3 tools/build_zip.py --check     validate only, write nothing
    python3 tools/build_zip.py --selftest  prove the validation actually bites
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "spoolmanscale"
DIST_DIR = REPO_ROOT / "dist"

# --------------------------------------------------------------------------
# Mirrored from FilaMan's plugin_service.py. Keep in sync when FilaMan changes.
# --------------------------------------------------------------------------

MAX_ZIP_SIZE = 10 * 1024 * 1024

# As of FilaMan 2.0.5, the oldest release this plugin supports. This plugin
# itself ships only .py, .json, .html and one checked .svg.
ALLOWED_EXTENSIONS = {
    ".py", ".json", ".md", ".txt", ".cfg", ".ini", ".yaml", ".yml", ".toml", ".html",
    ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".xml", ".map",
}

REQUIRED_MANIFEST_FIELDS = {"plugin_key", "name", "version", "description", "author"}
REQUIRED_DRIVER_FIELDS = {"driver_key"}
VALID_PLUGIN_TYPES = {"driver", "import", "integration"}

PLUGIN_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,49}$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")

MAX_FILE_SIZE = 1 * 1024 * 1024

# Never packed, regardless of extension.
EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}

# Files the operating system writes into the folder by itself. They are not
# part of the package by any definition, and FilaMan rejects every hidden file,
# so a Finder window on the package folder must not be able to break a build.
# Anything else hidden is a mistake and fails validation, see
# validate_hidden_files().
EXCLUDE_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}

# Icon limits, mirrored from FilaMan's app/services/plugin_icon_service.py.
# The icon the manifest names may have 4 KiB, any other SVG in the package 64.
MAX_ICON_BYTES = 4 * 1024
MAX_ICON_PATH_LENGTH = 250
MAX_LEGACY_SVG_BYTES = 64 * 1024
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"
SVG_ELEMENTS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "title", "desc"}
SVG_ATTRIBUTES = {
    "viewBox", "width", "height", "fill", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray",
    "stroke-dashoffset", "fill-rule", "clip-rule", "opacity", "fill-opacity",
    "stroke-opacity", "transform", "d", "x", "y", "x1", "y1", "x2", "y2",
    "cx", "cy", "r", "rx", "ry", "points", "preserveAspectRatio",
}


class ValidationError(Exception):
    """A packaging fault FilaMan would reject in exactly the same way."""


def _check_icon_path(plugin_dir: Path, icon: str) -> Path:
    """The icon path has to name an .svg inside the package, nothing else."""
    if (
        not isinstance(icon, str)
        or len(icon) > MAX_ICON_PATH_LENGTH
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*\.svg", icon)
        or any(part in ("", ".", "..") for part in icon.split("/"))
        or plugin_dir.is_symlink()
    ):
        raise ValueError("icon must be a relative .svg path within the plugin package")
    path = (plugin_dir / icon).resolve()
    if not path.is_relative_to(plugin_dir.resolve()):
        raise ValueError("icon must stay within the plugin package")
    return path


def _check_svg_source(data: bytes) -> None:
    """Only plain drawing elements and attributes, no references, no declarations."""
    source = data.decode("utf-8")
    if "<!" in source or re.search(r"<\?(?!xml\s)", source, re.IGNORECASE):
        raise ValueError("icon must not contain declarations, entities or comments")
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as error:
        raise ValueError("icon must be valid SVG XML") from error
    if root.tag != SVG_NAMESPACE + "svg":
        raise ValueError("icon must have an SVG root with the SVG namespace")
    allowed_tags = {SVG_NAMESPACE + tag for tag in SVG_ELEMENTS}
    for node in root.iter():
        if node.tag not in allowed_tags:
            raise ValueError("icon contains an unsupported SVG element")
        for attribute, value in node.attrib.items():
            if attribute not in SVG_ATTRIBUTES or re.search(r"url\s*\(|[\\\x00-\x1f]", value, re.IGNORECASE):
                raise ValueError("icon contains an unsupported SVG attribute or reference")


def validate_icon(plugin_dir: Path, icon: str, *, max_bytes: int = MAX_ICON_BYTES) -> None:
    """Mirror FilaMan's read_plugin_icon() without needing its app runtime.

    FilaMan runs this on the manifest icon at install time and refuses the
    whole package with invalid_icon when it fails.
    """
    try:
        path = _check_icon_path(plugin_dir, icon)
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"icon must be at most {max_bytes // 1024} KiB")
        _check_svg_source(data)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Invalid plugin icon {icon!r}: {exc}") from exc


def iter_plugin_files(plugin_dir: Path) -> tuple[list[Path], list[Path]]:
    """Every file that goes into the package, in a stable order.

    Returns the files to pack and the operating system files left out, so the
    build can name them rather than dropping them silently.
    """
    files = []
    dropped = []
    for path in sorted(plugin_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.relative_to(plugin_dir).parts):
            continue
        if path.name in EXCLUDE_FILES:
            dropped.append(path)
            continue
        files.append(path)
    return files, dropped


def validate_extensions(plugin_dir: Path, files: list[Path]) -> None:
    """The extension allow list, plus the SVG rules for every .svg in the package.

    FilaMan checks ``if suffix and suffix not in ALLOWED_EXTENSIONS``, so a file
    with no extension at all passes. That is mirrored deliberately. An .svg
    passes the list but has to pass the icon rules too, with the larger limit
    FilaMan grants an SVG the manifest does not name.
    """
    for path in files:
        suffix = path.suffix.lower()
        if suffix == ".svg":
            validate_icon(plugin_dir, str(path.relative_to(plugin_dir)), max_bytes=MAX_LEGACY_SVG_BYTES)
        if suffix and suffix not in ALLOWED_EXTENSIONS:
            rel = path.relative_to(plugin_dir)
            raise ValidationError(
                f"Forbidden file extension: {rel} ({suffix}). "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            )


def validate_hidden_files(plugin_dir: Path, files: list[Path]) -> None:
    """No hidden file survives FilaMan's check, whatever its extension.

    ``.DS_Store`` used to reach an upload this way and was refused there,
    which is exactly the kind of surprise this script exists to prevent. The
    operating system's own files are left out while packing, see
    EXCLUDE_FILES; anything else hidden is somebody's decision and fails here.
    """
    for path in files:
        if path.name.startswith("."):
            rel = path.relative_to(plugin_dir)
            raise ValidationError(
                f"Hidden file not allowed: {rel}. FilaMan refuses the upload "
                "with hidden_file."
            )


def validate_file_sizes(plugin_dir: Path, files: list[Path]) -> None:
    """FilaMan's per file limit, which is far below the limit for the ZIP."""
    for path in files:
        size = path.stat().st_size
        if size > MAX_FILE_SIZE:
            rel = path.relative_to(plugin_dir)
            raise ValidationError(
                f"File too large: {rel} ({size} bytes), the limit for a single "
                f"file is {MAX_FILE_SIZE}"
            )


def validate_python_syntax(plugin_dir: Path, files: list[Path]) -> None:
    """FilaMan compiles every .py at install time and refuses with syntax_error."""
    for path in files:
        if path.suffix.lower() != ".py":
            continue
        rel = path.relative_to(plugin_dir)
        try:
            compile(path.read_bytes(), str(rel), "exec")
        except (SyntaxError, ValueError) as exc:
            raise ValidationError(f"Invalid Python in {rel}: {exc}") from exc


def validate_structure(plugin_dir: Path, plugin_type: str) -> None:
    """The files required for this plugin type."""
    required = ["plugin.json", "__init__.py"]
    if plugin_type == "driver":
        required.append("driver.py")

    for name in required:
        if not (plugin_dir / name).is_file():
            raise ValidationError(f"Required file missing: {name}")


def validate_manifest(plugin_dir: Path) -> dict:
    """Read the manifest and check it against FilaMan's rules."""
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.is_file():
        raise ValidationError("plugin.json is missing")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"plugin.json is not valid JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ValidationError("plugin.json has to be a JSON object")

    # FilaMan counts an empty value as missing, like an absent key.
    missing = {field for field in REQUIRED_MANIFEST_FIELDS if not manifest.get(field)}
    if missing:
        raise ValidationError(f"Required manifest fields missing: {', '.join(sorted(missing))}")

    plugin_type = manifest.get("plugin_type", "driver")
    if plugin_type not in VALID_PLUGIN_TYPES:
        raise ValidationError(
            f"Invalid plugin_type '{plugin_type}' "
            f"(allowed: {', '.join(sorted(VALID_PLUGIN_TYPES))})"
        )
    manifest["plugin_type"] = plugin_type

    plugin_key = manifest["plugin_key"]
    if not PLUGIN_KEY_PATTERN.fullmatch(str(plugin_key)):
        raise ValidationError(
            f"plugin_key '{plugin_key}' does not match {PLUGIN_KEY_PATTERN.pattern}"
        )

    version = manifest["version"]
    if not SEMVER_PATTERN.fullmatch(str(version)):
        raise ValidationError(f"version '{version}' is not valid semver")

    if plugin_type == "driver":
        for field in REQUIRED_DRIVER_FIELDS:
            if not manifest.get(field):
                raise ValidationError(f"Required field '{field}' missing (needed for type 'driver')")

    if plugin_key != plugin_dir.name:
        raise ValidationError(
            f"plugin_key '{plugin_key}' and folder name '{plugin_dir.name}' "
            "have to agree, because FilaMan imports as app.plugins.<key>"
        )

    return manifest


def check_version_consistency(plugin_dir: Path, manifest: dict) -> None:
    """Hold __version__ in the package against the manifest version.

    Not a FilaMan rule, but exactly the kind of divergence you only notice after
    shipping.
    """
    init_text = (plugin_dir / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.MULTILINE)
    if not match:
        raise ValidationError("__version__ is missing from __init__.py")
    if match.group(1) != manifest["version"]:
        raise ValidationError(
            f"Versions disagree: __init__.py says {match.group(1)}, "
            f"plugin.json says {manifest['version']}"
        )


def validate(plugin_dir: Path) -> tuple[dict, list[Path], list[Path]]:
    """Run every check, returning the manifest, the file list and what was left out."""
    if not plugin_dir.is_dir():
        raise ValidationError(f"Plugin directory not found: {plugin_dir}")

    manifest = validate_manifest(plugin_dir)
    validate_structure(plugin_dir, manifest["plugin_type"])

    files, dropped = iter_plugin_files(plugin_dir)
    if not files:
        raise ValidationError("No files found to package")

    # FilaMan reads the icon from the installed package, so an icon that the
    # packing rules leave out would be named but missing.
    icon = manifest.get("icon")
    if icon is not None:
        validate_icon(plugin_dir, icon)
    if icon is not None and plugin_dir / icon not in files:
        raise ValidationError(f"Declared icon is excluded from the package: {icon}")

    validate_extensions(plugin_dir, files)
    validate_hidden_files(plugin_dir, files)
    validate_file_sizes(plugin_dir, files)
    validate_python_syntax(plugin_dir, files)
    check_version_consistency(plugin_dir, manifest)

    return manifest, files, dropped


def build(
    plugin_dir: Path,
    manifest: dict,
    files: list[Path],
    force: bool = False,
    dist_dir: Path | None = None,
) -> Path:
    """Write the ZIP. The plugin folder is kept as the single top level entry.

    Refuses to overwrite an existing build of the same version, because two
    different packages under one version number is a debugging trap: the
    installed files and the version an instance reports no longer agree, and the
    difference is invisible from the outside. Learned the hard way on 0.1.2.
    Bump the version, or pass --force when the previous build was never
    installed anywhere.
    """
    target = dist_dir or DIST_DIR
    target.mkdir(exist_ok=True, parents=True)
    out_path = target / f"{manifest['plugin_key']}-{manifest['version']}.zip"

    if out_path.exists() and not force:
        raise ValidationError(
            f"{out_path.name} already exists. Bump the version in plugin.json and "
            "__init__.py, or pass --force if that build never left this machine."
        )

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            arcname = Path(plugin_dir.name) / path.relative_to(plugin_dir)
            zf.write(path, arcname.as_posix())

    size = out_path.stat().st_size
    if size > MAX_ZIP_SIZE:
        out_path.unlink()
        raise ValidationError(
            f"ZIP is too large: {size} bytes, the limit is {MAX_ZIP_SIZE}"
        )

    return out_path


PLAIN_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h24v24z"/></svg>'


def _expect_rejected(staged: Path, label: str, failures: list[str]) -> None:
    """Record a failure when the staged package still validates."""
    try:
        validate(staged)
    except ValidationError:
        return
    failures.append(label)
    print(f"FAILED: {label} should have been rejected")


def selftest_icon_content(staged: Path) -> list[str]:
    """Active content and oversized SVGs are refused, a clean one at the limit is not."""
    failures: list[str] = []
    original_icon = (staged / "icon.svg").read_bytes()
    for label, content in (
        ("SVG style", b'<svg xmlns="http://www.w3.org/2000/svg"><style>path{fill:red}</style></svg>'),
        ("SVG script", b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'),
        ("SVG event handler", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'),
        ("SVG reference", b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="url(https://example.com/a)"/></svg>'),
        ("SVG declaration", b'<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>'),
        ("invalid SVG XML", b"<svg"),
        ("invalid SVG encoding", b"\xff"),
        ("declared icon over 4 KiB", PLAIN_SVG.ljust(MAX_ICON_BYTES + 1)),
    ):
        (staged / "icon.svg").write_bytes(content)
        _expect_rejected(staged, label, failures)
    (staged / "icon.svg").write_bytes(PLAIN_SVG.ljust(MAX_ICON_BYTES))
    validate(staged)
    (staged / "icon.svg").write_bytes(original_icon)

    # An SVG the manifest does not name gets FilaMan's larger limit.
    (staged / "extra.svg").write_bytes(PLAIN_SVG.ljust(MAX_LEGACY_SVG_BYTES))
    validate(staged)
    (staged / "extra.svg").write_bytes(PLAIN_SVG.ljust(MAX_LEGACY_SVG_BYTES + 1))
    _expect_rejected(staged, "undeclared SVG over 64 KiB", failures)
    (staged / "extra.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><style/></svg>')
    _expect_rejected(staged, "undeclared invalid SVG", failures)
    (staged / "extra.svg").unlink()
    return failures


def selftest_icon_paths(staged: Path, tmp: Path) -> list[str]:
    """The icon path cannot leave the package or point at something left out."""
    failures: list[str] = []
    manifest_path = staged / "plugin.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(original_manifest)

    def name_icon(icon: object) -> None:
        manifest_path.write_text(json.dumps({**manifest, "icon": icon}), encoding="utf-8")

    (tmp / "outside.svg").write_bytes(PLAIN_SVG)
    for icon in ("missing.svg", "../outside.svg", "/icon.svg", "assets/../icon.svg", "icon.png", "", 42):
        name_icon(icon)
        _expect_rejected(staged, f"icon path {icon!r}", failures)
    (staged / "linked.svg").symlink_to(tmp / "outside.svg")
    name_icon("linked.svg")
    _expect_rejected(staged, "icon symlink outside package", failures)
    (staged / "linked.svg").unlink()

    excluded_icon = staged / "assets/.git/icon.svg"
    excluded_icon.parent.mkdir(parents=True)
    excluded_icon.write_bytes(PLAIN_SVG)
    name_icon("assets/.git/icon.svg")
    _expect_rejected(staged, "icon excluded from ZIP", failures)
    shutil.rmtree(excluded_icon.parent)
    (staged / "assets/icon.svg").write_bytes(PLAIN_SVG)
    name_icon("assets/icon.svg")
    validate(staged)
    shutil.rmtree(staged / "assets")

    # A manifest without an icon stays valid, FilaMan then draws its own.
    for legacy in ({**manifest, "icon": None}, {k: v for k, v in manifest.items() if k != "icon"}):
        manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
        validate(staged)
    manifest_path.write_text(original_manifest, encoding="utf-8")
    return failures


def selftest_packing(staged: Path, tmp: Path) -> list[str]:
    """Operating system files are left out, other hidden files refused, builds not overwritten."""
    failures: list[str] = []
    # What a Finder window leaves behind must neither reach the ZIP nor break the build.
    (staged / ".DS_Store").write_bytes(b"\x00\x00\x01Bud1")
    manifest, files, dropped = validate(staged)
    if [path.name for path in dropped] != [".DS_Store"] or any(p.name == ".DS_Store" for p in files):
        failures.append(".DS_Store")
        print("FAILED: .DS_Store should have been left out of the package")

    # Any other hidden file is somebody's decision, and FilaMan refuses it.
    (staged / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _expect_rejected(staged, "a hidden .env", failures)
    (staged / ".env").unlink()

    # Two different packages under one version number make an installed plugin
    # and the version it reports disagree, with nothing visible from outside.
    manifest, files, _ = validate(staged)
    out_dir = tmp / "dist"
    out_path = build(staged, manifest, files, dist_dir=out_dir)
    try:
        build(staged, manifest, files, dist_dir=out_dir)
    except ValidationError:
        pass
    else:
        failures.append("same version twice")
        print("FAILED: a second build of the same version should have been refused")
    build(staged, manifest, files, force=True, dist_dir=out_dir)

    # FilaMan expects the plugin folder as the one top level entry, with exactly these files.
    with zipfile.ZipFile(out_path) as zf:
        packed = sorted(zf.namelist())
    expected = sorted(f"{staged.name}/{p.relative_to(staged).as_posix()}" for p in files)
    if packed != expected:
        failures.append("ZIP contents")
        print(f"FAILED: the ZIP holds {packed}, expected {expected}")
    return failures


def selftest_manifest(staged: Path) -> list[str]:
    """Manifest faults FilaMan refuses are refused here as well."""
    failures: list[str] = []
    manifest_path = staged / "plugin.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(original_manifest)
    for label, broken in (
        ("empty author", {**manifest, "author": ""}),
        ("plugin_key with capitals", {**manifest, "plugin_key": "SpoolmanScale"}),
        ("version with a trailing newline", {**manifest, "version": manifest["version"] + "\n"}),
        ("version not semver", {**manifest, "version": "0.2"}),
        ("manifest as a list", [manifest]),
    ):
        manifest_path.write_text(json.dumps(broken), encoding="utf-8")
        _expect_rejected(staged, label, failures)
    manifest_path.write_text(original_manifest, encoding="utf-8")

    init_path = staged / "__init__.py"
    original_init = init_path.read_text(encoding="utf-8")
    init_path.write_text(original_init + "\ndef broken(:\n", encoding="utf-8")
    _expect_rejected(staged, "Python syntax error", failures)
    init_path.write_text(original_init.replace(manifest["version"], "9.9.9"), encoding="utf-8")
    _expect_rejected(staged, "__version__ disagreeing with plugin.json", failures)
    init_path.write_text(original_init, encoding="utf-8")
    return failures


def selftest() -> int:
    """Prove the validation actually bites.

    Runs every check against a copy of the package, first clean, then with one
    fault at a time. A check that is never seen failing is a check nobody can
    trust.
    """
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / PLUGIN_DIR.name
        shutil.copytree(PLUGIN_DIR, staged)
        try:
            validate(staged)
        except ValidationError as exc:
            print(f"FAILED: a clean package was rejected: {exc}")
            return 1

        failures = (
            selftest_packing(staged, Path(tmp))
            + selftest_manifest(staged)
            + selftest_icon_content(staged)
            + selftest_icon_paths(staged, Path(tmp))
        )
        # An extension FilaMan never allows.
        (staged / "setup.exe").write_bytes(b"MZ")
        _expect_rejected(staged, "setup.exe", failures)
        (staged / "setup.exe").unlink()
        if failures:
            return 1

    print("Self test passed.")
    print("  clean package             accepted")
    print("  .DS_Store                 left out, build unaffected")
    print("  hidden .env               rejected")
    print("  same version twice        refused, --force accepted")
    print("  written ZIP               one top level folder, exactly the package files")
    print("  manifest and Python       empty field, bad key, bad version, syntax error rejected")
    print("  SVG content, size, paths  checked like FilaMan's installer")
    print("  setup.exe                 rejected as a forbidden extension")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    parser.add_argument("--selftest", action="store_true", help="test the validation itself")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing build of the same version",
    )
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    try:
        manifest, files, dropped = validate(PLUGIN_DIR)
    except ValidationError as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"{manifest['name']} {manifest['version']} ({manifest['plugin_key']}, {manifest['plugin_type']})")
    print(f"  {len(files)} files checked, every extension allowed")
    for path in dropped:
        print(f"  left out, the operating system wrote it: {path.relative_to(PLUGIN_DIR)}")

    if args.check:
        print("  validation only, nothing written")
        return 0

    try:
        out_path = build(PLUGIN_DIR, manifest, files, force=args.force)
    except ValidationError as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1

    size_kb = out_path.stat().st_size / 1024
    print(f"  written: {out_path.relative_to(REPO_ROOT)} ({size_kb:.1f} KB)")
    print()
    print("Upload it in FilaMan under Admin, Plugins.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
