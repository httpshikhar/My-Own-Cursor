# AI IDE

Terminal-first AI IDE with agentic tools. Provides an interactive REPL, multi-agent planning/execution, and code/run tooling.

## Install

Recommended with pipx:

```bash
pipx install .
```

Or user install:

```bash
pip install --user .
```

## Usage

```bash
ai-ide            # interactive REPL
ai-ide --multi --goal "Build a sample app"
ai-ide --e2e --goal "Do X" --no-commit
```

Set environment for Azure OpenAI before running:

```bash
export AZURE_OAI_ENDPOINT="https://<endpoint>.openai.azure.com/"
export AZURE_OAI_KEY="<key>"
export AZURE_OAI_DEPLOYMENT="<deployment>"
```
