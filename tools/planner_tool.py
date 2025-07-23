# tools/planner_tool.py

def plan_task(goal: str) -> str:
    """
    This function doesn't do actual planning.
    It serves as a signal for the LLM to break down the task into subtasks.
    The LLM will take over and generate the breakdown in response.
    """
    return f"Planning initiated for goal: {goal}. Awaiting breakdown by the assistant."

# Tool schema for registering with the LLM
plan_task_tool = {
    "type": "function",
    "function": {
        "name": "plan_task",
        "description": "Break down a complex coding task into a sequence of executable subtasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The user's high-level software goal."
                }
            },
            "required": ["goal"]
        }
    }
}
