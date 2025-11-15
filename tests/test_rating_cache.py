from pathlib import Path
from kalitools_lib.cache import RatingCache

def test_rating_cache_roundtrip(tmp_path: Path):
    path = tmp_path / 'ratings.json'
    rc = RatingCache(path)
    rc.put('nmap', 5)
    rc.put('sqlmap', 4)
    assert rc.save() is True

    rc2 = RatingCache(path)
    assert rc2.get('nmap') == 5
    assert rc2.get('sqlmap') == 4
