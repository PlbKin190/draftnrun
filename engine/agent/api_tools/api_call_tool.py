import logging
import json
from typing import Optional, Dict, Any

import requests
from openinference.semconv.trace import OpenInferenceSpanKindValues

from engine.agent.agent import (
    Agent,
    ChatMessage,
    AgentPayload,
    ToolDescription,
)
from engine.trace.trace_manager import TraceManager

LOGGER = logging.getLogger(__name__)

API_CALL_TOOL_DESCRIPTION = ToolDescription(
    name="api_call",
    description="A generic API tool that can make HTTP requests to any API endpoint.",
    tool_properties={
        "query_param1": {
            "type": "string",
            "description": "This the first query parameter to be sent to the API. ",
        },
        "query_param2": {
            "type": "string",
            "description": "This the second query parameter to be sent to the API.",
        },
    },
    required_tool_properties=[],
)


class APICallTool(Agent):
    TRACE_SPAN_KIND = OpenInferenceSpanKindValues.TOOL.value

    def __init__(
        self,
        trace_manager: TraceManager,
        component_instance_name: str,
        endpoint: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        fixed_parameters: Optional[Dict[str, Any]] = None,
        tool_description: ToolDescription = API_CALL_TOOL_DESCRIPTION,
    ) -> None:
        super().__init__(
            trace_manager=trace_manager,
            tool_description=tool_description,
            component_instance_name=component_instance_name,
        )
        self.trace_manager = trace_manager
        self.endpoint = endpoint
        self.method = method.upper()
        self.headers = headers or {}
        self.timeout = timeout
        self.fixed_parameters = fixed_parameters or {}

    def make_api_call(self, **kwargs) -> Dict[str, Any]:
        """Make an HTTP request to the configured API endpoint."""

        # Prepare headers
        request_headers = self.headers.copy()

        # Prepare request parameters
        request_kwargs = {
            "url": self.endpoint,
            "method": self.method,
            "headers": request_headers,
            "timeout": self.timeout,
        }

        # Combine fixed parameters with dynamic parameters
        all_parameters = self.fixed_parameters.copy()
        all_parameters.update(kwargs)

        # Handle parameters based on HTTP method
        if self.method in ["GET", "DELETE"]:
            if all_parameters:
                request_kwargs["params"] = all_parameters
        elif self.method in ["POST", "PUT", "PATCH"]:
            request_kwargs["json"] = all_parameters

        try:
            response = requests.request(**request_kwargs)
            response.raise_for_status()

            # Try to parse JSON response, fall back to text
            try:
                response_data = response.json()
            except ValueError:
                response_data = {"text": response.text}

            return {
                "status_code": response.status_code,
                "data": response_data,
                "headers": dict(response.headers),
                "success": True,
            }

        except requests.exceptions.RequestException as e:
            LOGGER.error(f"API request failed: {str(e)}")
            return {
                "status_code": getattr(e.response, "status_code", None) if hasattr(e, "response") else None,
                "error": str(e),
                "success": False,
            }

    async def _run_without_trace(
        self,
        *inputs: AgentPayload,
        **kwargs: Any,
    ) -> AgentPayload:

        # Make the API call
        api_response = self.make_api_call(**kwargs)

        # Format the API response as a readable message
        if api_response.get("success", False):
            content = json.dumps(api_response["data"], indent=2)
        else:
            content = f"API call failed: {api_response.get('error', 'Unknown error')}"

        return AgentPayload(
            messages=[ChatMessage(role="assistant", content=content)],
            artifacts={"api_response": api_response},
            is_final=False,
        )
