import unittest
from unittest import mock

from kalitools import cli


class CliTests(unittest.TestCase):
    def test_parser_defaults(self):
        args = cli.parse_args([])
        self.assertEqual(args.mode, "auto")
        self.assertEqual(args.log_level.upper(), "INFO")
        self.assertEqual(args.discovery_workers, 8)
        self.assertAlmostEqual(args.discovery_delay, 0.2)
        self.assertFalse(args.debug_scraper)

    def test_resolve_ui_mode_forces_basic_on_non_linux(self):
        with mock.patch("kalitools.cli.sys.platform", "win32"):
            self.assertEqual(cli.resolve_ui_mode("rich"), "basic")

    def test_resolve_ui_mode_auto_respects_termios_flag(self):
        with mock.patch("kalitools.cli.sys.platform", "linux"), mock.patch("kalitools.cli.TERMIOS_AVAILABLE", False):
            self.assertEqual(cli.resolve_ui_mode("auto"), "basic")
        with mock.patch("kalitools.cli.sys.platform", "linux"), mock.patch("kalitools.cli.TERMIOS_AVAILABLE", True):
            self.assertEqual(cli.resolve_ui_mode("auto"), "rich")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
