import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import yaml
from apolo_app_types_fixtures.constants import APP_ID, APP_SECRETS_NAME

from apolo_app_types.protocols.common import (
    ApoloSecret,
    ContainerImage,
    Env,
    IngressGrpc,
    IngressHttp,
    NoAuth,
)
from apolo_app_types.protocols.dockerhub import DockerConfigModel

from ..unit.test_inputs import GRPC_HOST, HTTP_HOST, generate


CHART_DIR = Path(__file__).parents[3] / "charts" / "triton"
RELEASE = "triton-app"
NAME = f"triton-{APP_ID}"
H2C_ANNOTATION = "traefik.ingress.kubernetes.io/service.serversscheme"

pytestmark = pytest.mark.skipif(
    not os.environ.get("CI") and shutil.which("helm") is None,
    reason="needs helm",
)


def render_text(values, tmp_path):
    values_file = tmp_path / "values.json"
    values_file.write_text(json.dumps(values))
    return subprocess.run(  # noqa: S603
        [
            shutil.which("helm"),
            "template",
            RELEASE,
            str(CHART_DIR),
            "--namespace",
            "project",
            "--values",
            str(values_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def render(values, tmp_path):
    documents = [
        doc for doc in yaml.safe_load_all(render_text(values, tmp_path)) if doc
    ]
    return {(doc["kind"], doc["metadata"]["name"]): doc for doc in documents}


def get_container(manifests):
    return manifests[("Deployment", NAME)]["spec"]["template"]["spec"]["containers"][0]


@pytest.mark.parametrize(
    ("http", "grpc", "services", "ingresses"),
    [
        (False, False, 1, 0),
        (True, False, 1, 1),
        (False, True, 2, 1),
        (True, True, 2, 2),
    ],
)
async def test_objects_per_endpoint_combination(
    setup_clients, minimal_inputs, tmp_path, http, grpc, services, ingresses
):
    inputs = minimal_inputs.model_copy(
        update={
            "ingress_http": IngressHttp() if http else None,
            "ingress_grpc": IngressGrpc() if grpc else None,
        }
    )
    manifests = render(await generate(setup_clients, inputs), tmp_path)

    kinds = Counter(kind for kind, _ in manifests)
    assert kinds == Counter(
        {
            "Deployment": 1,
            "Service": services,
            **({"Ingress": ingresses} if ingresses else {}),
        }
    )
    service = manifests[("Service", NAME)]
    assert [
        (port["name"], port["port"], port["targetPort"])
        for port in service["spec"]["ports"]
    ] == [("http", 8000, "http"), ("grpc", 8001, "grpc"), ("metrics", 8002, "metrics")]
    assert H2C_ANNOTATION not in (service["metadata"].get("annotations") or {})

    if http:
        ingress = manifests[("Ingress", NAME)]
        assert ingress["spec"]["ingressClassName"] == "traefik"
        assert ingress["spec"]["rules"] == [
            {
                "host": HTTP_HOST,
                "http": {
                    "paths": [
                        {
                            "path": "/",
                            "pathType": "Prefix",
                            "backend": {
                                "service": {"name": NAME, "port": {"name": "http"}}
                            },
                        }
                    ]
                },
            }
        ]
        assert ingress["metadata"]["annotations"] == {
            "traefik.ingress.kubernetes.io/router.middlewares": (
                "platform-platform-control-plane-ingress-auth@kubernetescrd"
            )
        }
    if grpc:
        grpc_service = manifests[("Service", f"{NAME}-grpc")]
        assert grpc_service["metadata"]["annotations"] == {H2C_ANNOTATION: "h2c"}
        assert [
            (port["name"], port["port"]) for port in grpc_service["spec"]["ports"]
        ] == [("grpc", 8001)]
        assert grpc_service["spec"]["selector"] == service["spec"]["selector"]
        ingress = manifests[("Ingress", f"{NAME}-grpc")]
        rule = ingress["spec"]["rules"][0]
        assert rule["host"] == GRPC_HOST
        assert rule["http"]["paths"][0]["backend"] == {
            "service": {"name": f"{NAME}-grpc", "port": {"name": "grpc"}}
        }
        assert ingress["metadata"]["annotations"] == {
            "traefik.ingress.kubernetes.io/router.entrypoints": "websecure",
            "traefik.ingress.kubernetes.io/router.middlewares": (
                "platform-platform-control-plane-ingress-auth@kubernetescrd"
            ),
        }


async def test_deployment(setup_clients, minimal_inputs, tmp_path):
    inputs = minimal_inputs.model_copy(
        update={
            "server_extra_args": ["--log-verbose=1", "--model-control-mode", "none"],
            "extra_env_vars": [
                Env(name="IT323_EXTRA", value="ok"),
                Env(name="IT323_SECRET", value=ApoloSecret(key="it323-secret")),
            ],
        }
    )
    manifests = render(await generate(setup_clients, inputs), tmp_path)

    deployment = manifests[("Deployment", NAME)]
    spec = deployment["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "Recreate"}
    assert spec["progressDeadlineSeconds"] == 2100
    pod = spec["template"]
    assert pod["metadata"]["labels"]["platform.apolo.us/inject-storage"] == "true"
    assert pod["metadata"]["labels"]["platform.apolo.us/preset"] == "cpu-small"
    assert pod["metadata"]["labels"]["platform.apolo.us/app-id"] == APP_ID
    assert (
        json.loads(pod["metadata"]["annotations"]["platform.apolo.us/inject-storage"])[
            0
        ]["mount_path"]
        == "/models"
    )
    assert "securityContext" not in pod["spec"]
    assert "imagePullSecrets" not in pod["spec"]
    assert pod["spec"]["volumes"] == [
        {"name": "dshm", "emptyDir": {"medium": "Memory"}}
    ]

    container = get_container(manifests)
    assert container["image"] == "nvcr.io/nvidia/tritonserver:26.10-py3"
    assert "command" not in container
    assert container["args"] == [
        "tritonserver",
        "--model-repository=/models",
        "--http-port=8000",
        "--grpc-port=8001",
        "--metrics-port=8002",
        "--log-verbose=1",
        "--model-control-mode",
        "none",
    ]
    assert [(port["name"], port["containerPort"]) for port in container["ports"]] == [
        ("http", 8000),
        ("grpc", 8001),
        ("metrics", 8002),
    ]
    assert container["env"] == [
        {"name": "IT323_EXTRA", "value": "ok"},
        {
            "name": "IT323_SECRET",
            "valueFrom": {
                "secretKeyRef": {"name": APP_SECRETS_NAME, "key": "it323-secret"}
            },
        },
    ]
    assert container["startupProbe"] == {
        "httpGet": {"path": "/v2/health/live", "port": "http"},
        "periodSeconds": 10,
        "failureThreshold": 180,
        "timeoutSeconds": 2,
    }
    assert container["livenessProbe"]["httpGet"] == {
        "path": "/v2/health/live",
        "port": "http",
    }
    assert container["readinessProbe"]["httpGet"] == {
        "path": "/v2/health/ready",
        "port": "http",
    }
    assert container["resources"]["limits"]["memory"] == "954M"
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert container["volumeMounts"] == [{"name": "dshm", "mountPath": "/dev/shm"}]


async def test_progress_deadline_follows_the_startup_budget(
    setup_clients, minimal_inputs, tmp_path
):
    values = await generate(setup_clients, minimal_inputs)
    values["probe"] = {"startup": {"periodSeconds": 10, "failureThreshold": 6}}
    values["progressDeadlineMarginSeconds"] = 60

    manifests = render(values, tmp_path)

    assert manifests[("Deployment", NAME)]["spec"]["progressDeadlineSeconds"] == 120


async def test_private_image_pull_secret(setup_clients, minimal_inputs, tmp_path):
    inputs = minimal_inputs.model_copy(
        update={
            "image": ContainerImage(
                repository="registry.example.com/triton",
                tag="custom",
                imagepullsecret=DockerConfigModel(filecontents="e30="),
            )
        }
    )
    manifests = render(await generate(setup_clients, inputs), tmp_path)

    secret = manifests[("Secret", f"{NAME}-dockerconfig")]
    assert secret["type"] == "kubernetes.io/dockerconfigjson"
    assert secret["data"] == {".dockerconfigjson": "e30="}
    pod = manifests[("Deployment", NAME)]["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": f"{NAME}-dockerconfig"}]
    assert get_container(manifests)["image"] == "registry.example.com/triton:custom"


async def test_rendering_is_stable(setup_clients, minimal_inputs, tmp_path):
    inputs = minimal_inputs.model_copy(
        update={
            "ingress_http": IngressHttp(auth=NoAuth()),
            "ingress_grpc": IngressGrpc(),
        }
    )
    values = await generate(setup_clients, inputs)

    assert render_text(values, tmp_path) == render_text(values, tmp_path)
