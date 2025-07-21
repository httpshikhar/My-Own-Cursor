import os
import json
from dotenv import load_dotenv
from openai import AzureOpenAI

# Load environment variables
load_dotenv()
azure_oai_endpoint = os.getenv("AZURE_OAI_ENDPOINT")
azure_oai_key = os.getenv("AZURE_OAI_KEY")
azure_oai_deployment = os.getenv("AZURE_OAI_DEPLOYMENT")

# Initialize Azure OpenAI client
client = AzureOpenAI(
    azure_endpoint=azure_oai_endpoint,
    api_key=azure_oai_key,
    api_version="2024-02-15-preview"
)

# === TOOL: write_file ===
def write_file(path: str, content: str) -> str:
    try:
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        return f"✅ File written successfully at: {path}"
    except Exception as e:
        return f"❌ Failed to write file at {path}: {str(e)}"

write_file_tool = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Creates or overwrites a single file with the provided content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the file to write (e.g., 'index.html')"
                },
                "content": {
                    "type": "string",
                    "description": "Content of the file"
                }
            },
            "required": ["path", "content"]
        }
    }
}

# === TOOL: write_files ===
def write_files(files: list[dict]) -> str:
    results = []
    for file in files:
        path = file.get("path")
        content = file.get("content")
        if not path or content is None:
            results.append(f"❌ Invalid file entry: {file}")
            continue

        try:
            dir_path = os.path.dirname(path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path)

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            results.append(f"✅ File written: {path}")
        except Exception as e:
            results.append(f"❌ Error writing {path}: {str(e)}")

    return "\n".join(results)

write_files_tool = {
    "type": "function",
    "function": {
        "name": "write_files",
        "description": "Creates multiple files with given paths and content.",
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "description": "List of file objects with path and content",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path (e.g., 'app/main.py')"
                            },
                            "content": {
                                "type": "string",
                                "description": "Code/text to write inside the file"
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            "required": ["files"]
        }
    }
}

# === REGISTER TOOLS ===
tools = [write_file_tool, write_files_tool]

# === SEND USER PROMPT ===
response = client.chat.completions.create(
    model=azure_oai_deployment,
    messages=[
        {
            "role": "system",
            "content": "You are a coding assistant. Use tools to create files as needed. Always return tool calls when writing code."
        },
        {
            "role": "user",
            "content": "Create a folder called 'website' and inside it, generate three files:\n- index.html with basic HTML\n- style.css with a red background\n- script.js with a console.log"
        }
    ],
    tools=tools,
    tool_choice="auto"  # Let the LLM decide which tool to call
)

# === PROCESS TOOL CALL ===
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
