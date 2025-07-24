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
from tools.project_generator import generate_project_structure, generate_project_structure_tool

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
tools = [write_file_tool, write_files_tool, 
shell_tool, edit_file_tool, read_file_tool, read_files_tool, 
run_python_file_tool, run_code_file_tool, plan_task_tool, 
generate_project_structure_tool]

history = ChatMemory()

# Initial system prompt
history.add("system", """
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
   To do so, construct a valid `structure` dictionary in this format:

   {
     "folder_name": "ProjectFolderName",
     "files": [
       {
         "path": "main.py",
         "content": "# main file code"
       },
       {
         "path": "models/task.py",
         "content": "# Task class code"
       },
       {
         "path": "utils/helper.py",
         "content": "# utility code"
       },
       {
         "path": "README.md",
         "content": "Description of the project"
       }
     ]
   }

   Once generated, pass this to the `generate_project_structure` tool to create all files and folders in one shot.

4. **Execute Step-by-Step**  
   For individual tasks, use:
   - `write_file`, `read_file`, `edit_file`
   - `run_code_file` (multi-language: Python, Bash, C++, etc.)
   - `run_shell_command`

5. **Verify and Self-Debug**  
   - Always run generated code.
   - If it fails, parse the traceback and fix it.
   - Retry until success or max retries.
   - Never stop at first failure — debug and iterate.

6. **Stay Autonomous**  
   Don’t ask for permission at every step. Only ask if user input is absolutely required.  
   You are expected to complete the task independently.

7. **Suggest the Next Move**  
   After each step (e.g. file creation, code execution), clearly suggest the next most logical move to complete the goal.

## Example (Project Structure Use)

User prompt:
> Build a basic CLI todo app with `main.py`, `models/task.py`, `utils/helper.py`, and a `README.md`

You should generate:
{
  "folder_name": "TodoApp",
  "files": [
    {
      "path": "main.py",
      "content": "..."
    },
    ...
  ]
}

Then use:
generate_project_structure(structure=<above dict>)

## Tools You Can Use

- write_file_tool, write_files_tool, 
- shell_tool, 
- edit_file_tool, 
- read_file_tool, read_files_tool, 
- run_python_file_tool, run_code_file_tool, 
- plan_task_tool, 
- generate_project_structure_tool
"""
)

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

            elif tool_name == "generate_project_structure":
                print(f"[DEBUG] args = {args}")
                result = generate_project_structure(**args)
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