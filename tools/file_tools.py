import os
from typing import List, Dict


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