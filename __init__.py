from typing_extensions import override
from comfy_api.latest import ComfyExtension, io

from .minimax_h3_refs import NODES
from .minimax_h3_dialogue_merger import MinimaxH3DialogueMerger


class MinimaxH3Extension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [*NODES, MinimaxH3DialogueMerger]


async def comfy_entrypoint() -> MinimaxH3Extension:
    return MinimaxH3Extension()
