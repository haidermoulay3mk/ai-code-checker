# AI Code Checker

A pull request reviewer for code written by AI assistants. On every pull request it posts:

1. **What the change does**, in plain English: an overview and a short summary per file, so engineers understand code they did not write.
2. **Whether the change matches its goal.** It compares the code with the pull request's title and description and flags anything unrelated: refactors nobody asked for, extra features, files touched for no reason.
3. **Code that relies on things that don't exist**: invented packages, functions, APIs and config keys.
4. **Bugs and security problems**, each with the reason and, where it fits on one line, a fix you apply with one click.
5. **Leftovers** an assistant often leaves behind: `// ... existing code ...` placeholders, `NotImplementedError` stubs, debugger calls, hard-coded secrets.
6. **Test results** from your own test command, plus suggested tests for changed behaviour that has none.

It only explains, flags and suggests. **It never approves, requests changes or merges**: every review it posts is a plain comment, and a person decides.

## How it works

Two layers run on every pull request:

| Layer | What it does | Needs a Claude API key |
|---|---|---|
| Automatic checks | Imports not declared in `requirements.txt` / `pyproject.toml` / `package.json`, secrets, placeholders, debug leftovers | No |
| Claude review | Explanation, goal check, bugs, security, invented APIs, fixes, suggested tests (model `claude-opus-5-5`) | Yes |

If the Claude review cannot run (no key, API down), the automatic checks still post their results.

The summary is one comment that is updated on every push rather than repeated, and inline comments already posted are not posted again.

## Set it up on a repository

1. Add your Claude API key as a repository secret named `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions).
2. Copy [`examples/ai-code-checker.yml`](examples/ai-code-checker.yml) to `.github/workflows/ai-code-checker.yml` in that repository.
3. Optional: set `test-command` (for example `pytest -q` or `npm test`) and add the steps that install your dependencies before it.

| Input | Default | Meaning |
|---|---|---|
| `anthropic-api-key` | none | Claude API key. Without it only the automatic checks run. |
| `test-command` | none | Command that runs your tests. |
| `fail-on` | `high` | Mark the check red when an issue this severe or worse is found, or tests fail (`high`, `medium`, `low`, `never`). |
| `model` | `claude-opus-5-5` | Claude model. |
| `effort` | `high` | How hard Claude thinks: `low` to `max`. Higher catches more and costs more. |

Pull requests from forks do not receive repository secrets, so on those only the automatic checks run.

## Run it on your own machine

```bash
pip install git+https://github.com/haidermoulay3mk/ai-code-checker
export ANTHROPIC_API_KEY=...
ai-code-checker local --base main --title "What this branch is meant to do" --test-command "pytest -q"
```

It prints the same report to the terminal. `--uncommitted` reviews changes you have not committed yet; `--no-ai` runs only the automatic checks.

## Development

```bash
pip install -e '.[dev]'
pytest
```
