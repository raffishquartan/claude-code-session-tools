"""Tests for tool / MCP / plugin classification and token attribution."""

from claude_code_usage import attribution


def test_classify_native_tool() -> None:
    assert attribution.classify_tool("Bash") == ("native", "Bash")


def test_classify_mcp_tool() -> None:
    assert attribution.classify_tool("mcp__opentabs__tesco_view_basket") == (
        "mcp",
        "opentabs",
    )


def test_classify_plugin_tool() -> None:
    name = "mcp__plugin_github_github__list_issues"
    assert attribution.classify_tool(name) == ("plugin", "github")


def test_classify_chrome_devtools_plugin_tool() -> None:
    name = "mcp__plugin_chrome-devtools-mcp_chrome-devtools__click"
    assert attribution.classify_tool(name) == ("plugin", "chrome-devtools-mcp")


def test_extract_tool_uses_returns_tool_names_in_order() -> None:
    message = {
        "content": [
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "name": "Bash", "id": "t1"},
            {"type": "tool_use", "name": "Read", "id": "t2"},
            {"type": "tool_use", "name": "mcp__opentabs__tesco_view_basket", "id": "t3"},
        ],
    }
    assert attribution.extract_tool_uses(message) == [
        "Bash",
        "Read",
        "mcp__opentabs__tesco_view_basket",
    ]


def test_extract_tool_uses_handles_no_content() -> None:
    assert attribution.extract_tool_uses({}) == []
    assert attribution.extract_tool_uses({"content": "string-not-list"}) == []
    assert attribution.extract_tool_uses({"content": []}) == []


def test_split_tokens_evenly_across_tools() -> None:
    rows = attribution.attribute_tokens(
        tools=["Bash", "Read", "Bash", "mcp__opentabs__tesco_view_basket"],
        tokens=400,
    )
    assert rows == [
        {"tool": "Bash", "kind": "native", "identifier": "Bash", "tokens": 100.0},
        {"tool": "Read", "kind": "native", "identifier": "Read", "tokens": 100.0},
        {"tool": "Bash", "kind": "native", "identifier": "Bash", "tokens": 100.0},
        {
            "tool": "mcp__opentabs__tesco_view_basket",
            "kind": "mcp",
            "identifier": "opentabs",
            "tokens": 100.0,
        },
    ]


def test_no_tools_attributes_to_no_tool_bucket() -> None:
    rows = attribution.attribute_tokens(tools=[], tokens=400)
    assert rows == [
        {
            "tool": "<no-tool>",
            "kind": "no-tool",
            "identifier": "<no-tool>",
            "tokens": 400.0,
        },
    ]


def test_zero_tokens_still_emits_rows() -> None:
    rows = attribution.attribute_tokens(tools=["Bash", "Read"], tokens=0)
    assert [r["tokens"] for r in rows] == [0.0, 0.0]
