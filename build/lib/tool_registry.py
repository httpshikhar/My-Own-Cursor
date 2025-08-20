from tools.file_tools import (
    write_file_tool,
    write_files_tool,
    edit_file_tool,
    read_file_tool,
    read_files_tool,
)
from tools.shell_tool import (
    shell_tool,  # run_shell_command
    run_python_file_tool,
)

def get_available_tools():
    return [
        # File tools
        write_file_tool,
        write_files_tool,
        edit_file_tool,
        read_file_tool,
        read_files_tool,
        # Shell/exec tools
        shell_tool,            # run_shell_command
        run_python_file_tool,
    ]
