import os
import json
from dotenv import load_dotenv
from openai import AzureOpenAI
from tool_registry import get_available_tools
from chat_memory import ChatMemory
from tools.file_tools import * 

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
tools = [write_file_tool, write_files_tool]
history = ChatMemory()

# Initial system prompt
history.add("system", """
You are an autonomous AI coding assistant. You can:
- Generate code
- Create files and folders
- Write content using the following tools:
  * write_file(path, content)
  * write_files([{path, content}, ...])
Respond in JSON when using tools. Ask clarifying questions when needed.
""")

# Main loop
while True:
    user_input = input("\n💬 User: ")
    if user_input.strip().lower() in ["exit", "quit"]:
        break

    history.add("user", user_input)

    response = client.chat.completions.create(
        model=AZURE_OAI_DEPLOYMENT,
        messages=history.get(),
        tools=tools,
        tool_choice="auto"
    )

    reply = response.choices[0].message
    history.add("assistant", reply.content or "(tool call)")
    print("\n🤖 Assistant:", reply.content)

    tool_calls = response.choices[0].message.tool_calls

    if tool_calls:
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if tool_name == "write_file":
                result = write_file(**args)
            elif tool_name == "write_files":
                result = write_files(**args)
            else:
                result = f"⚠️ Unknown tool: {tool_name}"

            print(result)
    else:
        print("❌ No tool call detected. LLM didn't use a tool.")