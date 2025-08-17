import os
import json
import argparse
from dotenv import load_dotenv
from openai import AzureOpenAI
from openai import APIConnectionError
import httpx
from tool_registry import get_available_tools
from chat_memory import ChatMemory
from tools.file_tools import *
from tools.shell_tool import *
from tools.code_runner import run_code_file, run_code_file_tool
from tools.planner_tool import plan_task, plan_task_tool
from tools.project_generator import generate_project_structure, generate_project_structure_tool

# Rich terminal UI
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live
from rich.syntax import Syntax
from rich import box
import difflib
from prompt_toolkit.shortcuts import radiolist_dialog, input_dialog
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.styles import Style
from prompt_toolkit.key_binding import KeyBindings
import shutil

# Load secrets
load_dotenv()
AZURE_OAI_ENDPOINT = os.getenv("AZURE_OAI_ENDPOINT")
AZURE_OAI_KEY = os.getenv("AZURE_OAI_KEY")
AZURE_OAI_DEPLOYMENT = os.getenv("AZURE_OAI_DEPLOYMENT")

# Optional TLS/Proxy configuration for corporate environments
AZURE_OAI_CA_BUNDLE = os.getenv("AZURE_OAI_CA_BUNDLE")  # path to custom CA bundle (.pem)
AZURE_OAI_INSECURE = os.getenv("AZURE_OAI_INSECURE", "false").strip().lower() in {"1", "true", "yes", "y"}


def _build_http_client() -> httpx.Client | None:
    """Create an httpx client with optional custom TLS verification.

    - If AZURE_OAI_CA_BUNDLE points to a file, use it to verify TLS.
    - If AZURE_OAI_INSECURE=true, disable TLS verification (NOT recommended).
    - Otherwise, return None to use library defaults.
    """
    try:
        if AZURE_OAI_CA_BUNDLE and os.path.exists(AZURE_OAI_CA_BUNDLE):
            # Also set common envs so any subprocesses use the same CA
            os.environ.setdefault("REQUESTS_CA_BUNDLE", AZURE_OAI_CA_BUNDLE)
            os.environ.setdefault("SSL_CERT_FILE", AZURE_OAI_CA_BUNDLE)
            return httpx.Client(verify=AZURE_OAI_CA_BUNDLE)
        if AZURE_OAI_INSECURE:
            return httpx.Client(verify=False)
    except Exception:
        # Fall back to default client behavior if any issue occurs
        return None
    return None

# Init client and tools
_http_client = _build_http_client()
client = (
    AzureOpenAI(
        azure_endpoint=AZURE_OAI_ENDPOINT,
        api_key=AZURE_OAI_KEY,
        api_version="2024-02-15-preview",
        http_client=_http_client,
    )
    if _http_client is not None
    else AzureOpenAI(
        azure_endpoint=AZURE_OAI_ENDPOINT,
        api_key=AZURE_OAI_KEY,
        api_version="2024-02-15-preview",
    )
)
# Source tools from the registry to keep a single source of truth,
# then append extras not yet registered there.
tools = get_available_tools() + [
    run_code_file_tool,
    plan_task_tool,
    generate_project_structure_tool,
]

history: ChatMemory | None = None
console = Console()


def _panel_for(role: str, content: str) -> Panel:
    role_title = {
        "system": "System",
        "user": "You",
        "assistant": "Assistant",
        "tool": "Tool",
    }.get(role, role.capitalize())

    border = {
        "system": "grey50",
        "user": "cyan",
        "assistant": "magenta",
        "tool": "yellow",
    }.get(role, "white")

    # Render content as Markdown to support code blocks and formatting
    body = Markdown(content or "(empty)")
    return Panel(
        body,
        title=f"[bold]{role_title}[/bold]",
        border_style=border,
        box=box.ROUNDED,
        expand=True,
    )


def _print_header():
    console.rule("[bold cyan]AI IDE — Agent REPL[/bold cyan]")
    console.print(
        "Use 'exit' or 'quit' to leave. Messages render as Markdown with code blocks.",
        style="dim",
    )
    console.print()


def _print_session_info():
    if history is None:
        return
    msgs = history.get()
    total = len(msgs)
    title = f"Session: {history.session_id} ({total} messages)"
    # Show last few non-tool messages for quick recall
    preview = []
    shown = 0
    for m in reversed(msgs):
        if m.get("role") in {"tool"}:
            continue
        preview.append(f"- {m.get('role')}: {str(m.get('content'))[:120].replace('\n',' ')}")
        shown += 1
        if shown >= 5:
            break
    text = "\n".join(reversed(preview)) if preview else "(no prior messages)"
    console.print(Panel(text, title=title, border_style="cyan", box=box.ROUNDED))


def _list_sessions(max_items: int = 50) -> list[str]:
    if history is None:
        return []
    try:
        files = [f for f in os.listdir(history.storage_dir) if f.endswith(".jsonl")]
        ids = [os.path.splitext(f)[0] for f in files]
        return sorted(ids, reverse=True)[:max_items]
    except Exception:
        return []


def _start_new_session(session_id: str | None = None):
    global history
    sessions_dir = history.storage_dir if history else ".sessions"
    history = ChatMemory.new_session(session_id=session_id, storage_dir=sessions_dir)
    console.print(Panel(f"Started new session: {history.session_id}", title="Session", border_style="cyan", box=box.ROUNDED))
    # Add system prompt to fresh session
    history.add("system", SYSTEM_PROMPT)


def _switch_session(session_id: str):
    global history
    if history is None:
        return
    sessions_dir = history.storage_dir
    history = ChatMemory.load(session_id, storage_dir=sessions_dir)
    console.print(Panel(f"Switched to session: {history.session_id}", title="Session", border_style="cyan", box=box.ROUNDED))
    _print_session_info()


def _show_context(n: int = 10):
    if history is None:
        return
    msgs = [m for m in history.get() if m.get("role") != "tool"]
    tail = msgs[-n:]
    lines = [f"- {m['role']}: {str(m['content'])[:200].replace('\n',' ')}" for m in tail]
    console.print(Panel("\n".join(lines) if lines else "(empty)", title=f"Last {n} messages", border_style="cyan", box=box.ROUNDED))


def _handle_slash_command(user_input: str) -> bool:
    """Return True if handled. Implements: /help, /sessions, /session <id>, /new [id], /save, /context [n],
    /read <path>, /runpy <path> [inputs...], /run <path>, /shell <cmd...>, /plan <goal...>
    """
    if not user_input.startswith("/"):
        return False

    # If only '/', do nothing (menu is shown inline via completer). Do not send to LLM.
    if user_input.strip() == "/":
        return True

    parts = user_input.strip().split()
    cmd = parts[0].lower()
    args = parts[1:]

    # Commands
    if cmd in {"/help", "/?"}:
        help_text = (
            "Commands:\n"
            "- /                 Open interactive command palette\n"
            "- /new [id]            Start a new chat session (optional custom id)\n"
            "- /sessions            List available session IDs\n"
            "- /session [id]        Switch to an existing session (picker if omitted)\n"
            "- /save                Flush in-memory messages to disk\n"
            "- /context [n]         Show last n messages (default 10)\n"
            "- /runpy <path> [... ] Run a Python file with optional inputs\n"
            "- /run <path>          Run a code file (.py/.sh/.cpp)\n"
            "- /plan <goal>         Invoke planner tool on a goal\n"
            "\nAttach context with @paths (files/images/folders). Example: 'Summarize @README.md @src/'.\n"
        )
        console.print(Panel(help_text, title="Help", border_style="green", box=box.ROUNDED))
        return True

    if cmd == "/sessions":
        ids = _list_sessions()
        cur = history.session_id if history else "-"
        lines = [f"* {sid} {'(current)' if sid == cur else ''}" for sid in ids] or ["(none)"]
        console.print(Panel("\n".join(lines), title="Sessions", border_style="cyan", box=box.ROUNDED))
        return True

    if cmd == "/new":
        sid = args[0] if args else None
        _start_new_session(sid)
        return True

    if cmd == "/session":
        if not args:
            ids = _list_sessions()
            if not ids:
                console.print(Panel("No sessions found.", title="Sessions", border_style="red", box=box.ROUNDED))
                return True
            chosen = radiolist_dialog(
                title="Switch Session",
                text="Choose a session",
                values=[(sid, sid) for sid in ids],
            ).run()
            if chosen:
                _switch_session(chosen)
            return True
        _switch_session(args[0])
        return True

    if cmd == "/save":
        if history:
            history.save()
            console.print(Panel("Session saved.", title="Session", border_style="green", box=box.ROUNDED))
        return True

    if cmd == "/context":
        n = 10
        if args:
            try:
                n = int(args[0])
            except Exception:
                pass
        _show_context(n)
        return True

    # /read removed (use @path context instead)

    if cmd == "/runpy":
        if not args:
            console.print(Panel("Usage: /runpy <path> [inputs ...]", title="Error", border_style="red", box=box.ROUNDED))
            return True
        path = args[0]
        inputs = args[1:] if len(args) > 1 else None
        res = run_python_file(path=path, inputs=inputs)
        console.print(Panel(res, title=f"Run Python: {path}", border_style="yellow", box=box.ROUNDED))
        return True

    if cmd == "/run":
        if not args:
            console.print(Panel("Usage: /run <path>", title="Error", border_style="red", box=box.ROUNDED))
            return True
        path = args[0]
        res = run_code_file(path=path)
        console.print(Panel(res, title=f"Run: {path}", border_style="yellow", box=box.ROUNDED))
        return True

    # /shell removed

    if cmd == "/e2e":
        if args:
            goal = " ".join(args)
        else:
            goal = input_dialog(title="E2E Orchestrator", text="High-level goal to complete end-to-end").run()
        if goal:
            run_end_to_end_session(goal)
        return True

    if cmd == "/plan":
        if not args:
            console.print(Panel("Usage: /plan <goal>", title="Error", border_style="red", box=box.ROUNDED))
            return True
        goal = " ".join(args)
        _chat_stream(
            messages=[
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": f"Plan the following goal:\n{goal}"},
            ],
            model=AZURE_OAI_DEPLOYMENT,
            panel_title="Planner",
            panel_border_style="green",
        )
        return True

    console.print(Panel(f"Unknown command: {cmd}", title="Error", border_style="red", box=box.ROUNDED))
    return True


def _open_command_palette():
    """Interactive menu for slash commands with arrow-key navigation and simple sub-dialogs."""
    items = [
        ("/new", "Start new session"),
        ("/sessions", "List sessions"),
        ("/session", "Switch session"),
        ("/save", "Save session to disk"),
        ("/context", "Show last N messages"),
        ("/runpy", "Run a Python file with inputs"),
        ("/run", "Run a code file (.py/.sh/.cpp)"),
        ("/e2e", "Plan → Execute → Validate → Commit"),
        ("/plan", "Plan a goal"),
        ("/help", "Show help"),
    ]

    result = radiolist_dialog(
        title="Command Palette",
        text="Select a command",
        values=[(k, f"{k} – {label}") for k, label in items],
    ).run()

    if not result:
        return

    # Sub-dialogs / options per command
    if result == "/new":
        sid = input_dialog(title="New Session", text="Optional session id (leave blank for auto):").run()
        _start_new_session(sid if sid else None)
        return

    if result == "/sessions":
        ids = _list_sessions()
        lines = [f"* {sid} {'(current)' if history and sid == history.session_id else ''}" for sid in ids] or ["(none)"]
        console.print(Panel("\n".join(lines), title="Sessions", border_style="cyan", box=box.ROUNDED))
        return

    if result == "/session":
        ids = _list_sessions()
        if not ids:
            console.print(Panel("No sessions found.", title="Sessions", border_style="red", box=box.ROUNDED))
            return
        chosen = radiolist_dialog(
            title="Switch Session",
            text="Choose a session",
            values=[(sid, sid) for sid in ids],
        ).run()
        if chosen:
            _switch_session(chosen)
        return

    if result == "/save":
        if history:
            history.save()
            console.print(Panel("Session saved.", title="Session", border_style="green", box=box.ROUNDED))
        return

    if result == "/context":
        n_str = input_dialog(title="Context", text="How many messages to show? (default 10)").run()
        try:
            n = int(n_str) if n_str else 10
        except Exception:
            n = 10
        _show_context(n)
        return

    # /read removed

    if result == "/runpy":
        path = input_dialog(title="Run Python", text="Path to Python file:").run()
        if not path:
            return
        inputs_raw = input_dialog(title="Inputs", text="Optional inputs (one line; separate multiple with |):").run()
        inputs = [s.strip() for s in inputs_raw.split("|")] if inputs_raw else None
        res = run_python_file(path=path, inputs=inputs)
        console.print(Panel(res, title=f"Run Python: {path}", border_style="yellow", box=box.ROUNDED))
        return

    if result == "/run":
        path = input_dialog(title="Run Code", text="Path to file (.py/.sh/.cpp):").run()
        if path:
            res = run_code_file(path=path)
            console.print(Panel(res, title=f"Run: {path}", border_style="yellow", box=box.ROUNDED))
        return

    # /shell removed

    if result == "/e2e":
        goal = input_dialog(title="E2E Orchestrator", text="High-level goal to complete end-to-end").run()
        if goal:
            run_end_to_end_session(goal)
        return

    if result == "/plan":
        goal = input_dialog(title="Planner", text="What is your goal?").run()
        if goal:
            _chat_stream(
                messages=[
                    {"role": "system", "content": PLANNER_PROMPT},
                    {"role": "user", "content": f"Plan the following goal:\n{goal}"},
                ],
                model=AZURE_OAI_DEPLOYMENT,
                panel_title="Planner",
                panel_border_style="green",
            )
        return

    if result == "/help":
        _handle_slash_command("/help")
        return


# ----- Slash command dynamic completer -----
COMMAND_SPECS: list[tuple[str, str]] = [
    ("/new", "Start new session"),
    ("/sessions", "List sessions"),
    ("/session", "Switch session"),
    ("/save", "Save session to disk"),
    ("/context", "Show last N messages"),
    ("/runpy", "Run a Python file with inputs"),
    ("/run", "Run a code file (.py/.sh/.cpp)"),
    ("/e2e", "Plan → Execute → Validate → Commit"),
    ("/plan", "Plan a goal"),
    ("/help", "Show help"),
]


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        # 1) Slash command completion only when the entire input starts with '/'
        if text.startswith('/'):
            word = text
            for cmd, desc in COMMAND_SPECS:
                if cmd.startswith(word):
                    display = f"{cmd} — {desc}"
                    yield Completion(cmd, start_position=-len(word), display=display, display_meta=desc)

        # 2) Dynamic @-path completion: complete the current token if it begins with '@'
        # Works anywhere in the line, not just at the start
        # Strategy: identify the last whitespace-delimited token before cursor
        # If it starts with '@', treat the remainder as a filesystem path prefix
        # and listdir on the appropriate base directory.
        # Example inputs:
        #   "Summarize @src/ma" -> completes files/dirs under ./src starting with 'ma'
        #   "@" -> lists entries in cwd
        #   "See @~/proj" -> expands ~ and lists under that dir
        before = text
        # Find the current token (after last whitespace)
        last_space = max(before.rfind(' '), before.rfind('\t'))
        token = before[last_space + 1:] if last_space != -1 else before
        if token.startswith('@') and len(token) >= 1:
            raw_path = token[1:]
            # Handle optional surrounding quotes while typing (basic handling)
            if (raw_path.startswith("'") and not raw_path.endswith("'")) or (
                raw_path.startswith('"') and not raw_path.endswith('"')
            ):
                # Don't attempt to complete until closing quote; fall back to no results
                return

            # Strip balanced quotes
            if (raw_path.startswith("'") and raw_path.endswith("'")) or (
                raw_path.startswith('"') and raw_path.endswith('"')
            ):
                raw_path = raw_path[1:-1]

            # Expand ~ and environment variables
            expanded = os.path.expanduser(os.path.expandvars(raw_path)) if raw_path else ''

            # Determine base directory and prefix to match
            if expanded and (os.sep in expanded or (os.altsep and os.altsep in expanded)):
                base_dir = os.path.dirname(expanded) or os.curdir
                prefix = os.path.basename(expanded)
            else:
                base_dir = expanded or os.curdir
                prefix = ''

            try:
                entries = []
                if os.path.isdir(base_dir):
                    for name in os.listdir(base_dir):
                        if prefix and not name.startswith(prefix):
                            continue
                        full_path = os.path.join(base_dir, name)
                        is_dir = os.path.isdir(full_path)
                        # Build the suggestion path relative to what user typed
                        # Reconstruct suggestion keeping the original raw prefix
                        # If user typed something like "@src/ma", we suggest "@src/main.py" etc.
                        if expanded:
                            # Compute path segment to append after the base_dir
                            after_base = name
                            # If base_dir is '.' but user provided some raw_path, keep that raw parent
                            if os.path.normpath(base_dir) != os.curdir:
                                suggested_raw = os.path.join(raw_path[: max(0, len(raw_path) - len(prefix))], after_base)
                            else:
                                suggested_raw = os.path.join(raw_path[: max(0, len(raw_path) - len(prefix))], after_base)
                        else:
                            suggested_raw = name

                        # Append path separator for directories to ease further navigation
                        display_name = suggested_raw + (os.sep if is_dir else '')
                        completion_text = '@' + display_name
                        meta = 'dir' if is_dir else 'file'
                        # Replace only the current token
                        yield Completion(
                            completion_text,
                            start_position=-len(token),
                            display=display_name,
                            display_meta=meta,
                        )
                # If base_dir doesn't exist, yield nothing
            except Exception:
                # Silently ignore completion errors
                return


def _term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 20)).columns
    except Exception:
        return default


def _input_prompt_box_top(title: str = "You") -> HTML:
    # Dynamic top border sized to terminal width (leaving margin for prompt symbol and spacing)
    width = max(20, _term_width())
    label = f" {title} "
    # Use light box drawing characters
    # ┌── title ───────────────────────────────┐
    inner = max(0, width - 2 - len(label))
    line = f"┌{label}{'─'*inner}┐\n│ "
    return HTML(line)


def _input_prompt_left() -> HTML:
    # Left border for subsequent input lines (in case of multiline usage later)
    return HTML("│ ")


def _bottom_toolbar():
    # Bottom border line and tips
    width = max(20, _term_width())
    line = f"└{'─'*(width-2)}┘"
    tips = "  Tips: Type '/' for commands • @ to attach files • Enter to send • 'exit' to quit"
    return HTML(f"<ansicyan>{line}</ansicyan><b>{tips}</b>")


# ----- @-context attachment helpers -----
TEXT_EXTS = {
    ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".xml", ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".csv", ".env",
}


def _is_text_file(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in TEXT_EXTS


def _extract_at_paths(text: str) -> tuple[str, list[str]]:
    parts = text.split()
    paths: list[str] = []
    cleaned_parts: list[str] = []
    for p in parts:
        if p.startswith("@") and len(p) > 1:
            raw = p[1:]
            # strip surrounding quotes if present
            if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
                raw = raw[1:-1]
            paths.append(raw)
        else:
            cleaned_parts.append(p)
    cleaned = " ".join(cleaned_parts)
    return cleaned, paths


def _expand_paths(paths: list[str], max_files: int = 50) -> list[str]:
    files: list[str] = []
    for p in paths:
        if not p:
            continue
        if os.path.isdir(p):
            for root, _, fnames in os.walk(p):
                for fname in fnames:
                    fp = os.path.join(root, fname)
                    files.append(fp)
                    if len(files) >= max_files:
                        return files
        elif os.path.isfile(p):
            files.append(p)
    return files


def _build_context_tool_message(files: list[str], char_limit: int = 80000) -> dict:
    lines: list[str] = []
    used = 0
    for fpath in files:
        try:
            if _is_text_file(fpath):
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                header = f"=== {fpath} (text) ==="
                budget = max(0, char_limit - used - len(header) - 1)
                snippet = content if len(content) <= budget else content[:budget] + "\n…[truncated]"
                lines.append(header)
                lines.append(snippet)
                used += len(header) + 1 + len(snippet)
                if used >= char_limit:
                    lines.append("…[context size limit reached]")
                    break
            else:
                size = os.path.getsize(fpath)
                lines.append(f"=== {fpath} (binary {size} bytes) ===")
        except Exception as e:
            lines.append(f"=== {fpath} (error: {e}) ===")
    content = "\n\n".join(lines) if lines else "(no context)"
    return {"role": "tool", "name": "attach_context", "content": content}


def log_created_files(paths: list[str], created_accumulator: list[str] | None = None):
    if not paths:
        return
    message = "\n".join(paths)
    console.print(Panel(message, title="Created files", border_style="green", box=box.ROUNDED))
    if created_accumulator is not None:
        created_accumulator.extend(paths)


def log_edited_file(path: str, mode: str, edited_accumulator: list[tuple[str, str]] | None = None):
    console.print(Panel(f"{path} (mode: {mode})", title="Edited file", border_style="blue", box=box.ROUNDED))
    if edited_accumulator is not None:
        edited_accumulator.append((path, mode))


def log_command_run(command: str, commands_accumulator: list[str] | None = None):
    console.print(Panel(f"$ {command}", title="Ran command", border_style="cyan", box=box.ROUNDED))
    if commands_accumulator is not None:
        commands_accumulator.append(command)


def _read_file_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def render_edit_diff(path: str, before_text: str | None, after_text: str | None):
    if before_text is None or after_text is None:
        return
    diff_lines = list(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if not diff_lines:
        return
    diff_text = "\n".join(diff_lines)
    console.print(
        Panel(
            Syntax(diff_text, "diff", theme="ansi_dark"),
            title=f"Changes: {path}",
            border_style="blue",
            box=box.ROUNDED,
        )
    )


def _render_summary(created_files: list[str], edited_files: list[tuple[str, str]], ran_commands: list[str], assistant_notes: str | None):
    parts: list[str] = []

    # Changes
    parts.append("### Changes made")
    if created_files:
        parts.append("- Created:")
        parts.extend([f"  - {p}" for p in created_files])
    if edited_files:
        parts.append("- Edited:")
        parts.extend([f"  - {p} (mode: {m})" for p, m in edited_files])
    if not created_files and not edited_files:
        parts.append("- No file changes recorded.")

    # Commands
    parts.append("\n### Commands run")
    if ran_commands:
        parts.extend([f"- $ {c}" for c in ran_commands])
    else:
        parts.append("- None")

    # How to run
    parts.append("\n### How to run")
    parts.append("- Activate env: `source Python/.venv/bin/activate`")
    parts.append("- Start agent UI: `python \"Python/main.py\"`")
    py_created = [p for p in created_files if p.endswith(".py")]
    if py_created:
        # Suggest running notable python files (e.g. main.py) directly
        notable = [p for p in py_created if os.path.basename(p) in {"main.py", "app.py"}] or py_created[:2]
        for path in notable:
            parts.append(f"- Run script: `python \"{path}\"`")

    # Suggestions / next steps
    parts.append("\n### Suggestions")
    if assistant_notes:
        parts.append("- Review assistant notes below for next actions.")
    parts.append("- Review created/edited files and run them as needed.")
    parts.append("- Add tests and a README if missing.")
    parts.append("- Commit your changes when satisfied.")

    summary_md = "\n".join(parts)
    console.print(Panel(Markdown(summary_md), title="Session summary", border_style="magenta", box=box.ROUNDED))
    if assistant_notes:
        console.print(Panel(Markdown(assistant_notes), title="Assistant notes", border_style="magenta", box=box.ROUNDED))


# ===== Multi-agent mode support =====
# Safe wrapper around chat.completions.create to handle TLS/proxy issues
def _chat_create(*, messages, tools=None, tool_choice=None, model: str = AZURE_OAI_DEPLOYMENT):
    try:
        return client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
    except APIConnectionError as err:
        help_text = (
            "Connection to Azure OpenAI failed. If you are behind a proxy or corporate CA, set "
            "`AZURE_OAI_CA_BUNDLE` to your PEM file, or set `AZURE_OAI_INSECURE=true` to skip verification (not recommended).\n"
            "Alternatives: set `REQUESTS_CA_BUNDLE` or `SSL_CERT_FILE` env vars.\n"
            f"Original error: {err}"
        )
        console.print(Panel(help_text, title="Connection error", border_style="red", box=box.ROUNDED))
        return None
    except Exception as err:
        console.print(Panel(str(err), title="Unexpected error", border_style="red", box=box.ROUNDED))
        return None


def _chat_stream(
    *,
    messages,
    tools=None,
    tool_choice=None,
    model: str = AZURE_OAI_DEPLOYMENT,
    panel_title: str | None = None,
    panel_border_style: str | None = None,
):
    """Stream assistant output with a spinner. Returns (response, rendered_bool) or (None, False) on error.

    rendered_bool indicates whether we already rendered assistant content (so caller should not re-print it).
    """
    try:
        # Establish the stream
        stream_ctx = client.chat.completions.stream(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        accumulated_text = ""
        rendered_any_content = False
        placeholder = "…"
        with stream_ctx as stream:
            # Keep spinner visible until first content token arrives (or completion ends)
            with console.status("Assistant is thinking...", spinner="dots"):
                for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "message.delta":
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            continue
                        content = getattr(delta, "content", None)
                        if isinstance(content, str):
                            accumulated_text += content
                            rendered_any_content = rendered_any_content or bool(content)
                        elif isinstance(content, list):
                            for part in content:
                                text = ""
                                if isinstance(part, dict):
                                    text = part.get("text") or part.get("content") or ""
                                elif isinstance(part, str):
                                    text = part
                                accumulated_text += text
                                rendered_any_content = rendered_any_content or bool(text)
                        # We got first visible content: break to start live panel
                        if rendered_any_content:
                            break
                    elif event_type == "message.completed":
                        # No content is expected; stop spinner and proceed to finalize
                        break
                    else:
                        # Ignore other event types while waiting
                        pass

            # Start live panel (spinner has just stopped)
            with Live(
                Panel(
                    Markdown(accumulated_text or placeholder),
                    title=(panel_title or "Assistant"),
                    border_style=(panel_border_style or "magenta"),
                    box=box.ROUNDED,
                ),
                refresh_per_second=12,
                console=console,
                transient=True,
            ) as live:
                for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "message.delta":
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            continue
                        content = getattr(delta, "content", None)
                        if isinstance(content, str):
                            accumulated_text += content
                            rendered_any_content = rendered_any_content or bool(content)
                        elif isinstance(content, list):
                            for part in content:
                                text = ""
                                if isinstance(part, dict):
                                    text = part.get("text") or part.get("content") or ""
                                elif isinstance(part, str):
                                    text = part
                                accumulated_text += text
                                rendered_any_content = rendered_any_content or bool(text)
                        live.update(
                            Panel(
                                Markdown(accumulated_text or placeholder),
                                title=(panel_title or "Assistant"),
                                border_style=(panel_border_style or "magenta"),
                                box=box.ROUNDED,
                            )
                        )
                    elif event_type == "tool_calls.delta":
                        # Not user-facing; ignore
                        pass
                    elif event_type == "message.completed":
                        # Final message assembled
                        pass
                final_response = stream.get_final_response()

        # After the live context exits, persist output only if we actually rendered content
        if rendered_any_content:
            console.print(
                Panel(
                    Markdown(accumulated_text),
                    title=(panel_title or "Assistant"),
                    border_style=(panel_border_style or "magenta"),
                    box=box.ROUNDED,
                )
            )
        return final_response, rendered_any_content
    except APIConnectionError as err:
        help_text = (
            "Connection to Azure OpenAI failed. If you are behind a proxy or corporate CA, set "
            "`AZURE_OAI_CA_BUNDLE` to your PEM file, or set `AZURE_OAI_INSECURE=true` to skip verification (not recommended).\n"
            "Alternatives: set `REQUESTS_CA_BUNDLE` or `SSL_CERT_FILE` env vars.\n"
            f"Original error: {err}"
        )
        console.print(Panel(help_text, title="Connection error", border_style="red", box=box.ROUNDED))
        return None, False
    except Exception:
        # If streaming is not supported or any error occurs, fall back to non-streaming
        response = _chat_create(messages=messages, tools=tools, tool_choice=tool_choice, model=model)
        return response, False
# Prompts for different specialized agents
PLANNER_PROMPT = (
    "You are the Planner agent. Your job is to break the user's goal into a concise, actionable plan of 3-10 steps focused on coding tasks. "
    "Prefer concrete file operations and commands. Do not execute, only plan."
)

CODER_PROMPT = (
    "You are the Coder agent. Your job is to implement the plan end-to-end using available tools. "
    "Favor creating files, editing code, running code, and shell commands when needed. "
    "Think step-by-step and verify outputs. Keep going until the goal is complete."
)

REVIEWER_PROMPT = (
    "You are the Reviewer agent. Review the recent code changes for correctness, style, and potential issues. "
    "Suggest improvements and, if safe and unambiguous, apply small fixes using edit tools. Keep feedback concise and practical."
)


def run_multi_agent_session(user_goal: str):
    """Run a Planner -> Coder (with tools) -> Reviewer (optional fixes) pipeline."""
    # ---------- Planner ----------
    planner_messages = [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": f"Plan the following goal:\n{user_goal}"},
    ]
    planner_response, rendered_stream = _chat_stream(
        messages=planner_messages,
        model=AZURE_OAI_DEPLOYMENT,
        panel_title="Planner",
        panel_border_style="green",
    )
    if planner_response is None:
        return
    planner_plan = planner_response.choices[0].message.content or "(No plan produced)"
    if not rendered_stream:
        console.print(Panel(Markdown(planner_plan), title="Planner", border_style="green", box=box.ROUNDED))

    # ---------- Coder (tool-enabled loop) ----------
    created_files_session: list[str] = []
    edited_files_session: list[tuple[str, str]] = []
    commands_session: list[str] = []
    final_notes: str | None = None

    coder_history = [
        {"role": "system", "content": CODER_PROMPT},
        {"role": "user", "content": f"Goal:\n{user_goal}"},
        {"role": "assistant", "content": f"Plan:\n{planner_plan}"},
    ]

    for _ in range(15):
        response, rendered_stream = _chat_stream(
            messages=coder_history,
            tools=tools,
            tool_choice="auto",
            model=AZURE_OAI_DEPLOYMENT,
            panel_title="Coder",
            panel_border_style="magenta",
        )
        if response is None:
            console.print("[bold red]Stopping due to connection error.[/bold red]")
            break
        reply = response.choices[0].message
        tool_calls = reply.tool_calls

        if not tool_calls:
            final_content = reply.content or "(No reply)"
            final_notes = final_content
            if not rendered_stream:
                console.print(Panel(Markdown(final_content), title="Coder", border_style="magenta", box=box.ROUNDED))
            break

        # Record assistant message with tool calls
        coder_history.append({
            "role": "assistant",
            "content": reply.content,
            "tool_calls": [tc.model_dump() for tc in tool_calls],
        })

        # Execute tool calls one by one
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if tool_name == "write_file":
                result = write_file(**args)
                path = args.get("path")
                if path:
                    log_created_files([path], created_files_session)

            elif tool_name == "write_files":
                result = write_files(**args)
                files = args.get("files") or []
                paths = [f.get("path") for f in files if f.get("path")]
                log_created_files(paths, created_files_session)

            elif tool_name == "run_shell_command":
                command = args.get("command")
                user_input_val = args.get("user_input")
                result = run_shell_command(command=command, user_input=user_input_val)
                if command:
                    log_command_run(command, commands_session)

            elif tool_name == "edit_file":
                path = args.get("path")
                before_text = _read_file_text(path) if path else None
                result = edit_file(**args)
                after_text = _read_file_text(path) if path else None
                mode = args.get("mode", "append")
                if path:
                    log_edited_file(path, mode, edited_files_session)
                    render_edit_diff(path, before_text, after_text)

            elif tool_name == "read_file":
                result = read_file(**args)

            elif tool_name == "read_files":
                result = read_files(**args)

            elif tool_name == "run_python_file":
                result = run_python_file(**args)
                path = args.get("path")
                if path:
                    log_command_run(f"python3 {path}", commands_session)

            elif tool_name == "run_code_file":
                result = run_code_file(**args)
                path = args.get("path")
                if path:
                    log_command_run(f"run_code_file {path}", commands_session)

            elif tool_name == "generate_project_structure":
                result = generate_project_structure(**args)
                folder = args.get("folder_name", "")
                files = args.get("files") or []
                paths = [os.path.join(folder, f.get("path")) for f in files if f.get("path")]
                log_created_files(paths, created_files_session)
            else:
                result = f"⚠️ Unknown tool: {tool_name}"

            # Feed tool result back into coder history
            coder_history.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result),
            })
    else:
        console.print("[bold red]⚠️ Coder: Max iterations reached. Stopping.[/bold red]")

    # ---------- Reviewer (optional fixes) ----------
    has_file_changes = bool(created_files_session or edited_files_session)
    review_notes: str | None = None
    if has_file_changes:
        # Build compact context of changed files
        parts: list[str] = []
        for path in created_files_session:
            content = _read_file_text(path) or "(empty or unreadable)"
            parts.append(f"### Created: {path}\n\n```\n{content}\n```\n")
        for path, _mode in edited_files_session:
            content = _read_file_text(path) or "(empty or unreadable)"
            parts.append(f"### Edited: {path}\n\n```\n{content}\n```\n")
        changed_context = "\n\n".join(parts)

        reviewer_history = [
            {"role": "system", "content": REVIEWER_PROMPT},
            {"role": "user", "content": (
                "Please review the following changes for correctness, potential bugs, and improvements. "
                "If trivial fixes are needed, you may apply them using the edit tools.\n\n" + changed_context
            )},
        ]

        for _ in range(8):
            response, rendered_stream = _chat_stream(
                messages=reviewer_history,
                tools=tools,
                tool_choice="auto",
                model=AZURE_OAI_DEPLOYMENT,
                panel_title="Reviewer",
                panel_border_style="yellow",
            )
            if response is None:
                console.print("[bold red]Stopping reviewer due to connection error.[/bold red]")
                break
            reply = response.choices[0].message
            tool_calls = reply.tool_calls

            if not tool_calls:
                review_notes = reply.content or "(No review notes)"
                if not rendered_stream:
                    console.print(Panel(Markdown(review_notes), title="Reviewer", border_style="yellow", box=box.ROUNDED))
                break

            reviewer_history.append({
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [tc.model_dump() for tc in tool_calls],
            })

            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                if tool_name == "edit_file":
                    path = args.get("path")
                    before_text = _read_file_text(path) if path else None
                    result = edit_file(**args)
                    after_text = _read_file_text(path) if path else None
                    mode = args.get("mode", "append")
                    if path:
                        log_edited_file(path, mode, edited_files_session)
                        render_edit_diff(path, before_text, after_text)
                elif tool_name == "write_file":
                    result = write_file(**args)
                    path = args.get("path")
                    if path:
                        log_created_files([path], created_files_session)
                elif tool_name == "write_files":
                    result = write_files(**args)
                    files = args.get("files") or []
                    paths = [f.get("path") for f in files if f.get("path")]
                    log_created_files(paths, created_files_session)
                elif tool_name == "read_file":
                    result = read_file(**args)
                elif tool_name == "read_files":
                    result = read_files(**args)
                elif tool_name == "run_shell_command":
                    command = args.get("command")
                    user_input_val = args.get("user_input")
                    result = run_shell_command(command=command, user_input=user_input_val)
                    if command:
                        log_command_run(command, commands_session)
                elif tool_name == "run_python_file":
                    result = run_python_file(**args)
                    path = args.get("path")
                    if path:
                        log_command_run(f"python3 {path}", commands_session)
                elif tool_name == "run_code_file":
                    result = run_code_file(**args)
                    path = args.get("path")
                    if path:
                        log_command_run(f"run_code_file {path}", commands_session)
                elif tool_name == "generate_project_structure":
                    result = generate_project_structure(**args)
                    folder = args.get("folder_name", "")
                    files = args.get("files") or []
                    paths = [os.path.join(folder, f.get("path")) for f in files if f.get("path")]
                    log_created_files(paths, created_files_session)
                else:
                    result = f"⚠️ Unknown tool: {tool_name}"

                reviewer_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result),
                })
        else:
            console.print("[bold red]⚠️ Reviewer: Max iterations reached. Stopping.[/bold red]")

    # ---------- Final summary ----------
    has_file_changes = bool(created_files_session or edited_files_session)
    if has_file_changes:
        _render_summary(created_files_session, edited_files_session, commands_session, None)
    else:
        # No file changes; nothing to summarize
        pass

# Initial system prompt (added lazily when starting a fresh session)
SYSTEM_PROMPT = (
    """
You are an AI Software Engineer working inside an AI-powered IDE.

Your job is to take high-level goals from the user (e.g., "Build a CLI todo app") and fully complete them using the available tools.

You are not just a code generator — you think, plan, verify, fix, and iterate to accomplish the goal end-to-end.

## Responsibilities

1. **Understand the Goal**  
   Carefully read the user's message to understand the desired outcome.

2. **Plan the Task**  
   Use the `plan_task` tool to break the goal into smaller actionable steps (like "write main.py", "define CLI", etc.)

3. **Generate Project Structure**  
   If a project folder needs to be created with multiple files, use the `generate_project_structure` tool.

4. **Execute Step-by-Step**  
   For individual tasks, use `write_file`, `read_file`, `edit_file`, `run_code_file`, and `run_shell_command`.

5. **Verify and Self-Debug**  
   Always run generated code; debug and iterate on failures.

6. **Stay Autonomous**  
   Don’t ask for permission at every step; only ask if user input is required.

7. **Suggest the Next Move**  
   After each step, clearly suggest the next logical move to complete the goal.
"""
)

# Main loop
def run_repl_loop():
    _print_header()
    # Add system prompt only if starting fresh (no prior messages).
    if history is not None and len(history.get()) == 0:
        history.add("system", SYSTEM_PROMPT)
    _print_session_info()
    # Configure styles and key bindings
    style = Style.from_dict({
        # These affect general UI elements when using HTML tags
        "prompt": "ansicyan bold",
        "bottom-toolbar": "noreverse",
    })

    kb = KeyBindings()

    @kb.add('c-l')
    def _(event):
        """Clear the screen."""
        console.clear()

    @kb.add('c-k')
    def _(event):
        """Clear current input buffer."""
        b = event.app.current_buffer
        b.document = b.document.delete_before_cursor(count=b.cursor_position)

    # Shift+Tab: Quick switch to Planner mode
    @kb.add('s-tab')
    def _(event):
        """Open a quick Planner dialog to generate a plan for a goal."""
        def _show_dialog():
            try:
                # Use Rich console input to avoid nested prompt_toolkit application issues
                console.print(Panel("Enter goal for Planner:", title="Planner (Shift+Tab)", border_style="green", box=box.ROUNDED))
                goal = console.input("[bold cyan]> [/bold cyan]")
                if goal and goal.strip():
                    _chat_stream(
                        messages=[
                            {"role": "system", "content": PLANNER_PROMPT},
                            {"role": "user", "content": f"Plan the following goal:\n{goal.strip()}"},
                        ],
                        model=AZURE_OAI_DEPLOYMENT,
                        panel_title="Planner",
                        panel_border_style="green",
                    )
            except Exception as e:
                console.print(Panel(str(e), title="Planner error", border_style="red", box=box.ROUNDED))

        # Run outside the current prompt_toolkit application to avoid nested UI errors
        run_in_terminal(_show_dialog)

    # PromptSession with dynamic slash and @-path completion and a framed input prompt
    session = PromptSession(
        completer=SlashCompleter(),
        bottom_toolbar=_bottom_toolbar,
        style=style,
        key_bindings=kb,
        prompt_continuation=lambda width, line_number, is_soft_wrap: _input_prompt_left(),
    )
    while True:
        try:
            with patch_stdout():
                # Render a dynamic top border above the input each time
                user_input = session.prompt(
                    _input_prompt_box_top("You"),
                    complete_while_typing=True,
                    multiline=False,
                )
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in ["exit", "quit"]:
            break

        # Step 1: Add user input to history
        # Slash commands are handled locally and are NOT sent to the model
        if _handle_slash_command(user_input):
            # Already handled; do not send to LLM or record as normal message
            continue

        # Otherwise, record in session history and proceed
        # Extract @paths and merge their content into the user message (avoid 'tool' role misuse)
        cleaned_input, at_paths = _extract_at_paths(user_input)
        merged_user_content = cleaned_input if cleaned_input else user_input
        if at_paths:
            files = _expand_paths(at_paths)
            if files:
                ctx_msg = _build_context_tool_message(files)
                ctx_content = ctx_msg.get("content", "")
                # Append the context in a clear section below the user's text
                if merged_user_content:
                    merged_user_content = f"{merged_user_content}\n\n[Attached context]\n{ctx_content}"
                else:
                    merged_user_content = f"[Attached context]\n{ctx_content}"
        history.add("user", merged_user_content)

        # Step 2: Agent loop (LLM can call tools repeatedly until it's satisfied)
        created_files_session: list[str] = []
        edited_files_session: list[tuple[str, str]] = []
        commands_session: list[str] = []
        final_notes: str | None = None
        for iteration in range(10):  # max 10 steps to prevent infinite loops
            response, rendered_stream = _chat_stream(
                messages=history.get(), tools=tools, tool_choice="auto", model=AZURE_OAI_DEPLOYMENT
            )
            if response is None:
                console.print("[bold red]Stopping due to connection error.[/bold red]")
                break

            reply = response.choices[0].message
            tool_calls = reply.tool_calls

            # Step 3: No tool call — LLM is done
            if not tool_calls:
                final_content = reply.content or "(No reply)"
                final_notes = final_content
                history.add("assistant", final_content)
                # Only show the summary if files were created or edited; otherwise show normal AI text
                has_file_changes = bool(created_files_session or edited_files_session)
                if has_file_changes:
                    _render_summary(created_files_session, edited_files_session, commands_session, final_notes)
                else:
                    # If we already rendered the streamed content, do not re-print
                    if not rendered_stream:
                        console.print(_panel_for("assistant", final_content))
                break

            # Step 4: Tool calls present, execute them
            history.messages.append({
                "role": "assistant",
                "content": reply.content,  # may be None, that’s fine
                "tool_calls": [tool_call.model_dump() for tool_call in reply.tool_calls]
            })
            
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                # Match the tool name and run accordingly
                if tool_name == "write_file":
                    result = write_file(**args)
                    # Minimal UI: show created file
                    path = args.get("path")
                    if path:
                        log_created_files([path], created_files_session)

                elif tool_name == "write_files":
                    result = write_files(**args)
                    # Minimal UI: show all created files (by intent)
                    files = args.get("files") or []
                    paths = [f.get("path") for f in files if f.get("path")]
                    log_created_files(paths, created_files_session)

                elif tool_name == "run_shell_command":
                    command = args.get("command")
                    user_input_val = args.get("user_input")  # optional
                    result = run_shell_command(command=command, user_input=user_input_val)
                    if command:
                        log_command_run(command, commands_session)

                elif tool_name == "edit_file":
                    # Capture before/after to render a diff
                    path = args.get("path")
                    before_text = _read_file_text(path) if path else None
                    result = edit_file(**args)
                    after_text = _read_file_text(path) if path else None
                    mode = args.get("mode", "append")
                    if path:
                        log_edited_file(path, mode, edited_files_session)
                        render_edit_diff(path, before_text, after_text)

                elif tool_name == "read_file":
                    result = read_file(**args)
                    # Minimal UI: do not show reads

                elif tool_name == "read_files":
                    result = read_files(**args)
                    # Minimal UI: do not show reads
                
                elif tool_name == "run_python_file":
                    result = run_python_file(**args)
                    path = args.get("path")
                    if path:
                        log_command_run(f"python3 {path}", commands_session)
                
                elif tool_name == "run_code_file":
                    result = run_code_file(**args)
                    path = args.get("path")
                    if path:
                        log_command_run(f"run_code_file {path}", commands_session)

                elif tool_name == "generate_project_structure":
                    result = generate_project_structure(**args)
                    folder = args.get("folder_name", "")
                    files = args.get("files") or []
                    paths = [os.path.join(folder, f.get("path")) for f in files if f.get("path")]
                    log_created_files(paths, created_files_session)
                else:
                    result = f"⚠️ Unknown tool: {tool_name}"

                # Step 5: Add tool result back to chat history
                history.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result) if result is not None else ""
                })


def _run_validation_steps() -> tuple[bool, dict[str, str]]:
    """Run formatter and tests. Returns (all_ok, outputs)."""
    outputs: dict[str, str] = {}
    all_ok = True

    # Black
    cmd = "black ."
    res = run_shell_command(cmd)
    log_command_run(cmd)
    outputs["black"] = res or ""
    console.print(Panel(outputs["black"], title="Formatter: black", border_style="yellow", box=box.ROUNDED))

    # Ruff
    cmd = "ruff check . --fix"
    res = run_shell_command(cmd)
    log_command_run(cmd)
    outputs["ruff"] = res or ""
    console.print(Panel(outputs["ruff"], title="Linter: ruff", border_style="yellow", box=box.ROUNDED))

    # Pytest
    cmd = "pytest -q"
    res = run_shell_command(cmd)
    log_command_run(cmd)
    outputs["pytest"] = res or ""
    lower = outputs["pytest"].lower()
    if outputs["pytest"].startswith("❌ Error:") and ("no tests ran" not in lower and "not found" not in lower):
        all_ok = False
        console.print(Panel(outputs["pytest"], title="Tests failed", border_style="red", box=box.ROUNDED))
    else:
        console.print(Panel(outputs["pytest"], title="Tests", border_style="green", box=box.ROUNDED))

    return all_ok, outputs


def _git_commit_changes(commit_message: str | None = None):
    """Stage and commit any changes. Initializes git repo if needed."""
    check = run_shell_command("git rev-parse --is-inside-work-tree")
    if "true" not in (check or "").lower():
        init_out = run_shell_command("git init")
        log_command_run("git init")
        console.print(Panel(init_out, title="git init", border_style="cyan", box=box.ROUNDED))

    add_out = run_shell_command("git add -A")
    log_command_run("git add -A")
    console.print(Panel(add_out, title="git add -A", border_style="cyan", box=box.ROUNDED))

    msg = commit_message or "chore: automated E2E commit from AI IDE"
    commit_cmd = f"git commit -m {json.dumps(msg)}"
    commit_out = run_shell_command(commit_cmd)
    log_command_run(commit_cmd)
    style = "green" if not (commit_out or "").startswith("❌ Error:") else "red"
    console.print(Panel(commit_out, title="git commit", border_style=style, box=box.ROUNDED))


def run_end_to_end_session(goal: str, *, commit: bool = True, commit_message: str | None = None, max_retries: int = 1):
    """Plan → Execute → Validate → optional Commit with a retry on failures."""
    console.rule("[bold green]E2E Orchestration[/bold green]")
    run_multi_agent_session(goal)

    ok, outputs = _run_validation_steps()
    retries = 0
    while not ok and retries < max_retries:
        retries += 1
        console.print(Panel(f"Retry {retries}/{max_retries}: attempting to fix test failures automatically.", title="Retry", border_style="red", box=box.ROUNDED))
        failure_context = outputs.get("pytest", "")
        retry_goal = (
            f"{goal}\n\nThen fix the following test/validation failures and rerun validations until green (one attempt):\n{failure_context}"
        )
        run_multi_agent_session(retry_goal)
        ok, outputs = _run_validation_steps()

    if commit:
        _git_commit_changes(commit_message)

    status = "All validations passed" if ok else "Validations still failing"
    border = "green" if ok else "red"
    console.print(Panel(status, title="E2E Result", border_style=border, box=box.ROUNDED))


def main():
    parser = argparse.ArgumentParser(description="AI IDE — Agent REPL / Multi-agent CLI")
    parser.add_argument("--multi", action="store_true", help="Run in multi-agent mode (Planner -> Coder -> Reviewer)")
    parser.add_argument("--goal", type=str, default=None, help="High-level goal for multi-agent mode")
    parser.add_argument("--session", type=str, default=None, help="Resume a specific session ID (from .sessions)")
    parser.add_argument("--new-session", action="store_true", help="Start a brand new session with a fresh ID")
    parser.add_argument("--sessions-dir", type=str, default=".sessions", help="Directory to store session JSONL files")
    parser.add_argument("--e2e", action="store_true", help="Run the end-to-end orchestrator (plan → execute → validate → commit)")
    parser.add_argument("--no-commit", action="store_true", help="Do not commit changes at the end of e2e run")
    parser.add_argument("--commit-message", type=str, default=None, help="Custom git commit message for e2e mode")
    args = parser.parse_args()

    # Initialize chat memory session
    global history
    if args.new_session:
        history = ChatMemory.new_session(storage_dir=args.sessions_dir)
    elif args.session:
        history = ChatMemory.load(args.session, storage_dir=args.sessions_dir)
    else:
        history = ChatMemory(storage_dir=args.sessions_dir)

    if args.e2e:
        if not args.goal:
            console.print("[bold red]Please provide --goal for e2e mode.[/bold red]")
            return
        _print_header()
        run_end_to_end_session(
            args.goal,
            commit=not args.no_commit,
            commit_message=args.commit_message,
            max_retries=1,
        )
    elif args.multi:
        if not args.goal:
            console.print("[bold red]Please provide --goal for multi-agent mode.[/bold red]")
            return
        _print_header()
        run_multi_agent_session(args.goal)
    else:
        run_repl_loop()


if __name__ == "__main__":
    main()