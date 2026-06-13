# Contribution Guidelines

## Reporting Bugs

1. Check existing issues on GitHub
2. Create new issue with:
   - Description of the bug
   - Steps to reproduce
   - Expected behavior
   - Actual behavior
   - Screenshots (if applicable)
   - Environment (OS, browser, Python version)

## Feature Requests

1. Create GitHub issue with label `enhancement`
2. Describe the feature and use case
3. Explain why it's valuable

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`feature/description` or `fix/description`)
3. Write code following existing conventions
4. Add tests for new functionality
5. Ensure all tests pass: `pytest tests/ -v`
6. Create PR with description of changes
7. Request code review

## Code Style

- **Python**: PEP8, snake_case for functions/variables
- **JavaScript**: camelCase
- **Templates**: Jinja2 with 4-space indentation
- **CSS**: Tailwind utility classes (no custom CSS unless necessary)
- **No comments in code** (keep it self-documenting)
- **No emojis in code** (unless in user-facing messages)

## Commit Message Format

```
type: short description (max 50 chars)

Longer description if needed (max 72 chars per line)
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `devops`

Examples:
- `feat: add CSV student import endpoint`
- `fix: anti-cheat penalty not saved to submission`
- `docs: update API reference with new endpoints`

## Branch Naming

- Feature: `feature/description`
- Bug fix: `fix/description`
- Documentation: `docs/description`
- Refactoring: `refactor/description`

## Testing

- All new features must include tests
- Minimum coverage: 80%
- Run full suite before PR: `pytest tests/ -v`
