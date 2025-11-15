import pathlib
from kalitools_lib.scraping import parse_tool_page

SAMPLE_HTML = """
<html><body>
<dl>
  <dt>Package</dt><dd>nmap</dd>
  <dt>Tags</dt><dd><a href='/tag/recon'>recon</a>, <a href='/tag/network'>network</a></dd>
</dl>
</body></html>
"""

def test_parse_tool_page_basic():
    parsed = parse_tool_page(SAMPLE_HTML)
    assert parsed is not None
    pkg, category, tags = parsed
    assert pkg == 'nmap'
    assert category == 'recon'
    assert 'recon' in tags
