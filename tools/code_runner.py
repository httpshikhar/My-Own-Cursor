import subprocess
import os

def run_code_file(path: str, inputs: list[str] = None) -> str:
    ext = os.path.splitext(path)[1]
    input_data = "\n".join(inputs) if inputs else None

    try:
        if ext == ".py":
            result = subprocess.run(
                ["python3", path],
                input=input_data,
                capture_output=True,
                text=True
            )

        elif ext == ".sh":
            result = subprocess.run(
                ["bash", path],
                input=input_data,
                capture_output=True,
                text=True
            )

        elif ext == ".cpp":
            # Compile to binary
            binary_path = path.replace(".cpp", "")
            compile_res = subprocess.run(
                ["g++", path, "-o", binary_path],
                capture_output=True,
                text=True
            )
            if compile_res.returncode != 0:
                return f"❌ Compilation failed:\n{compile_res.stderr}"

            result = subprocess.run(
                [f"./{binary_path}"],
                input=input_data,
                capture_output=True,
                text=True
            )

        else:
            return f"❌ Unsupported file type: {ext}"

        if result.returncode == 0:
            return f"✅ Output:\n{result.stdout}"
        else:
            return f"❌ Error:\n{result.stderr}"

    except Exception as e:
        return f"⚠️ Exception: {str(e)}"


run_code_file_tool = {
    "type": "function",
    "function": {
        "name": "run_code_file",
        "description": "Run code file based on extension. Supports Python (.py), Bash (.sh), and C++ (.cpp). Returns output or error traceback.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the code file."
                },
                "inputs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional simulated user inputs for interactive programs."
                }
            },
            "required": ["path"]
        }
    }
}

