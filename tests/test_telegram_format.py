"""Tests for app.telegram_format's HTML escaping and whole-message
code-block wrapping -- pure functions, no mocking or infra needed."""
from app.telegram_format import escape_html, wrap_as_code_block


class TestEscapeHtml:
    def test_escapes_ampersand_first_to_avoid_double_escaping(self):
        assert escape_html("a & b") == "a &amp; b"

    def test_escapes_angle_brackets(self):
        assert escape_html("x < 5 and y > 3") == "x &lt; 5 and y &gt; 3"

    def test_plain_text_is_unchanged(self):
        assert escape_html("hello world") == "hello world"

    def test_does_not_double_escape_an_existing_entity(self):
        # &lt; contains no raw &, <, or > itself once escaped -- confirms
        # the single-pass replace order doesn't mangle its own output.
        assert escape_html("&lt;") == "&amp;lt;"


class TestWrapAsCodeBlock:
    def test_wraps_plain_text_in_pre_tags(self):
        assert wrap_as_code_block("hello") == "<pre>hello</pre>"

    def test_escapes_content_before_wrapping(self):
        assert wrap_as_code_block("x < 5 & y > 3") == "<pre>x &lt; 5 &amp; y &gt; 3</pre>"

    def test_a_filename_containing_angle_brackets_cannot_produce_a_stray_tag(self):
        result = wrap_as_code_block('"report <draft>.pdf" received')
        assert "<draft>" not in result
        assert "&lt;draft&gt;" in result

    def test_multiline_text_is_preserved_inside_the_block(self):
        text = "line one\nline two\nline three"
        assert wrap_as_code_block(text) == f"<pre>{text}</pre>"

    def test_markdown_syntax_is_left_untouched_as_literal_text(self):
        # No selective parsing -- **bold** and ```code``` markers are just
        # literal characters, escaped like everything else, not converted.
        text = "**bold** and ```code```"
        assert wrap_as_code_block(text) == f"<pre>{text}</pre>"
