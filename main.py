import os
import json
from dotenv import load_dotenv
from openai import AzureOpenAI
from tool_registry import get_available_tools
from chat_memory import ChatMemory
from tools.file_tools import *
from tools.shell_tool import *

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
tools = [write_file_tool, write_files_tool, shell_tool, edit_file_tool, read_file_tool, read_files_tool]
history = ChatMemory()

# Initial system prompt
history.add("system", """
You are an autonomous AI coding assistant. You can:
- Generate code
- Create files and folders
- Write and read files
- Explain file content, code, or directory structures when asked

Use tools like:
  * write_file(path, content)
  * write_files([{path, content}, ...])
  * read_file(path)
  * read_files(paths)

Respond in JSON when using tools. Ask clarifying questions when needed.
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