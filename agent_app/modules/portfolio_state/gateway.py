from __future__ import annotations

from typing import Any, Mapping

from agent_app.contracts.unified_objects import ExternalRequest, ExternalResponse


class PortfolioGatewayError(ValueError):
    """Raised when broker sync cannot use the documented Gateway boundary."""


def request_via_gateway(gateway: Any, external_request: ExternalRequest) -> ExternalResponse:
    if gateway is None:
        raise PortfolioGatewayError("external_request_gateway_unavailable")
    if hasattr(gateway, "process"):
        result = gateway.process(external_request)
    elif hasattr(gateway, "execute"):
        result = gateway.execute(external_request)
    else:
        raise PortfolioGatewayError("external_request_gateway_adapter_invalid")

    response = getattr(result, "response", result)
    if isinstance(response, ExternalResponse):
        return response
    if isinstance(response, Mapping):
        return ExternalResponse.from_dict(response)
    raise PortfolioGatewayError("external_request_gateway_response_invalid")
