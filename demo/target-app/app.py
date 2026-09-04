"""Toy Flask-less app used only to demo two concurrent Claude Code agents
editing the same file through AgentRoom."""


def greet(name: str) -> str:
    # TODO(zhangsan): add an exclamation mark at the end of the greeting
    return f"Hello, {name}"


def farewell(name: str) -> str:
    # TODO(lisi): add a "See you soon" suffix
    return f"Bye, {name}"
