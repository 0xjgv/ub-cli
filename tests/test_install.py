import base64
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from ub_cli import signature as ed


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
# Test-only Ed25519 signing (RFC 8032, section 6) so the tests can sign releases.
SECRET = hashlib.sha256(b"ub test key").digest()
KEY_ID = b"ubtest01"


def compress(point):
    inverse = pow(point[2], ed.p - 2, ed.p)
    x, y = point[0] * inverse % ed.p, point[1] * inverse % ed.p
    return (y | (x & 1) << 255).to_bytes(32, "little")


def ed25519_sign(message: bytes) -> tuple[bytes, bytes]:
    digest = hashlib.sha512(SECRET).digest()
    a = int.from_bytes(digest[:32], "little") & ((1 << 254) - 8) | 1 << 254
    public = compress(ed.point_mul(a, ed.G))
    r = ed.sha512_modq(digest[32:] + message)
    encoded_r = compress(ed.point_mul(r, ed.G))
    s = (r + ed.sha512_modq(encoded_r + public + message) * a) % ed.q
    return encoded_r + s.to_bytes(32, "little"), public


PUBLIC_KEY = base64.b64encode(b"Ed" + KEY_ID + ed25519_sign(b"")[1]).decode()


def minisign(message: bytes, comment: str = "ub test") -> str:
    """Sign like `minisign -S`: prehashed signature plus a signed trusted comment."""
    signature, _ = ed25519_sign(hashlib.blake2b(message).digest())
    signed_comment, _ = ed25519_sign(signature + comment.encode())
    blob = base64.b64encode(b"ED" + KEY_ID + signature).decode()
    return f"untrusted comment: test\n{blob}\ntrusted comment: {comment}\n{base64.b64encode(signed_comment).decode()}\n"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ub installer ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.base = self.root / "releases"
        self.install = self.root / "bin"
        self.commands = self.root / "commands"
        self.commands.mkdir()
        # The release key is replaced by a test key the tests can sign with.
        self.installer = self.root / "install.sh"
        self.installer.write_text(
            re.sub(r"(?m)^PUBLIC_KEY=.*$", f"PUBLIC_KEY={PUBLIC_KEY}", INSTALLER.read_text())
        )
        self.verifier = self.commands / "ub-verifier"
        self.verifier.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" -c '
            "'import sys; from ub_cli.cli import run; sys.exit(run(sys.argv[1:]))' \"$@\"\n"
        )
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
        (location / "checksums.txt.minisig").write_text(minisign((location / "checksums.txt").read_bytes()))
        return location

    def run_install(self, **overrides):
        return subprocess.run(
            ["sh", str(self.installer)], env=self.environment | overrides,
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

    def test_skips_download_when_installed_version_is_current(self):
        location = self.release()
        self.assertEqual(0, self.run_install().returncode)
        original = (self.install / "ub").read_bytes()
        (location / "ub_0.1.0_linux_amd64.tar.gz").unlink()
        result = self.run_install()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("already up to date: ub 0.1.0", result.stdout)
        self.assertEqual(original, (self.install / "ub").read_bytes())

    def test_signature_is_required_by_the_verifier_and_minisign(self):
        modes = {"ub update": {"UB_SIGNATURE_VERIFIER": str(self.verifier)}}
        if shutil.which("minisign", path=self.environment["PATH"]):
            modes["minisign"] = {}
        for mode, overrides in modes.items():
            with self.subTest(mode=mode):
                location = self.release()
                checksums = location / "checksums.txt"
                result = self.run_install(**overrides)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertNotIn("not verified", result.stderr)
                (self.install / "ub").unlink()
                checksums.write_text(checksums.read_text() + "\n")
                tampered = self.run_install(**overrides)
                self.assertNotEqual(0, tampered.returncode)
                self.assertIn("invalid signature", tampered.stderr)
                (location / "checksums.txt.minisig").unlink()
                unsigned = self.run_install(**overrides)
                self.assertNotEqual(0, unsigned.returncode)
                self.assertIn("checksums.txt.minisig", unsigned.stderr)
                self.assertFalse((self.install / "ub").exists())

    def test_installs_without_minisign_after_a_warning(self):
        self.release()
        without_minisign = os.pathsep.join(
            entry for entry in self.environment["PATH"].split(os.pathsep)
            if not (Path(entry) / "minisign").exists()
        )
        result = self.run_install(PATH=without_minisign)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("signature not verified", result.stderr)

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
                (location / "checksums.txt.minisig").write_text(minisign(selected.encode()))
                result = self.run_install(TEST_OS=system, TEST_ARCH=machine, TEST_ROSETTA=rosetta)
                self.assertEqual(0, result.returncode, result.stderr)
                (location / "checksums.txt").write_text(all_checksums)
                (location / "checksums.txt.minisig").write_text(minisign(all_checksums.encode()))

    def test_checksum_failure_preserves_installed_binary(self):
        self.release()
        self.assertEqual(0, self.run_install().returncode)
        original = (self.install / "ub").read_bytes()
        location = self.release("0.2.0")
        (location / "ub_0.2.0_linux_amd64.tar.gz").write_bytes(b"tampered")
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
