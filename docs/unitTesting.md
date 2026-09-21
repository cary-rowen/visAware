# Unit Testing

This template provides a built-in unit testing structure powered by Python's standard `unittest` framework.

## Running Tests Locally

To run the unit test suite locally using `uv`:

``` bash
uv run python -m unittest discover -s tests/unit -v
```

This runs the template tests used by CI. Vis Aware's existing tests in `tests/test_*.py` can be run separately with `uv run pytest` and the relevant test path.

Or execute tests for a specific file:

``` bash
uv run python -m unittest -v tests/unit/template/test_sanity.py
```
