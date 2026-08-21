#!/usr/bin/env python3
"""Create a self-contained Stopwatch Micro flashing bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any


VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESERVED_BUNDLE_NAMES = {
    name.casefold()
    for name in (
        "flash_args",
        "Stopwatch-Micro-merged.bin",
        "flash.ps1",
        "FLASHING.md",
        "manifest.json",
        "SHA256SUMS",
    )
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_project_version() -> str:
    cmake = (PROJECT_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    match = re.search(r'set\(PROJECT_VER\s+"([^"]+)"\)', cmake)
    if match is None:
        raise SystemExit("Unable to read PROJECT_VER from CMakeLists.txt")
    return match.group(1)


def verify_source_provenance(commit: str) -> None:
    try:
        head = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
            check=True,
            text=True,
            encoding="utf-8",
            capture_output=True,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-C",
                str(PROJECT_ROOT),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            check=True,
            text=True,
            encoding="utf-8",
            capture_output=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("Release packaging requires a readable Git checkout") from exc
    if head != commit:
        raise SystemExit(f"--commit {commit} does not match HEAD {head}")
    if status:
        raise SystemExit(f"Refusing to package a dirty worktree:\n{status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", help="semantic firmware version; defaults to PROJECT_VER"
    )
    parser.add_argument("--commit", required=True, help="Git commit used for the build")
    parser.add_argument(
        "--codex-version",
        default="unverified",
        help="Windows Codex version used for the compatibility check",
    )
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    return parser.parse_args()


def load_flash_plan(
    build_dir: Path,
) -> tuple[dict[str, Any], list[tuple[str, str, Path]]]:
    build_root = build_dir.resolve()
    plan_path = build_root / "flasher_args.json"
    if not plan_path.is_file():
        raise SystemExit(f"Missing build flash plan: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise SystemExit(f"Invalid object in {plan_path}")
    flash_files = plan.get("flash_files")
    if not isinstance(flash_files, dict) or not flash_files:
        raise SystemExit(f"Invalid flash_files in {plan_path}")

    parsed: list[tuple[int, str, Path]] = []
    used_names: set[str] = set()
    for raw_offset, relative in flash_files.items():
        if not isinstance(raw_offset, str) or not raw_offset:
            raise SystemExit(f"Invalid flash offset in {plan_path}: {raw_offset!r}")
        try:
            offset = int(raw_offset, 0)
        except ValueError as exc:
            raise SystemExit(f"Invalid flash offset in {plan_path}: {raw_offset!r}") from exc
        if offset < 0 or not isinstance(relative, str) or not relative:
            raise SystemExit(f"Invalid flash entry in {plan_path}: {raw_offset!r}={relative!r}")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise SystemExit(f"Flash artifact must stay inside the build directory: {relative}")
        source = (build_root / relative_path).resolve()
        if not source.is_relative_to(build_root):
            raise SystemExit(f"Flash artifact escapes the build directory: {relative}")
        if not source.is_file():
            raise SystemExit(f"Missing build artifact: {source}")
        name = source.name
        normalized_name = name.casefold()
        if normalized_name in used_names or normalized_name in RESERVED_BUNDLE_NAMES:
            raise SystemExit(f"Duplicate or reserved flash artifact basename: {name}")
        used_names.add(normalized_name)
        parsed.append((offset, name, source))

    entries: list[tuple[str, str, Path]] = []
    previous_end = 0
    flash_limit = 0x1000000
    for offset, name, source in sorted(parsed, key=lambda item: item[0]):
        size = source.stat().st_size
        end = offset + size
        if size <= 0 or end > flash_limit or offset < previous_end:
            raise SystemExit(
                f"Invalid or overlapping flash range: 0x{offset:X}-0x{end:X} ({name})"
            )
        previous_end = end
        entries.append((f"0x{offset:X}", name, source))
    return plan, entries


def powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def write_windows_flasher(
    path: Path,
    chip: str,
    before: str,
    after: str,
    use_stub: bool,
    write_args: list[str],
    entries: list[tuple[str, str, Path]],
) -> None:
    esptool_args = [
        "--chip",
        chip,
        "--port",
        "$Port",
        "--baud",
        "460800",
        "--before",
        before,
        "--after",
        after,
    ]
    if not use_stub:
        esptool_args.append("--no-stub")
    esptool_args.extend(("write_flash", *write_args))
    for offset, name, _ in entries:
        esptool_args.extend((offset, name))

    argument_lines = []
    for value in esptool_args:
        argument_lines.append(
            "    $Port" if value == "$Port" else f"    {powershell_literal(value)}"
        )
    arguments = ",\n".join(argument_lines)
    script = f"""[CmdletBinding()]
param([string]$Port)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$checksumFile = Join-Path $PSScriptRoot 'SHA256SUMS'
foreach ($line in Get-Content -LiteralPath $checksumFile) {{
    $parts = $line -split '\\s+', 2
    if ($parts.Count -ne 2) {{
        throw "Invalid checksum line: $line"
    }}
    $expected = $parts[0].ToLowerInvariant()
    $file = Join-Path $PSScriptRoot $parts[1]
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {{
        throw "SHA-256 mismatch: $($parts[1])"
    }}
}}
if (-not $Port -or $Port -notmatch '^COM\\d+$') {{
    throw 'Pass the verified StopWatch port explicitly with -Port COMx.'
}}

$esptoolArguments = @(
{arguments}
)
& python -m esptool @esptoolArguments
if ($LASTEXITCODE -ne 0) {{
    throw "esptool failed with exit code $LASTEXITCODE"
}}
"""
    path.write_text(script, encoding="utf-8")


def main() -> None:
    args = parse_args()
    version = args.version or read_project_version()
    if VERSION_PATTERN.fullmatch(version) is None:
        raise SystemExit(f"Invalid firmware version: {version}")
    if re.fullmatch(r"[0-9a-f]{40}", args.commit) is None:
        raise SystemExit("--commit must be a full 40-character Git SHA")
    verify_source_provenance(args.commit)

    description_path = args.build_dir.resolve() / "project_description.json"
    if not description_path.is_file():
        raise SystemExit(f"Missing build description: {description_path}")
    description = json.loads(description_path.read_text(encoding="utf-8"))
    if (
        not isinstance(description, dict)
        or description.get("project_name") != "Stopwatch-Micro"
        or description.get("project_version") != version
        or description.get("target") != "esp32s3"
    ):
        raise SystemExit(f"Build description does not match this release: {description_path}")

    flash_plan, flash_files = load_flash_plan(args.build_dir)
    app_bin = description.get("app_bin")
    if not isinstance(app_bin, str) or not app_bin:
        raise SystemExit("Build description is missing app_bin")
    raw_flash_files = flash_plan.get("flash_files", {})
    actual_flash_files = {
        int(offset, 0): Path(relative).as_posix()
        for offset, relative in raw_flash_files.items()
    }
    expected_flash_files = {
        0x0: "bootloader/bootloader.bin",
        0x8000: "partition_table/partition-table.bin",
        0xD000: "ota_data_initial.bin",
        0x20000: Path(app_bin).as_posix(),
    }
    if actual_flash_files != expected_flash_files:
        raise SystemExit(f"Flash images do not match the ESP-IDF build description: {actual_flash_files}")
    raw_write_args = flash_plan.get("write_flash_args")
    extra_args = flash_plan.get("extra_esptool_args")
    flash_settings = flash_plan.get("flash_settings")
    if not isinstance(raw_write_args, list) or not all(
        isinstance(value, str) and value for value in raw_write_args
    ):
        raise SystemExit("Invalid write_flash_args in flasher_args.json")
    if not isinstance(extra_args, dict) or not isinstance(flash_settings, dict):
        raise SystemExit("Invalid flash metadata in flasher_args.json")
    if len(raw_write_args) % 2 != 0:
        raise SystemExit("write_flash_args must contain option/value pairs")
    write_options: dict[str, str] = {}
    for index in range(0, len(raw_write_args), 2):
        option, value = raw_write_args[index : index + 2]
        if option in write_options:
            raise SystemExit(f"Duplicate write flash option: {option}")
        write_options[option] = value
    expected_write_options = {
        "--flash_mode": "dio",
        "--flash_size": "16MB",
        "--flash_freq": "80m",
    }
    if write_options != expected_write_options:
        raise SystemExit(f"Unexpected write flash options: {write_options}")
    write_args = list(raw_write_args)
    chip = extra_args.get("chip")
    before = extra_args.get("before")
    after = extra_args.get("after")
    use_stub = extra_args.get("stub")
    if (
        chip != "esp32s3"
        or before not in {"default_reset", "no_reset", "usb_reset"}
        or after not in {"hard_reset", "soft_reset", "no_reset", "no_reset_stub"}
    ):
        raise SystemExit("Unsupported chip or reset policy in flasher_args.json")
    if not isinstance(use_stub, bool):
        raise SystemExit("Invalid stub policy in flasher_args.json")
    expected_flash_settings = {"flash_mode": "dio", "flash_size": "16MB", "flash_freq": "80m"}
    if flash_settings != expected_flash_settings:
        raise SystemExit(f"Unexpected flash settings: {flash_settings}")
    expected_offsets = {0x0, 0x8000, 0xD000, 0x20000}
    actual_offsets = {int(offset, 0) for offset, _, _ in flash_files}
    if actual_offsets != expected_offsets:
        raise SystemExit(f"Incomplete or unexpected flash image set: {sorted(actual_offsets)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"Stopwatch-Micro-v{version}"
    bundle_dir = args.output_dir / bundle_name
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir()

    for _, name, source in flash_files:
        shutil.copy2(source, bundle_dir / name)

    flash_args = [" ".join(write_args)]
    flash_args.extend(f"{offset} {name}" for offset, name, _ in flash_files)
    (bundle_dir / "flash_args").write_text(
        "\n".join(flash_args) + "\n", encoding="utf-8"
    )

    merged_image = bundle_dir / "Stopwatch-Micro-merged.bin"
    merge_command = [
        sys.executable,
        "-m",
        "esptool",
        "--chip",
        chip,
        "merge_bin",
        "-o",
        str(merged_image),
        *write_args,
    ]
    for offset, _, source in flash_files:
        merge_command.extend((offset, str(source)))
    subprocess.run(merge_command, check=True)

    write_windows_flasher(
        bundle_dir / "flash.ps1",
        chip,
        before,
        after,
        use_stub,
        write_args,
        flash_files,
    )

    flashing = f"""# Flash Stopwatch Micro v{version}

This bundle targets the M5Stack StopWatch ESP32-S3 with 16 MB flash.
The standalone flasher verifies bundle checksums but does not create a factory backup. Use the
project `tools/stopwatch.ps1 backup` workflow before first flashing a device.

On Windows, install `esptool`, connect the watch, and run:

```powershell
.\\flash.ps1 -Port COM5
```

The merged image can also be written directly:

```powershell
python -m esptool --chip {chip} --port COM5 --baud 460800 write_flash 0x0 Stopwatch-Micro-merged.bin
```

Verify the files against `SHA256SUMS` before flashing.
"""
    (bundle_dir / "FLASHING.md").write_text(flashing, encoding="utf-8")

    manifest = {
        "project": "Stopwatch Micro",
        "version": version,
        "commit": args.commit,
        "target": chip,
        "flash_size": flash_settings.get("flash_size", "16MB"),
        "codex_windows_version": args.codex_version,
        "files": [
            {"offset": offset, "name": name} for offset, name, _ in flash_files
        ],
        "merged_image": {"offset": "0x0", "name": merged_image.name},
    }
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    checksum_files = sorted(
        path for path in bundle_dir.iterdir() if path.name != "SHA256SUMS"
    )
    checksums = "".join(f"{sha256(path)}  {path.name}\n" for path in checksum_files)
    (bundle_dir / "SHA256SUMS").write_text(checksums, encoding="utf-8")

    archive = args.output_dir / f"{bundle_name}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as package:
        for path in sorted(bundle_dir.iterdir()):
            package.write(path, Path(bundle_name) / path.name)

    archive_checksum = args.output_dir / f"{archive.name}.sha256"
    archive_checksum.write_text(
        f"{sha256(archive)}  {archive.name}\n", encoding="utf-8"
    )
    print(f"Created {archive} ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
