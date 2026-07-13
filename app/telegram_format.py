"""Wraps every outgoing message in a single Telegram <pre> block so every
reply renders in monospace -- a uniform code-block/terminal look, rather
than selectively parsing markdown into bold/inline-code/fenced-code-block
pieces (simpler, and nothing to partially get wrong)."""


def escape_html(text: str) -> str:
    """Escapes the only 3 characters HTML parse mode treats as special.
    & must be escaped first, or it would double-escape the & introduced by
    the following two replacements."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap_as_code_block(text: str) -> str:
    """The whole message becomes one <pre> block. Escaping the entire
    content up front (rather than at each individual interpolation site)
    means any special character anywhere -- an LLM answer, an uploaded
    filename, anything -- is automatically safe without needing to
    remember to escape it piecemeal."""
    return f"<pre>{escape_html(text)}</pre>"
