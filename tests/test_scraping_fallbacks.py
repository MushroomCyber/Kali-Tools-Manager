from kalitools_lib.scraping import parse_tool_page

HTML_TABLE = """
<table>
  <tr><th>Package</th><td>sqlmap</td></tr>
  <tr><th>Tags</th><td><a href='#'>web</a>, <a href='#'>sql</a></td></tr>
</table>
"""

HTML_JSONLD = """
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"SoftwareApplication","name":"ffuf","applicationCategory":"Security"}
</script>
"""

def test_fallback_table_parsing():
    parsed = parse_tool_page(HTML_TABLE)
    assert parsed is not None
    pkg, category, tags = parsed
    assert pkg == 'sqlmap'
    assert category in ("web","database","other")


def test_fallback_jsonld_name():
    parsed = parse_tool_page(HTML_JSONLD)
    assert parsed is not None
    pkg, category, tags = parsed
    assert pkg == 'ffuf'
