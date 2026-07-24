"""Thread management utilities for LangGraph checkpointed graphs.

Provides utilities to:
- List saved thread IDs (async)
- Get state snapshots for threads (async)
- Get full checkpoint history (async)
- Resume execution from any checkpoint (async)
"""

from typing import Any

from src.core.logging.logger import get_logger

logger = get_logger("thread_manager")


class ThreadManager:
    """Utilities for managing checkpointed threads in LangGraph."""

    def __init__(self, checkpointer: Any, graph: Any):
        """Initialize the thread manager.

        Args:
            checkpointer: The checkpointer instance (e.g., PostgresCheckpointer)
            graph: The compiled LangGraph graph
        """
        self.checkpointer = checkpointer
        self.graph = graph

    async def get_thread_state(self, thread_id: str):
        """Get the latest state snapshot for a thread.

        Args:
            thread_id: The thread identifier

        Returns:
            StateSnapshot with values, next nodes, config, metadata
        """
        config = {"configurable": {"thread_id": thread_id}}
        return await self.graph.aget_state(config)

    async def get_thread_history(self, thread_id: str) -> list:
        """Get full checkpoint history for a thread.

        Args:
            thread_id: The thread identifier

        Returns:
            List of StateSnapshot objects, newest first
        """
        config = {"configurable": {"thread_id": thread_id}}
        # aget_state_history is an async iterator
        history = []
        async for state in self.graph.aget_state_history(config):
            history.append(state)
        return history

    async def resume_from_checkpoint(
        self,
        thread_id: str,
        checkpoint_id: str | None = None,
        input_state: dict | None = None,
        callbacks: list[Any] | None = None,
    ):
        """Resume graph execution from a specific checkpoint.

        Args:
            thread_id: The thread identifier
            checkpoint_id: Optional specific checkpoint to resume from.
                          If None, resumes from latest checkpoint.
            input_state: Optional input state. If None, continues from
                        checkpoint state.
            callbacks: Optional list of LangChain callback handlers.

        Returns:
            Final state after execution completes
        """
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        if checkpoint_id:
            config["configurable"]["checkpoint_id"] = checkpoint_id
        if callbacks:
            config["callbacks"] = callbacks
        return await self.graph.ainvoke(input_state, config=config)

    def print_state_snapshot(self, snapshot, indent: int = 0) -> None:
        """Pretty print a state snapshot.

        Args:
            snapshot: The StateSnapshot to print
            indent: Number of indentation levels
        """
        prefix = "  " * indent
        logger.info(f"{prefix}Next: {snapshot.next}")
        checkpoint_id = snapshot.config["configurable"].get("checkpoint_id", "N/A")
        logger.info(f"{prefix}Checkpoint ID: {checkpoint_id}")
        if snapshot.metadata:
            logger.info(f"{prefix}Step: {snapshot.metadata.get('step', 'N/A')}")
            logger.info(f"{prefix}Source: {snapshot.metadata.get('source', 'N/A')}")
