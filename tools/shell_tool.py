import subprocess
import threading
import queue

# This will store interactive state (in-memory for now)
_interactive_process = {
    "process": None,
    "stdin_queue": None,
    "command": None
}

def run_shell_command(command: str, user_input: str = None) -> str:
    global _interactive_process

    # If continuing a paused command (waiting for user input)
    if user_input and _interactive_process["process"]:
        _interactive_process["stdin_queue"].put(user_input + "\n")
        return "📝 Input received. Resuming..."

    try:
        # Start the process
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        _interactive_process["process"] = process
        _interactive_process["command"] = command
        _interactive_process["stdin_queue"] = queue.Queue()

        output = []
        waiting_for_input = False

        def write_to_stdin():
            while True:
                user_input = _interactive_process["stdin_queue"].get()
                if user_input is None:
                    break
                try:
                    process.stdin.write(user_input)
                    process.stdin.flush()
                except:
                    break

        # Start input writer thread
        writer_thread = threading.Thread(target=write_to_stdin, daemon=True)
        writer_thread.start()

        for line in process.stdout:
            output.append(line)
            print("🟢", line.strip())  # Real-time feedback

            # Detect common interactive prompts
            if any(prompt in line.lower() for prompt in ["[y/n]", "[y/n]", "press enter", "do you want", "continue?"]):
                waiting_for_input = True
                process.stdout.close()
                return (
                    f"⏸️ Command paused: `{command}`\n\n"
                    f"❓ It needs user input. Detected prompt:\n```{line.strip()}```\n"
                    f"👉 Please reply with what you want to enter (e.g., `y`, `n`, just `Enter`, etc.)"
                )

        stderr = process.stderr.read()
        if process.wait() != 0:
            return f"❌ Error:\n{stderr.strip()}"

        return "".join(output).strip()

    except Exception as e:
        return f"⚠️ Exception occurred: {str(e)}"
   
    
shell_tool = {
    "type": "function",
    "function": {
        "name": "run_shell_command",
        "description": "Execute a shell command and interact if it asks for input.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run"
                },
                "user_input": {
                    "type": "string",
                    "description": "User input to continue an interactive command (e.g., 'y' or 'Enter')",
                    "nullable": True
                }
            },
            "required": ["command"]
        }
    }
}
