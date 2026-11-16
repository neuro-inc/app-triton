import typing as t

from apolo_app_types.clients.kube import get_current_namespace
from apolo_app_types.outputs.base import BaseAppOutputsProcessor
from apolo_app_types.protocols.common import ServiceAPI
from apolo_app_types.protocols.triton import (
    TritonServerGRPCEndpoint,
    TritonServerHttpEndpoint,
)
from apolo_apps_triton.app_types import TritonAppOutputs


HTTP_PORT = 8000
GRPC_PORT = 8001
INGRESS_PORT = 443


def _get_external_host(helm_values: dict[str, t.Any], name: str) -> str | None:
    ingress = (helm_values.get("ingress") or {}).get(name) or {}
    if not ingress.get("enabled") or not ingress.get("host"):
        return None
    return t.cast(str, ingress["host"])


class TritonAppOutputProcessor(BaseAppOutputsProcessor[TritonAppOutputs]):
    async def _generate_outputs(
        self,
        helm_values: dict[str, t.Any],
        app_instance_id: str,
    ) -> TritonAppOutputs:
        internal_host = f"{helm_values['fullnameOverride']}.{get_current_namespace()}"
        http_host = _get_external_host(helm_values, "http")
        grpc_host = _get_external_host(helm_values, "grpc")
        return TritonAppOutputs(
            http_endpoint=ServiceAPI[TritonServerHttpEndpoint](
                internal_url=TritonServerHttpEndpoint(
                    host=internal_host, port=HTTP_PORT, protocol="http"
                ),
                external_url=TritonServerHttpEndpoint(
                    host=http_host, port=INGRESS_PORT, protocol="https"
                )
                if http_host
                else None,
            ),
            grpc_endpoint=ServiceAPI[TritonServerGRPCEndpoint](
                internal_url=TritonServerGRPCEndpoint(
                    host=internal_host, port=GRPC_PORT, protocol="http"
                ),
                external_url=TritonServerGRPCEndpoint(
                    host=grpc_host, port=INGRESS_PORT, protocol="https"
                )
                if grpc_host
                else None,
            ),
        )
