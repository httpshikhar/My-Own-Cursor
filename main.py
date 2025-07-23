import os
import json
from dotenv import load_dotenv
from openai import AzureOpenAI
from tool_registry import get_available_tools
from chat_memory import ChatMemory
from tools.file_tools import *
from tools.shell_tool import *
from tools.code_runner import run_code_file, run_code_file_tool
from tools.planner_tool import plan_task, plan_task_tool

# Load secrets
load_dotenv()
AZURE_OAI_ENDPOINT = os.getenv("AZURE_OAI_ENDPOINT")
AZURE_OAI_KEY = os.getenv("AZURE_OAI_KEY")
AZURE_OAI_DEPLOYMENT = os.getenv("AZURE_OAI_DEPLOYMENT")

# Init client and tools
client = AzureOpenAI(
    azure_endpoint=AZURE_OAI_ENDPOINT,
    api_key=AZURE_OAI_KEY,
    api_version="2024-02-15-preview"
)
tools = [write_file_tool, write_files_tool, shell_tool, edit_file_tool, read_file_tool, read_files_tool, run_python_file_tool, run_code_file_tool, plan_task_tool]
history = ChatMemory()

# Initial system prompt
history.add("system", """
You are an AI Software Engineer working inside an IDE.

Your job is to take high-level goals from the user (e.g. "Build a CLI todo app") and complete them fully using the available tools.

## Step-by-step Strategy:

1. **Understand the User Goal**: Read the user's message carefully and identify what final outcome they want.
2. **Plan the Task**: Call the `plan_task` tool to break the goal into smaller subtasks (e.g. create folder, write main.py, define CLI structure).
3. **Execute Each Subtask**: For each subtask, use available tools (`write_file`, `read_file`, `run_code_file`, `edit_file`, `shell_command`, etc.)
4. **Auto-Evaluate Outputs**:
   - If you generate code, immediately run it using `run_code_file`.
   - If there is an error, read the traceback and try to fix the issue by editing the code.
   - Repeat until the output matches the expected behavior.
5. **Self-Correct if Needed**: If the result does not satisfy the original goal, iterate again with updated reasoning.
6. **Don't Ask the User to Retry**: Always try to solve the issue yourself unless more clarification is absolutely needed.

## Goals:
- Be as autonomous as possible.
- Always verify your work.
- Break down and complete complex goals step-by-step.

You have access to tools like:
- `write_file`, `edit_file`, `read_file`
- `run_code_file` (supports Python, Bash, C++)
- `plan_task`
- `run_shell_command`

Do not give up if something fails — read the output, fix the problem, and try again.

Return only the final results to the user, or updates when you're done with major milestones.
""")

# Main loop
while True:
    user_input = input("\n💬 User: ")
    if user_input.strip().lower() in ["exit", "quit"]:
        break

    # Step 1: Add user input to history
    history.add("user", user_input)

    # Step 2: Agent loop (LLM can call tools repeatedly until it's satisfied)
    for iteration in range(10):  # max 10 steps to prevent infinite loops
        print(f"\n🔁 Iteration {iteration + 1}")

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
            print("\n🤖 Assistant:", final_content)
            history.add("assistant", final_content)
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

            elif tool_name == "write_files":
                result = write_files(**args)

            elif tool_name == "run_shell_command":
                command = args.get("command")
                user_input_val = args.get("user_input")  # optional
                result = run_shell_command(command=command, user_input=user_input_val)

            elif tool_name == "edit_file":
                result = edit_file(**args)

            elif tool_name == "read_file":
                result = read_file(**args)

            elif tool_name == "read_files":
                result = read_files(**args)
            
            elif tool_name == "run_python_file":
                result = run_python_file(**args)
            
            elif tool_name == "run_code_file":
                result = run_code_file(**args)


            else:
                result = f"⚠️ Unknown tool: {tool_name}"

            print(f"\n🔧 Tool `{tool_name}` result:\n{result}")

            # Step 5: Add tool result back to chat history
            history.messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result)
            })
    else:
        print("⚠️ Max iterations reached. Stopping.")