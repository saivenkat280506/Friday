"""Shared VisionAgent singleton used by routes and the command processor."""

from executor.vision_agent_loop import VisionAgent

vision_agent = VisionAgent()