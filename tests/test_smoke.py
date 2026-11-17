import contextlib
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from android_smoke import Apk, PACKAGE, inspect_apk, select_apks, run_smoke
from play_download import credentials, download, prepare_profile


def apk(split="", abis=(), package=PACKAGE, version="42"):
    return Apk(Path((split or "base") + ".apk"), package, version, split, frozenset(abis))


class SelectionTests(unittest.TestCase):
    def test_standalone(self):
        self.assertEqual(select_apks([apk()]), [apk()])

    def test_universal_base(self):
        self.assertEqual(select_apks([apk(abis=["arm64-v8a", "x86_64"])]),
                         [apk(abis=["arm64-v8a", "x86_64"])])

    def test_compatible_splits_only(self):
        files = [apk("config.arm64_v8a", ["arm64-v8a"]), apk("config.en"),
                 apk(), apk("config.x86_64", ["x86_64"]), apk("config.xxhdpi")]
        self.assertEqual([x.split for x in select_apks(files)],
                         ["", "config.en", "config.x86_64", "config.xxhdpi"])

    def test_feature_abi_groups(self):
        files = [apk(), apk("camera"), apk("camera.config.arm64_v8a", ["arm64-v8a"]),
                 apk("camera.config.x86_64", ["x86_64"])]
        self.assertEqual(len(select_apks(files)), 3)

    def test_reject_unsafe_sets(self):
        cases = [[], [apk("config.en")], [apk(), apk()],
                 [apk(), apk("config.en", version="43")],
                 [apk(package="another.app")], [apk(abis=["arm64-v8a"])],
                 [apk(), apk("feature", ["armeabi-v7a"])],
                 [apk(), apk("config.arm64_v8a", ["arm64-v8a"])],
                 [apk(), apk("config.x86_64", ["arm64-v8a"])],
                 [apk(), apk("config.x86_64", ["x86_64"]),
                  apk("camera.config.arm64_v8a", ["arm64-v8a"])]]
        for files in cases:
            with self.subTest(files=files), self.assertRaises(ValueError):
                select_apks(files)

    def test_inspects_contents_and_aapt_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.apk"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("lib/arm64-v8a/libapp.so", b"placeholder")
            metadata = f"package: name='{PACKAGE}' versionCode='42' split='config.arm64_v8a'\n"
            with patch("android_smoke.subprocess.check_output", return_value=metadata) as run:
                result = inspect_apk(path, "/sdk/aapt")
            self.assertEqual(result.abis, frozenset(["arm64-v8a"]))
            self.assertEqual(result.split, "config.arm64_v8a")
            run.assert_called_once_with(["/sdk/aapt", "dump", "badging", str(path)], text=True)

    def test_malformed_metadata(self):
        with patch("android_smoke.subprocess.check_output", return_value="package: broken"):
            with self.assertRaises(ValueError):
                inspect_apk(Path("bad.apk"), "aapt")

    def test_install_and_launch_commands(self):
        for files, install_command in [([apk()], "install"),
                                       ([apk(), apk("config.x86_64", ["x86_64"])], "install-multiple")]:
            with self.subTest(install=install_command), tempfile.TemporaryDirectory() as directory:
                sdk = Path(directory)
                aapt = sdk / "build-tools/34.0.0/aapt"
                aapt.parent.mkdir(parents=True)
                aapt.touch()
                (sdk / "base.apk").touch()
                def output(args, **kwargs):
                    self.assertEqual(args[:3], ["adb", "-s", "emulator-5554"])
                    if "getprop" in args:
                        return "x86_64,x86\n"
                    if "pidof" in args:
                        return "1234\n"
                    if "dumpsys" in args:
                        return f"mResumedActivity: ActivityRecord{{ {PACKAGE}/.MainActivity }}"
                    return "ok"
                with patch.dict(os.environ, {"ANDROID_HOME": directory, "EMULATOR_PORT": "5554"}), \
                     patch("android_smoke.inspect_apk", return_value=files[0]), \
                     patch("android_smoke.select_apks", return_value=files), \
                     patch("android_smoke.subprocess.check_output", side_effect=output), \
                     patch("android_smoke.subprocess.run") as install, \
                     patch("android_smoke.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
                    run_smoke(directory)
                self.assertEqual(install.call_args.args[0][3], install_command)

    def test_launch_failure_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk = Path(directory)
            aapt = sdk / "build-tools/34.0.0/aapt"
            aapt.parent.mkdir(parents=True)
            aapt.touch()
            (sdk / "base.apk").touch()
            for pid, resumed in [("", ""), ("123", "mResumedActivity: other.app/.Main")]:
                def output(args, **kwargs):
                    if "getprop" in args:
                        return "x86_64"
                    if "pidof" in args:
                        return pid
                    if "dumpsys" in args:
                        return resumed
                    return "ok"
                with self.subTest(pid=pid), patch.dict(os.environ, {"ANDROID_HOME": directory}), \
                     patch("android_smoke.inspect_apk", return_value=apk()), \
                     patch("android_smoke.subprocess.check_output", side_effect=output), \
                     patch("android_smoke.subprocess.run"), patch("android_smoke.time.sleep"):
                    with self.assertRaises(ValueError):
                        run_smoke(directory)


class DownloadTests(unittest.TestCase):
    def test_missing_and_malformed_secrets(self):
        for email, token in [("", ""), ("user@example.com", ""), ("user@example.com", "token\ninjected=value")]:
            with self.subTest(token=bool(token)), patch.dict(os.environ, {"GOOGLE_EMAIL": email, "GOOGLE_AAS_TOKEN": token}):
                with self.assertRaises(ValueError):
                    credentials()

    def test_config_is_private_and_removed_and_errors_are_suppressed(self):
        for mode in ("success", "zero_no_apk", "failure", "timeout"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                seen = []
                def run(args, **kwargs):
                    self.assertNotIn("private-token", args)
                    self.assertNotIn("GOOGLE_AAS_TOKEN", kwargs["env"])
                    config = Path(args[args.index("-i") + 1])
                    seen.append(config)
                    self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
                    self.assertIn("aas_token = private-token", config.read_text())
                    self.assertNotIn("--accept-tos", args)
                    if mode == "timeout":
                        raise subprocess.TimeoutExpired(args, 600, output=b"private-token")
                    if mode == "success":
                        (Path(args[-1]) / "base.apk").touch()
                    return subprocess.CompletedProcess(args, int(mode == "failure"), b"private-token", b"private-token")
                output = io.StringIO()
                with patch.dict(os.environ, {"GOOGLE_EMAIL": "user@example.com", "GOOGLE_AAS_TOKEN": "private-token"}), \
                     patch("play_download.subprocess.run", side_effect=run), contextlib.redirect_stdout(output):
                    if mode == "success":
                        download(Path(directory) / "apks", "profile")
                    else:
                        with self.assertRaises(ValueError) as raised:
                            download(Path(directory) / "apks", "profile")
                        self.assertNotIn("private-token", str(raised.exception))
                self.assertFalse(seen[0].exists())
                self.assertNotIn("private-token", output.getvalue())

    def test_refuses_existing_downloads(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"GOOGLE_EMAIL": "user@example.com", "GOOGLE_AAS_TOKEN": "token"}):
            (Path(directory) / "old.apk").touch()
            with self.assertRaises(ValueError):
                download(directory, "profile")

    def test_profile_requests_only_x86_64(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device.properties"
            path.write_text("[other]\nPlatforms=arm64-v8a\n[google_kiwi_x86_64]\nBuild.VERSION.SDK_INT=34\nPlatforms=x86_64,x86,arm64-v8a\n[next]\nPlatforms=arm64-v8a\n")
            prepare_profile(path)
            self.assertEqual(path.read_text(), "[google_kiwi_x86_64]\nBuild.VERSION.SDK_INT=34\nPlatforms=x86_64\n")

    def test_profile_fails_closed_if_upstream_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device.properties"
            for text in ["[other]\n", "[google_kiwi_x86_64]\nBuild.VERSION.SDK_INT=35\nPlatforms=x86_64\n",
                         "[google_kiwi_x86_64]\nBuild.VERSION.SDK_INT=34\n"]:
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    prepare_profile(path)


if __name__ == "__main__":
    unittest.main()
