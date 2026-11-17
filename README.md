# vmx_oxyz

## UpFashion Google Play smoke test

The manually dispatched [workflow](.github/workflows/upfashion-smoke.yml) downloads
`com.kruxlabs.upfashion` directly from Google Play, installs it in an Android 14
(API 34) x86_64 emulator, and launches it with one `monkey` launcher event. Success
requires the process to remain alive and the app to be the resumed activity after
10 seconds. This checks launch only; it does not test login, search, or other app
features.

### Setup and run

1. In repository **Settings → Secrets and variables → Actions**, configure
   `GOOGLE_EMAIL` and `GOOGLE_AAS_TOKEN` for an account you control and authorize
   for this download. If already configured, reuse those secrets. Never put the
   values in workflow inputs, code, issues, PRs, logs, or chat.
2. The AAS token is apkeep's reusable Google account token, not an OAuth token,
   an Android Publisher service-account key, or a short-lived `ya29` AUTH token.
   If you do not already have one, follow the official
   [apkeep authentication guide](https://github.com/EFForg/apkeep/blob/1.1.0/USAGE-google-play.md)
   privately on your own trusted machine. This workflow does not obtain/extract
   tokens, use token dispensers, change account security, or accept Google Play
   terms. Complete any required account/Play setup yourself outside CI.
3. Review and merge this PR so GitHub registers the `workflow_dispatch` workflow
   on the default branch. In **Actions → UpFashion Play Store smoke test → Run
   workflow**, select a reviewed branch and run it. Dispatch only trusted code:
   the selected ref has access to the two account secrets.
4. A successful run ends with `PASS: installed ... compatible APK(s); UpFashion
   is alive and resumed after 10 seconds.` The separate validation workflow runs
   helper tests on PRs without any Google secrets.

A public [Play listing](https://play.google.com/store/apps/details?id=com.kruxlabs.upfashion)
was found on 2026-11-17. That does not prove this particular account, region, Play
profile, or emulator can download/run the current app. No authenticated download
or emulator installation has been verified during implementation. Secret presence
could not be inspected with the available connector/browser session. A real run
requires both secrets, available Actions minutes/KVM, account access to the free
app, a compatible SDK 34 x86_64 build, and Play/network availability. Paid/DRM apps
are not supported; no purchase or mirror fallback is performed.

### Runner and APK compatibility

The claim that GitHub Ubuntu runners lack KVM is outdated. GitHub's current
[hosted-runner documentation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
says Linux runners support Android hardware acceleration. The
[emulator action documentation](https://github.com/ReactiveCircus/android-emulator-runner#running-hardware-accelerated-emulators-on-linux-runners)
recommends Ubuntu with KVM enabled. This workflow uses `ubuntu-24.04`, checks
`/dev/kvm`, and explicitly enables acceleration rather than switching to macOS.

The default apkeep device profile is ARM, so it is not used. The workflow fetches
a commit- and checksum-pinned upstream
[SDK 34 x86_64 device profile](https://github.com/EFForg/rs-google-play/blob/eb423a3edfc653a1d533d4b7f6c9148d5bc144a8/gpapi/device.properties)
and restricts its advertised `Platforms` to **x86_64 only**. The profile models
Google Play Games on PC; Play may filter mobile-only apps from that profile. It
is a download configuration, not a claim that the emulator is a certified Play
Games device. ARM translation is never assumed. If Play refuses delivery or
returns ARM-only code, obtain an authorized publisher x86_64/universal build or
use a real ARM device in a separately reviewed workflow. Changing the runner to
macOS alone does not fix app/ABI compatibility.

`apkeep 1.1.0` is installed from its official x86_64 Linux release binary with a
pinned SHA-256. Its verified syntax is `-a PACKAGE -d google-play -i CONFIG -o
OPTIONS OUTPUT`; `split_apk=true` downloads split APKs into a directory, with a
full-APK fallback. The helper recursively reads APKs and validates package ID,
version, exactly one base APK, unique split IDs, native library ZIP paths and
`aapt dump badging` metadata. It keeps only x86_64 ABI splits plus delivered
language/density/features, rejects missing x86_64 feature groups and incompatible
base/features, and uses `adb install` for a single APK or `adb install-multiple`
for the validated set. Android checks signatures and required split dependencies
during installation. It does not blindly install every downloaded file or combine
separate releases. Build-tools 34.0.0 is explicitly installed for `aapt`.

The upstream profile includes its SPDX attribution and is downloaded only to the
ephemeral runner. Downloaded APKs and credentials are neither cached nor uploaded
as artifacts. Secrets are scoped to the preflight and download steps, stored in
a mode-0600 temporary apkeep config, kept off command arguments, and removed on
normal completion/failure. Raw apkeep output is suppressed even on errors; CI
reports generic authentication/availability failures instead. Runner teardown
removes ephemeral files if the job is forcibly terminated.

### Validation and troubleshooting

Run locally without credentials or an emulator:

```sh
python3 -m unittest discover -s tests -v
```

The 14 test methods exercise safe/mixed/ARM-only split sets, malformed APK
metadata, base and feature variants, single/multiple install commands, launch
failure detection, missing/malformed credentials, restrictive config permissions,
cleanup, suppressed subprocess output, timeout handling, empty downloads and
profile restrictions. APK/adb/apkeep fixtures are mocked; passing these tests
**does not establish a real Play download, install, or successful app launch**.

- Missing secrets: configure the two names above in GitHub, never in chat.
- No usable Play download: check the authorized account/token, previously
  accepted Play terms, app entitlement, region and profile availability privately.
  apkeep can exit zero on a failed app download, so file validation is mandatory.
- ABI or SDK incompatibility: an x86_64 SDK 34 payload must exist for this profile.
  A newer minimum Android version requires updating both the requested profile
  and emulator API in a reviewed change. Do not add ARM splits to an x86 emulator.
- Install or launch failure: review the generic workflow error and adb result.
  An emulator may also fail Play Integrity or DRM checks; this workflow does not
  bypass those checks or sign the emulator into the Google account.
