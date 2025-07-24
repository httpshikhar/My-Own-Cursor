# tools/project_generator.py
import os

def generate_project_structure(folder_name: str, files: list[dict]):
    """
    Creates a project folder with the specified file paths and their contents.
    Each file will be placed under the base folder `folder_name`.
    """
    created_paths = []

    os.makedirs(folder_name, exist_ok=True)

    for file in files:
        file_path = os.path.join(folder_name, file["path"])
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(file.get("content", ""))
        created_paths.append(file_path)

    return {"message": "Project structure created", "paths": created_paths}


generate_project_structure_tool = {
    "type": "function",
    "function": {
        "name": "generate_project_structure",
        "description": "Creates a project folder with multiple files and their contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_name": {
                    "type": "string",
                    "description": "The name of the base folder to create."
                },
                "files": {
                    "type": "array",
                    "description": "A list of files to create, with relative paths and content.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative file path inside the project folder."
                            },
                            "content": {
                                "type": "string",
                                "description": "The content to write into the file."
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            "required": ["folder_name", "files"]
        }
    }
}
