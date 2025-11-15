import types
from kalitools import KaliToolsManager

class DummySubprocessResult:
    def __init__(self, stdout='', returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_get_popularity_rating(monkeypatch):
    mgr = KaliToolsManager()

    def fake_run(args, capture_output=True, text=True, timeout=6):
        if args[:2] == ['apt-cache', 'rdepends']:
            return DummySubprocessResult(stdout='Reverse Depends:\n  pkgA\n  pkgB\n  pkgC\n  pkgD', returncode=0)
        return DummySubprocessResult(stdout='', returncode=0)

    monkeypatch.setattr('subprocess.run', fake_run)
    rating = mgr.get_popularity_rating('customtool')
    assert rating >= 3  # with four reverse deps should be at least 3
