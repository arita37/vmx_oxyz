"""Download one fixed public app without exposing Google credentials to logs/argv."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile

PACKAGE = "com.kruxlabs.upfashion"


def credentials():
    values = [os.environ.get(key, "") for key in ("GOOGLE_EMAIL", "GOOGLE_AAS_TOKEN")]
    if any(not value or any(c in value for c in "\r\n\x00") for value in values):
        raise ValueError("Configure valid GOOGLE_EMAIL and GOOGLE_AAS_TOKEN repository secrets.")
    return values


def prepare_profile(path):
    # Upstream's PC profile is SDK 34 but also advertises ARM translation. Do not
    # request ARM payloads: our API 34 x86_64 emulator must run native x86_64 code.
    text = Path(path).read_text()
    match = re.search(r"(?ms)^\[google_kiwi_x86_64\]\n(.*?)(?=^\[|\Z)", text)
    if not match:
        raise ValueError("Pinned x86_64 Play profile is missing.")
    section = match.group(1)
    if "Build.VERSION.SDK_INT=34" not in section:
        raise ValueError("Play profile no longer matches emulator API 34.")
    section, count = re.subn(r"(?m)^Platforms=.*$", "Platforms=x86_64", section)
    if count != 1:
        raise ValueError("Expected exactly one Platforms setting.")
    Path(path).write_text("[google_kiwi_x86_64]\n" + section)


def download(output, profile):
    email, token = credentials()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Download directory must be empty; refusing to mix APK sets.")
    # TemporaryDirectory and file creation use restrictive permissions. No shell
    # interpolation, command-line credentials, OAuth exchange, or ToS acceptance.
    with tempfile.TemporaryDirectory(prefix="upfashion-auth-") as directory:
        config = Path(directory) / "apkeep.ini"
        config.touch(mode=0o600)
        config.write_text(f"[google]\nemail = {email}\naas_token = {token}\n")
        child_env = {k: v for k, v in os.environ.items()
                     if k not in ("GOOGLE_EMAIL", "GOOGLE_AAS_TOKEN")}
        try:
            result = subprocess.run(
                ["apkeep", "-a", PACKAGE, "-d", "google-play", "-i", str(config),
                 "-o", f"device=google_kiwi_x86_64,device_properties_file={Path(profile).resolve()},split_apk=true",
                 str(output)],
                env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=600, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("apkeep could not complete. Check installation, authentication and Play availability.") from None
        # Never print raw apkeep output, including error messages or URLs.
        if result.returncode or not any(output.rglob("*.apk")):
            raise ValueError("No usable Play download. Check account access, AAS token, accepted Play terms, region and x86_64 availability; no fallback source is used.")
    print("Play download produced APK files; package and ABI validation follows.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-secrets", action="store_true")
    parser.add_argument("--prepare-profile")
    parser.add_argument("--download")
    parser.add_argument("--profile")
    args = parser.parse_args()
    try:
        if args.check_secrets:
            credentials()
            print("Required secret values are present (not displayed).")
        elif args.prepare_profile:
            prepare_profile(args.prepare_profile)
        elif args.download and args.profile:
            download(args.download, args.profile)
        else:
            parser.error("Choose --check-secrets, --prepare-profile or --download with --profile")
    except ValueError as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
