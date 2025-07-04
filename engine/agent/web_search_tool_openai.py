from typing import Optional

from engine.agent.agent import Agent, AgentPayload, ChatMessage, ToolDescription
from engine.llm_services.llm_service import LLMService
from engine.trace.trace_manager import TraceManager


DEFAULT_WEB_SEARCH_OPENAI_TOOL_DESCRIPTION = ToolDescription(
    name="Web_Search_Tool",
    description="Answer a question using web search.",
    tool_properties={
        "query": {
            "type": "string",
            "description": "The standalone question to be answered using web search.",
        },
    },
    required_tool_properties=["query"],
)


class WebSearchOpenAITool(Agent):
    def __init__(
        self,
        llm_service: LLMService,
        trace_manager: TraceManager,
        component_instance_name: str,
        tool_description: ToolDescription = DEFAULT_WEB_SEARCH_OPENAI_TOOL_DESCRIPTION,
    ):
        super().__init__(
            trace_manager=trace_manager,
            tool_description=tool_description,
            component_instance_name=component_instance_name,
        )
        self._llm_service = llm_service

    async def _run_without_trace(
        self,
        *inputs: AgentPayload,
        query: Optional[str] = None,
    ) -> AgentPayload:
        agent_input = inputs[0]
        query_str = query or agent_input.last_message.content
        output = self._llm_service.web_search(query_str)
        return AgentPayload(messages=[ChatMessage(role="assistant", content=output)])
