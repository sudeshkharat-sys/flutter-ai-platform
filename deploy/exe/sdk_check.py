"""Checks that all bundled build tools exist before starting services."""

from __future__ import annotations

import subprocess
from pathlib import Path


class SDKCheckResult:
    def __init__(self):
        self.checks: list[tuple[str, bool, str]] = []  # (label, ok, detail)

    @property
    def all_ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    def add(self, label: str, ok: bool, detail: str = "") -> None:
        self.checks.append((label, ok, detail))

    def print_report(self) -> None:
        print("=" * 60)
        print("  Build Tools Verification Report")
        print("=" * 60)
        for label, ok, detail in self.checks:
            status = "[OK]  " if ok else "[MISS]"
            line = f"  {status} {label}"
            if detail:
                line += f" : {detail}"
            print(line)
        print()
        if self.all_ok:
            print("  RESULT: All build tools are present.")
        else:
            print("  RESULT: Some tools are missing (see above).")
            print("  APK builds will fail until missing tools are added.")
            print("  Refer to SETUP_GUIDE.md for download instructions.")
        print("=" * 60)


def verify(base_dir: Path) -> SDKCheckResult:
    result = SDKCheckResult()

    # Flutter
    flutter_bat = base_dir / "flutter" / "bin" / "flutter.bat"
    result.add("Flutter SDK", flutter_bat.exists(), str(flutter_bat) if not flutter_bat.exists() else "")

    # JDK
    java_exe = base_dir / "jdk" / "bin" / "java.exe"
    if java_exe.exists():
        try:
            ver = subprocess.check_output([str(java_exe), "-version"], stderr=subprocess.STDOUT, text=True)
            result.add("JDK", True, ver.splitlines()[0].strip())
        except Exception:
            result.add("JDK", True, "")
    else:
        result.add("JDK", False, str(java_exe))

    # Android SDK
    android_sdk = base_dir / "android-sdk"
    build_tools = list((android_sdk / "build-tools").glob("*")) if (android_sdk / "build-tools").exists() else []
    result.add(
        "Android SDK (build-tools)",
        len(build_tools) > 0,
        build_tools[0].name if build_tools else str(android_sdk / "build-tools"),
    )

    # Gradle ZIP
    gradle_zip = base_dir / "gradle" / "gradle-8.10.2-all.zip"
    result.add("Gradle 8.10.2 ZIP", gradle_zip.exists(), str(gradle_zip) if not gradle_zip.exists() else "")

    # PostgreSQL
    pg_ctl = base_dir / "postgres" / "bin" / "pg_ctl.exe"
    result.add("PostgreSQL portable", pg_ctl.exists(), str(pg_ctl) if not pg_ctl.exists() else "")

    # Redis
    redis_exe = base_dir / "redis" / "redis-server.exe"
    result.add("Redis portable", redis_exe.exists(), str(redis_exe) if not redis_exe.exists() else "")

    return result
