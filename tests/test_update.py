import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from ub_cli.signature import verify
from ub_cli.update import run

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "minisign"
KEY = (FIXTURES / "key.pub").read_text().splitlines()[1]
MESSAGE = (FIXTURES / "checksums.txt").read_bytes()
SIGNATURE = (FIXTURES / "checksums.txt.minisig").read_text()


class SignatureTests(unittest.TestCase):
    def test_accepts_prehashed_and_legacy_minisign_signatures(self):
        self.assertEqual("ub v9.9.9", verify(KEY, MESSAGE, SIGNATURE))
        legacy = (FIXTURES / "legacy.txt.minisig").read_text()
        self.assertEqual("legacy", verify(KEY, (FIXTURES / "legacy.txt").read_bytes(), legacy))

    def test_rejects_tampering_and_other_keys(self):
        lines = SIGNATURE.splitlines()
        other_key = "RWTLb536jI8+3VG6zclr5kbCi69IAiL7HkhEefxbOJSlJRl2v9muK6ow"
        cases = {
            "message": (KEY, MESSAGE + b"x", SIGNATURE),
            "trusted comment": (KEY, MESSAGE, "\n".join([*lines[:2], "trusted comment: ub v0.0.1", lines[3]])),
            "other key": (other_key, MESSAGE, SIGNATURE),
            "truncated": (KEY, MESSAGE, "\n".join(lines[:3])),
            "not base64": (KEY, MESSAGE, "\n".join([lines[0], "!!", *lines[2:]])),
        }
        for name, arguments in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
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


if __name__ == "__main__":
    unittest.main()
