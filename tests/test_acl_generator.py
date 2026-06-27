"""
test_acl_generator.py

Run with: python3 -m pytest test_acl_generator.py -v
(or just: python3 test_acl_generator.py for a plain run without pytest)
"""

from acl_generator import extract_host, derive_registrable_domain, build_acl_lines


def test_extract_host_basic():
    assert extract_host("https://www.example.com/path?x=1") == "www.example.com"


def test_extract_host_no_path():
    assert extract_host("https://example.com") == "example.com"


def test_derive_registrable_domain_simple():
    assert derive_registrable_domain("www.example.com") == "example.com"
    assert derive_registrable_domain("blog.assets.example.com") == "example.com"


def test_derive_registrable_domain_multipart_tld_KNOWN_LIMITATION():
    """
    This documents the KNOWN BUG, it does not assert correct behavior.
    For "www.example.co.uk" the correct registrable domain is
    "example.co.uk", but this heuristic returns "co.uk" because it only
    looks at the last two labels. This test exists so that if/when the
    heuristic is replaced with a real public-suffix-list implementation,
    this test will start FAILING -- which is the signal to update it
    to assert the correct value ("example.co.uk") instead.
    """
    result = derive_registrable_domain("www.example.co.uk")
    assert result == "co.uk", (
        "If this assertion fails, it likely means the heuristic was "
        "fixed -- update this test to assert 'example.co.uk' and remove "
        "this docstring warning."
    )


def test_build_acl_lines_single_page():
    lines = build_acl_lines(
        entry_url="https://www.example.com/about",
        scope="single-page",
        static_extras=[],
        common_list=[".cloudflare.com", "fonts.gstatic.com"],
    )
    assert lines[0] == "www.example.com"
    assert ".cloudflare.com" in lines
    assert "fonts.gstatic.com" in lines


def test_build_acl_lines_site_scope_wildcards_registrable_domain():
    lines = build_acl_lines(
        entry_url="https://www.example.com/",
        scope="site",
        static_extras=[],
        common_list=[],
    )
    assert lines == [".example.com"]


def test_build_acl_lines_dedupes_and_skips_comments_blank_lines():
    lines = build_acl_lines(
        entry_url="https://www.example.com/",
        scope="single-page",
        static_extras=["www.example.com", "# a comment", ""],
        common_list=["www.example.com", ".cloudflare.com"],
    )
    # www.example.com should appear only once despite being in target,
    # static_extras, AND common_list
    assert lines.count("www.example.com") == 1
    assert ".cloudflare.com" in lines
    assert "# a comment" not in lines


def test_build_acl_lines_rejects_unknown_scope():
    try:
        build_acl_lines(
            entry_url="https://www.example.com/",
            scope="bogus-scope",
            static_extras=[],
            common_list=[],
        )
        assert False, "expected ValueError for unknown scope"
    except ValueError:
        pass


if __name__ == "__main__":
    # Plain runner if pytest isn't installed
    import sys
    import traceback

    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except Exception:
            failures += 1
            print(f"FAIL: {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)