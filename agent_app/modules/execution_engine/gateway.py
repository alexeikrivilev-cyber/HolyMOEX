from __future__ import annotations

from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects import ExternalRequest, ExternalResponse


class ExecutionGateway(Protocol):
    def process(self, external_request: ExternalRequest) -> Any:
        ...


def submit_via_gateway(gateway: Any, external_request: ExternalRequest) -> ExternalResponse:
    if gateway is None:
        raise GatewayUnavailableError("external_request_gateway_unavailable")

    if hasattr(gateway, "submit_order"):
        result = gateway.submit_order(external_request)
    elif hasattr(gateway, "process"):
        result = gateway.process(external_request)
    elif hasattr(gateway, "execute"):
        result = gateway.execute(external_request)
    else:
        raise GatewayUnavailableError("external_request_gateway_adapter_invalid")

    response = getattr(result, "response", result)
    if isinstance(response, ExternalResponse):
        return response
    if isinstance(response, Mapping):
        return ExternalResponse.from_dict(response)
    raise GatewayUnavailableError("external_request_gateway_response_invalid")


class GatewayUnavailableError(ValueError):
    """Raised when live trading cannot reach the documented Gateway boundary."""
