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

def edit_file(path: str, new_content: str, mode: str = "append") -> str:
    """
    Edit an existing file by appending, prepending, or replacing its content.

    Parameters:
    - path: file path to edit
    - new_content: text to add
    - mode: 'append', 'prepend', or 'replace' (default: append)
    """
    if not os.path.exists(path):
        return f"❌ File does not exist: {path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            current_content = f.read()

        if mode == "replace":
            updated_content = new_content
        elif mode == "prepend":
            updated_content = new_content + "\n" + current_content
        elif mode == "append":
            updated_content = current_content + "\n" + new_content
        else:
            return f"⚠️ Invalid mode: {mode}. Use 'append', 'prepend', or 'replace'."

        with open(path, "w", encoding="utf-8") as f:
            f.write(updated_content)

        return f"✅ File edited successfully: {path} (mode: {mode})"

    except Exception as e:
        return f"❌ Error editing file {path}: {str(e)}"

edit_file_tool = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": "Edits an existing file by appending, prepending, or replacing its content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file to edit"
                },
                "new_content": {
                    "type": "string",
                    "description": "Content to add to the file"
                },
                "mode": {
                    "type": "string",
                    "description": "How to edit the file: 'append', 'prepend', or 'replace'",
                    "enum": ["append", "prepend", "replace"]
                }
            },
            "required": ["path", "new_content"]
        }
    }
}
