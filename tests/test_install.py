import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ub installer ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.base = self.root / "releases"
        self.install = self.root / "bin"
        self.commands = self.root / "commands"
        self.commands.mkdir()
        (self.commands / "uname").write_text(
            '#!/bin/sh\ncase $1 in -s) echo "$TEST_OS";; -m) echo "$TEST_ARCH";; esac\n'
        )
        (self.commands / "sysctl").write_text('#!/bin/sh\necho "${TEST_ROSETTA:-0}"\n')
        for command in self.commands.iterdir():
            command.chmod(0o755)
        self.environment = os.environ | {
            "UB_DOWNLOAD_BASE": str(self.base),
            "UB_INSTALL_DIR": str(self.install),
            "PATH": f"{self.commands}:{os.environ['PATH']}",
            "TEST_OS": "Linux",
            "TEST_ARCH": "x86_64",
        }
        self.environment.pop("UB_VERSION", None)

    def release(self, version="0.1.0", pinned=False, binary=None):
        location = self.base / (f"download/v{version}" if pinned else "latest/download")
        location.mkdir(parents=True, exist_ok=True)
        checksums = []
        for platform in ("darwin_amd64", "darwin_arm64", "linux_amd64", "linux_arm64"):
            archive = location / f"ub_{version}_{platform}.tar.gz"
            script = binary or f"#!/bin/sh\necho 'ub {version}'\n"
            payload = script.encode()
            with tarfile.open(archive, "w:gz") as output:
                member = tarfile.TarInfo("ub")
                member.size = len(payload)
                member.mode = 0o755
                output.addfile(member, io.BytesIO(payload))
            checksums.append(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
        (location / "checksums.txt").write_text("".join(checksums))
        return location

    def run_install(self, **overrides):
        return subprocess.run(
            ["sh", str(INSTALLER)], env=self.environment | overrides,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def test_installs_and_updates_without_changing_other_files(self):
        self.release()
        first = self.run_install()
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertIn("Installed", first.stdout)
        preserved = self.install / "other-tool"
        preserved.write_text("keep\n")
        self.release("0.2.0")
        update = self.run_install()
        self.assertEqual(0, update.returncode, update.stderr)
        self.assertIn("ub 0.1.0 -> ub 0.2.0", update.stdout)
        version = subprocess.check_output([str(self.install / "ub"), "--version"], text=True)
        self.assertEqual("ub 0.2.0\n", version)
        self.assertEqual("keep\n", preserved.read_text())
        self.assertFalse(list(self.install.glob(".ub.*")))

    def test_version_can_be_pinned_with_or_without_v_prefix(self):
        self.release("0.1.0", pinned=True)
        for value in ("0.1.0", "v0.1.0"):
            with self.subTest(value=value):
                result = self.run_install(UB_VERSION=value)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("ub 0.1.0", result.stdout)

    def test_all_platforms_and_rosetta_select_their_archive(self):
        location = self.release()
        for system, machine, rosetta, platform in (
            ("Darwin", "x86_64", "0", "darwin_amd64"),
            ("Darwin", "arm64", "0", "darwin_arm64"),
            ("Darwin", "x86_64", "1", "darwin_arm64"),
            ("Linux", "x86_64", "0", "linux_amd64"),
            ("Linux", "aarch64", "0", "linux_arm64"),
        ):
            with self.subTest(system=system, machine=machine, rosetta=rosetta):
                all_checksums = (location / "checksums.txt").read_text()
                selected = next(line for line in all_checksums.splitlines(True) if platform in line)
                (location / "checksums.txt").write_text(selected)
                result = self.run_install(TEST_OS=system, TEST_ARCH=machine, TEST_ROSETTA=rosetta)
                self.assertEqual(0, result.returncode, result.stderr)
                (location / "checksums.txt").write_text(all_checksums)

    def test_checksum_failure_preserves_installed_binary(self):
        location = self.release()
        self.assertEqual(0, self.run_install().returncode)
        original = (self.install / "ub").read_bytes()
        (location / "ub_0.1.0_linux_amd64.tar.gz").write_bytes(b"tampered")
        result = self.run_install()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertEqual(original, (self.install / "ub").read_bytes())

    def test_unrunnable_or_wrong_version_binary_preserves_existing_install(self):
        self.release()
        self.assertEqual(0, self.run_install().returncode)
        original = (self.install / "ub").read_bytes()
        for binary in ("#!/bin/sh\nexit 1\n", "#!/bin/sh\necho 'ub 9.9.9'\n"):
            with self.subTest(binary=binary):
                self.release("0.2.0", binary=binary)
                result = self.run_install()
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(original, (self.install / "ub").read_bytes())
                self.assertFalse(list(self.install.glob(".ub.*")))

    def test_invalid_pin_and_unsupported_platform_fail_before_install(self):
        for override in ({"UB_VERSION": "../main"}, {"TEST_OS": "Windows"}, {"TEST_ARCH": "riscv64"}):
            with self.subTest(override=override):
                result = self.run_install(**override)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(self.install.exists())


if __name__ == "__main__":
    unittest.main()
