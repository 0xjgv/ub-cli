import base64
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from ub_cli.signature import G, ed25519_verify, p, point_decompress, point_equal, point_mul, q, recover_x, verify
from ub_cli import update
from ub_cli.update import run

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "minisign"
KEY = (FIXTURES / "key.pub").read_text().splitlines()[1]
MESSAGE = (FIXTURES / "checksums.txt").read_bytes()
SIGNATURE = (FIXTURES / "checksums.txt.minisig").read_text()


# RFC 8032, section 7.1, tests 1 and 2: (public key, message, signature).
RFC_VECTORS = [
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


class SignatureTests(unittest.TestCase):
    def test_ed25519_matches_rfc_8032_vectors(self):
        for public, message, signature in RFC_VECTORS:
            public, message, signature = map(bytes.fromhex, (public, message, signature))
            self.assertTrue(ed25519_verify(public, message, signature))
            self.assertFalse(ed25519_verify(public, message + b"x", signature))
            flipped = bytes([signature[0] ^ 1]) + signature[1:]
            self.assertFalse(ed25519_verify(public, message, flipped))

    def test_accepts_prehashed_and_legacy_minisign_signatures(self):
        self.assertEqual("ub v9.9.9", verify(KEY, MESSAGE, SIGNATURE))
        legacy = (FIXTURES / "legacy.txt.minisig").read_text()
        self.assertEqual("legacy", verify(KEY, (FIXTURES / "legacy.txt").read_bytes(), legacy))

    def test_point_equal_compares_projective_points(self):
        point = point_mul(3, G)
        self.assertTrue(point_equal(point, tuple(c * 5 % p for c in point)))
        self.assertFalse(point_equal(point, (-point[0] % p, point[1], point[2], -point[3] % p)))
        self.assertFalse(point_equal(point, (point[0], -point[1] % p, point[2], -point[3] % p)))

    def test_decompress_rejects_noncanonical_and_off_curve_encodings(self):
        self.assertEqual(recover_x(1, 0), 0)
        self.assertIsNone(recover_x(1, 1))  # x = 0 has no negative encoding
        self.assertIsNone(recover_x(p - 2, 0))  # y = -2 is not on the curve
        self.assertIsNone(point_decompress(p.to_bytes(32, "little")))  # y = p is y = 0, encoded non-canonically

    def test_ed25519_rejects_wrong_lengths_and_noncanonical_s(self):
        public, message, signature = map(bytes.fromhex, RFC_VECTORS[0])
        self.assertFalse(ed25519_verify(public, message, signature + b"\0"))
        self.assertFalse(ed25519_verify(public + b"\0", message, signature))
        # With the identity as key and R, s = 0 verifies, so only the range check rejects s = q.
        identity = (1).to_bytes(32, "little")
        self.assertTrue(ed25519_verify(identity, b"m", identity + bytes(32)))
        self.assertFalse(ed25519_verify(identity, b"m", identity + q.to_bytes(32, "little")))

    def test_rejects_tampering_and_other_keys(self):
        lines = SIGNATURE.splitlines()
        blob, global_signature = base64.b64decode(lines[1]), base64.b64decode(lines[3])
        key = base64.b64decode(KEY)
        encode = lambda data: base64.b64encode(data).decode()
        signature = lambda blob=lines[1], comment=lines[2], global_signature=lines[3]: "\n".join(
            [lines[0], blob, comment, global_signature])
        other_key = "RWTLb536jI8+3VG6zclr5kbCi69IAiL7HkhEefxbOJSlJRl2v9muK6ow"
        cases = {
            "message": (KEY, MESSAGE + b"x", SIGNATURE, "verification failed"),
            "trusted comment": (KEY, MESSAGE, signature(comment="trusted comment: ub v0.0.1"), "verification failed"),
            "other key": (other_key, MESSAGE, SIGNATURE, "different key"),
            "key id": (KEY, MESSAGE, signature(blob=encode(blob[:2] + bytes(8) + blob[10:])), "different key"),
            "key prefix": (encode(b"Ex" + key[2:]), MESSAGE, SIGNATURE, "invalid minisign public key"),
            "key length": (encode(key + b"\0"), MESSAGE, SIGNATURE, "invalid minisign public key"),
            "key not base64": (KEY[:5] + "!" + KEY[5:], MESSAGE, SIGNATURE, "base64"),
            "truncated": (KEY, MESSAGE, "\n".join(lines[:3]), "malformed"),
            "untrusted comment": (KEY, MESSAGE, signature(comment="untrusted comment: x"), "malformed"),
            "not base64": (KEY, MESSAGE, signature(blob="!!"), "base64"),
            "signature not base64": (KEY, MESSAGE, signature(blob=lines[1][:5] + "!" + lines[1][5:]), "base64"),
            "global signature not base64": (
                KEY, MESSAGE, signature(global_signature=lines[3][:5] + "!" + lines[3][5:]), "base64"),
            "signature length": (KEY, MESSAGE, signature(blob=encode(blob + b"\0")), "malformed"),
            "global signature length": (
                KEY, MESSAGE, signature(global_signature=encode(global_signature + b"\0")), "malformed"),
            "algorithm": (KEY, MESSAGE, signature(blob=encode(b"XX" + blob[2:])), "unsupported"),
        }
        for name, (*arguments, error) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, error):
                verify(*arguments)


class UpdateCommandTests(unittest.TestCase):
    def invoke(self, argv):
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            return run(argv), stderr.getvalue()

    def test_verify_signature_reports_status(self):
        good = [KEY, str(FIXTURES / "checksums.txt"), str(FIXTURES / "checksums.txt.minisig")]
        self.assertEqual(0, self.invoke(["--verify-signature", *good])[0])
        bad = [KEY, str(FIXTURES / "key.pub"), str(FIXTURES / "checksums.txt.minisig")]
        status, error = self.invoke(["--verify-signature", *bad])
        self.assertEqual(1, status)
        self.assertIn("signature verification failed", error)

    def test_passes_version_and_verifier_to_the_installer(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch("ub_cli.update.subprocess.run") as installer:
            installer.return_value.returncode = 0
            self.assertEqual(0, self.invoke(["v0.1.0"])[0])
        environment = installer.call_args.kwargs["env"]
        self.assertEqual("v0.1.0", environment["UB_VERSION"])
        self.assertEqual(sys.executable, environment["UB_SIGNATURE_VERIFIER"])

    def test_rejects_extra_arguments_and_unknown_flags(self):
        for argv in (["v0.1.0", "v0.2.0"], ["--force"]):
            with self.subTest(argv=argv):
                self.assertEqual(2, self.invoke(argv)[0])

    def test_help_prints_usage(self):
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(0, run([flag]))
                self.assertTrue(stdout.getvalue().startswith("Usage: ub update"))

    def test_source_install_points_to_package_manager(self):
        # A source install has no sys.frozen at all; leave it unset.
        self.assertFalse(hasattr(sys, "frozen"))
        with patch("ub_cli.update.subprocess.run") as installer:
            status, error = self.invoke([])
        installer.assert_not_called()
        self.assertEqual(1, status)
        self.assertIn("Python package manager", error)

    def run_frozen(self, environment):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "executable", "/opt/ub/bin/ub"), \
                patch.dict(os.environ, environment, clear=True), \
                patch("ub_cli.update.subprocess.run") as installer:
            installer.return_value.returncode = 7
            self.assertEqual(7, self.invoke([])[0])
        self.assertEqual(["sh", Path(update.__file__).with_name("install.sh")], installer.call_args.args[0])
        env = installer.call_args.kwargs["env"]
        self.assertEqual("1", env["PYINSTALLER_RESET_ENVIRONMENT"])
        return env

    def test_frozen_install_defaults_to_executable_directory(self):
        env = self.run_frozen({"PATH": "/bin"})
        self.assertEqual(str(Path("/opt/ub/bin/ub").resolve().parent), env["UB_INSTALL_DIR"])
        self.assertEqual("/bin", env["PATH"])

    def test_frozen_install_keeps_chosen_directory(self):
        self.assertEqual("/custom", self.run_frozen({"UB_INSTALL_DIR": "/custom"})["UB_INSTALL_DIR"])


if __name__ == "__main__":
    unittest.main()
