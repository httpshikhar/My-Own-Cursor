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
from rich.syntax import Syntax
from rich import box
import difflib

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
tools = [
    write_file_tool,
    write_files_tool,
    shell_tool,
    edit_file_tool,
    read_file_tool,
    read_files_tool,
    run_python_file_tool,
    run_code_file_tool,
    plan_task_tool,
    generate_project_structure_tool,
]

history = ChatMemory()
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
    planner_response = _chat_create(messages=planner_messages, model=AZURE_OAI_DEPLOYMENT)
    if planner_response is None:
        return
    planner_plan = planner_response.choices[0].message.content or "(No plan produced)"
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
        response = _chat_create(messages=coder_history, tools=tools, tool_choice="auto", model=AZURE_OAI_DEPLOYMENT)
        if response is None:
            console.print("[bold red]Stopping due to connection error.[/bold red]")
            break
        reply = response.choices[0].message
        tool_calls = reply.tool_calls

        if not tool_calls:
            final_content = reply.content or "(No reply)"
            final_notes = final_content
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
            response = _chat_create(messages=reviewer_history, tools=tools, tool_choice="auto", model=AZURE_OAI_DEPLOYMENT)
            if response is None:
                console.print("[bold red]Stopping reviewer due to connection error.[/bold red]")
                break
            reply = response.choices[0].message
            tool_calls = reply.tool_calls

            if not tool_calls:
                review_notes = reply.content or "(No review notes)"
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

# Initial system prompt
history.add(
    "system",
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
""",
)

# Main loop
def run_repl_loop():
    _print_header()
    while True:
        user_input = console.input("[bold cyan]\n💬 You: [/bold cyan]")
        if user_input.strip().lower() in ["exit", "quit"]:
            break

        # Step 1: Add user input to history
        history.add("user", user_input)

        # Step 2: Agent loop (LLM can call tools repeatedly until it's satisfied)
        created_files_session: list[str] = []
        edited_files_session: list[tuple[str, str]] = []
        commands_session: list[str] = []
        final_notes: str | None = None
        for iteration in range(10):  # max 10 steps to prevent infinite loops
            response = client.chat.completions.create(
                model=AZURE_OAI_DEPLOYMENT,
                messages=history.get(),
                tools=tools,
                tool_choice="auto"
            )

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
                    "content": str(result)
                })
        else:
            console.print("[bold red]⚠️ Max iterations reached. Stopping.[/bold red]")
            # Only show the summary if files were created or edited; otherwise show normal AI text (if any)
            has_file_changes = bool(created_files_session or edited_files_session)
            if has_file_changes:
                _render_summary(created_files_session, edited_files_session, commands_session, final_notes)
            elif final_notes:
                console.print(_panel_for("assistant", final_notes))


def main():
    parser = argparse.ArgumentParser(description="AI IDE — Agent REPL / Multi-agent CLI")
    parser.add_argument("--multi", action="store_true", help="Run in multi-agent mode (Planner -> Coder -> Reviewer)")
    parser.add_argument("--goal", type=str, default=None, help="High-level goal for multi-agent mode")
    args = parser.parse_args()

    if args.multi:
        if not args.goal:
            console.print("[bold red]Please provide --goal for multi-agent mode.[/bold red]")
            return
        _print_header()
        run_multi_agent_session(args.goal)
    else:
        run_repl_loop()


if __name__ == "__main__":
    main()