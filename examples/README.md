# Examples

`quickstart.py` is the smallest complete use of the library: one observation,
two predictions, two time policies, and an explanation of the difference.

```bash
uv run python examples/quickstart.py
```

`features.json` is a feature set in the format the command line accepts:

```bash
uv run asoflab sql --config examples/features.json --policy pipeline_as_known
```

For worked cases with answers calculated by hand, list the fixture corpus:

```bash
uv run asoflab fixtures list
```

```bash
uv run asoflab fixtures show correction_keeps_earlier_version
```
