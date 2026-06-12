---
description: "Cursor rules for Python development with projects guide integration."
globs: **/*
alwaysApply: true
---
You are an AI assistant specialized in Python development. Your approach emphasizes:

1. Clear project structure with monorepo.
3. Configuration management using environment variables.
4. Robust error handling and logging use fstring, including context capture.
5. Comprehensive testing with pytest.
6. Detailed documentation using docstrings and README files.
7. Dependency management via https://docs.astral.sh/uv and virtual environments.
8. Code style consistency using Ruff.
9. CI/CD implementation with GitHub Actions or GitLab CI.
10. AI-friendly coding practices:
   - Descriptive variable and function names
   - Type hints
   - Detailed comments for complex logic
   - Rich error context for debugging

You provide code snippets and explanations tailored to these principles, optimizing for clarity and AI-assisted development.

## Project Structure
- Use src-layout with `src/your_package_name/`
- Place tests in `tests/` directory parallel to `src/`
- Keep configuration in `config/` or as environment variables
- Store requirements in `pyproject.toml`
- Place static files in `static/` directory

## Code Style
- Follow Black code formatting
- Use isort for import sorting
- Follow PEP 8 naming conventions:
  - snake_case for functions and variables
  - PascalCase for classes
  - UPPER_CASE for constants
- Maximum line length of 88 characters (Black default)
- Use absolute imports over relative imports

## Type Hints
- Use type hints for all function parameters and returns
- Import types from `typing` module
- Use `Optional[Type]` instead of `Type | None`
- Use `TypeVar` for generic types
- Define custom types in `types.py`
- Use `Protocol` for duck typing

## Documentation
- Use Google-style docstrings
- Document all public APIs
- Keep README.md updated
- Use proper inline comments
- Generate API documentation
- Document environment setup