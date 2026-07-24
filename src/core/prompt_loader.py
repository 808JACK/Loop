"""Prompt file loader helpers with compilation support."""

from functools import cache
from pathlib import Path
from string import Template

_PROMPT_DIR = Path(__file__).resolve().parents[1] / "graph" / "prompts"


class PromptTemplate:
    """Prompt template with compilation support following plan.txt pattern."""

    def __init__(self, template: str):
        self.template = template

    def compile(self, **kwargs) -> str:
        """Compile the template with provided variables."""
        try:
            return self.template.format(**kwargs)
        except KeyError:
            # Fallback to Template for more flexible variable handling
            return Template(self.template).safe_substitute(kwargs)


@cache
def load_prompt(filename: str) -> PromptTemplate:
    """Load a prompt template from src/graph/prompts/."""
    content = (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
    return PromptTemplate(content)
