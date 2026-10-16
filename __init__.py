from typing_extensions import override
from comfy_api.latest import ComfyExtension, io

from .minimax_h3_prompt import MinimaxH3PromptGenerator
from .minimax_h3_llm_helper import MinimaxH3SystemPromptBuilder, MinimaxH3LLMOutputParser
from .minimax_h3_dialogue_merger import MinimaxH3DialogueMerger


class MinimaxH3Extension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            MinimaxH3PromptGenerator,
            MinimaxH3SystemPromptBuilder,
            MinimaxH3LLMOutputParser,
            MinimaxH3DialogueMerger,
        ]


async def comfy_entrypoint() -> MinimaxH3Extension:
    return MinimaxH3Extension()
