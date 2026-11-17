"""Validate a Play APK set, install native x86_64 splits, and check app launch."""
import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time
import zipfile

PACKAGE = "com.kruxlabs.upfashion"
ABI_NAMES = {"arm64_v8a": "arm64-v8a", "armeabi_v7a": "armeabi-v7a",
             "armeabi": "armeabi", "x86": "x86", "x86_64": "x86_64",
             "mips": "mips", "mips64": "mips64", "riscv64": "riscv64"}


@dataclass(frozen=True)
class Apk:
    path: Path
    package: str
    version: str
    split: str
    abis: frozenset


def inspect_apk(path, aapt):
    badging = subprocess.check_output([str(aapt), "dump", "badging", str(path)], text=True)
    header = next((line for line in badging.splitlines() if line.startswith("package:")), "")
    attrs = dict(re.findall(r"([\w]+)='([^']*)'", header))
    if not attrs.get("name") or not attrs.get("versionCode"):
        raise ValueError("APK package metadata could not be read.")
    abis = set()
    for line in badging.splitlines():
        if line.startswith("native-code:"):
            abis.update(re.findall(r"'([^']+)'", line))
    # Inspect contents too: metadata alone is not sufficient for every split.
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            parts = name.split("/")
            if len(parts) >= 3 and parts[0] == "lib" and parts[-1].endswith(".so"):
                abis.add(parts[1])
    return Apk(Path(path), attrs["name"], attrs["versionCode"], attrs.get("split", ""), frozenset(abis))


def split_abi(split):
    # ABI names can occur on feature configuration splits as well as base configs.
    for name, abi in ABI_NAMES.items():
        if split == "config." + name or split.endswith(".config." + name):
            return abi
    return None


def select_apks(apks):
    if not apks:
        raise ValueError("No APKs downloaded (apkeep may exit zero on download failure).")
    if any(apk.package != PACKAGE for apk in apks):
        raise ValueError("Unexpected package in download.")
    if len({apk.version for apk in apks}) != 1:
        raise ValueError("Mixed APK versions; refusing installation.")
    if sum(not apk.split for apk in apks) != 1:
        raise ValueError("Expected exactly one base APK.")
    if len({apk.split for apk in apks}) != len(apks):
        raise ValueError("Duplicate split IDs; refusing ambiguous APK set.")
    selected, excluded_groups, native_groups = [], set(), set()
    for apk in apks:
        abi = split_abi(apk.split)
        group = apk.split.rsplit("config.", 1)[0] if abi else None
        if abi and abi != "x86_64":
            excluded_groups.add(group)
            continue
        if apk.abis and "x86_64" not in apk.abis:
            raise ValueError("Base/feature APK has no native x86_64 code; an ARM device or publisher x86_64 build is required.")
        if abi == "x86_64":
            native_groups.add(group)
        selected.append(apk)
    if excluded_groups - native_groups:
        raise ValueError("An ABI split group lacks an x86_64 variant; ARM translation is not assumed.")
    return sorted(selected, key=lambda apk: (bool(apk.split), apk.split))


def run_smoke(directory):
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
    aapt = sdk / "build-tools" / "34.0.0" / "aapt"
    if not aapt.is_file():
        raise ValueError("Android build-tools 34.0.0/aapt is required.")
    # Explicit emulator serial avoids accidentally targeting another connected device.
    adb = ["adb", "-s", "emulator-" + os.environ.get("EMULATOR_PORT", "5554")]
    def command(*args):
        return subprocess.check_output(adb + list(args), text=True).strip()
    abis = command("shell", "getprop", "ro.product.cpu.abilist").split(",")
    if "x86_64" not in abis:
        raise ValueError("Expected an x86_64 emulator.")
    apks = [inspect_apk(path, aapt) for path in sorted(Path(directory).rglob("*.apk"))]
    selected = select_apks(apks)
    paths = [str(apk.path) for apk in selected]
    install = ["install", "-r"] if len(paths) == 1 else ["install-multiple", "-r"]
    subprocess.run(adb + install + paths, check=True, timeout=120)
    command("shell", "pm", "path", PACKAGE)
    # A single launcher event starts the app; no exploratory taps or account login.
    command("shell", "monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(10)
    if not command("shell", "pidof", PACKAGE):
        raise ValueError("App process did not remain alive after launch.")
    activities = command("shell", "dumpsys", "activity", "activities")
    if not any(PACKAGE + "/" in line and ("mResumedActivity" in line or "topResumedActivity" in line)
               for line in activities.splitlines()):
        raise ValueError("App is not the resumed activity after launch; check launch failures or system dialogs.")
    print(f"PASS: installed {len(paths)} compatible APK(s); UpFashion is alive and resumed after 10 seconds.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    args = parser.parse_args()
    try:
        run_smoke(args.directory)
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Android smoke test failed: {error}\n")


if __name__ == "__main__":
    main()
