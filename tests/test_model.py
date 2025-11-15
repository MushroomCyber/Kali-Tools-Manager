import unittest

from kalitools.model import Tool


class ToolModelTests(unittest.TestCase):
    def test_command_list_is_normalized_and_deduped(self):
        tool = Tool(name="Nmap", commands=["nmap", "Nmap", "", "nmap"], subpackages=["nmap", "nmap-common"])
        self.assertEqual(tool.commands, ["nmap"])  # duplicates removed and original casing preserved
        self.assertEqual(tool.subpackages, ["nmap", "nmap-common"])

    def test_metadata_defaults_are_populated(self):
        tool = Tool(name="ffuf", commands=[])
        self.assertEqual(tool.commands[0], "ffuf")
        self.assertFalse(tool.installed)
        self.assertEqual(tool.category, "other")
        self.assertEqual(tool.metadata, {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
