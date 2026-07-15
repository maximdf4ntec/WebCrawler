# Good vs Bad Tests — trajectory-to-tests

## Good: tests behavior through the public seam

```python
def test_openai_parser_extracts_tool_call_arguments_as_dict():
    trajectory = load_fixture("openai_single_tool_call.json")
    result = parse_trajectory(trajectory, format="openai_messages")
    assert result.tool_calls[0].arguments == {"query": "SF weather"}

def test_openai_parser_raises_on_missing_tool_response():
    trajectory = load_fixture("openai_tool_call_no_response.json")
    with pytest.raises(TrajectoryParseError, match="missing tool response"):
        parse_trajectory(trajectory, format="openai_messages")
```

Why: goes through `parse_trajectory` (the public seam), names the exact behavior and condition, survives a full rewrite of the parser's internals.

## Bad: tests internals, breaks on refactor

```python
def test_parser_internal_state():
    parser = OpenAIParser()
    parser._process_raw(raw_messages)
    assert parser._buffer == [...]   # private attribute, not the public contract
```

Why: `_buffer` is an implementation detail. Renaming it or switching to a generator breaks this test even though behavior is unchanged.

## Good: one behavior per test, descriptive name

```python
def test_soft_rule_yaml_export_preserves_nested_assertion_groups(): ...
def test_soft_rule_yaml_export_rejects_circular_group_references(): ...
```

## Bad: bundled, vague

```python
def test_yaml_export():
    # tests five unrelated things, first assertion failure hides the rest
    ...
```

## Parametrization over copy-paste

```python
@pytest.mark.parametrize("format_name", ["openai_messages", "anthropic_messages", "langfuse_trace"])
def test_parser_rejects_empty_trajectory(format_name):
    with pytest.raises(TrajectoryParseError, match="empty trajectory"):
        parse_trajectory({}, format=format_name)
```

Use parametrization when the *same* behavior must hold across several inputs — not as a way to avoid writing a docstring-quality test name for each distinct behavior.
