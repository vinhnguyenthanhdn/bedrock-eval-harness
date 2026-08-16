"""The one module that talks to AWS.

`boto3` is not a dependency of this project: everything else here runs on the standard
library, and CI never needs credentials. It is imported when a real run is asked for, and
not before, so a missing SDK is an error at the moment you tried to call a model rather
than at the moment you imported anything.

The whole module is four lines of logic wrapped in an explanation, and that is the design:
if talking to the service were more than this, the interesting parts of the harness would
be untestable without an account.
"""

from __future__ import annotations

from typing import Any, Callable

DEFAULT_REGION_ENV = "AWS_REGION"

MISSING_SDK = (
    "boto3 is not installed. It is not a dependency of this project because nothing else"
    " here needs it; install it to run against Bedrock:\n\n    pip install boto3\n\n"
    "Credentials come from the standard AWS credential chain. This harness never reads a"
    " key from a suite, a run file or a flag."
)


class MissingSDK(Exception):
    """boto3 is not installed, and a real call was requested."""


class BedrockConverseClient:
    """`invoke(model_id, body)` on top of `bedrock-runtime.converse`.

    `client_factory` exists so the contract with the SDK — modelId beside the body, the
    response returned untouched — is covered by a test that installs nothing. It is not a
    hook for behaviour: anything that shapes a request or reads a response lives above
    this boundary, where it can be tested for real.
    """

    def __init__(
        self,
        region: str | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.region = region
        self._client = (client_factory or _boto3_client)(region)

    def invoke(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        # modelId is a path parameter on the wire; boto3 takes it as a keyword beside the
        # body fields. Nesting it inside the body is the mistake this line exists to avoid.
        return self._client.converse(modelId=model_id, **body)


def _boto3_client(region: str | None) -> Any:
    try:
        import boto3  # noqa: PLC0415 - deliberately lazy; see the module docstring
    except ImportError as exc:  # pragma: no cover - exercised through MissingSDK message
        raise MissingSDK(MISSING_SDK) from exc
    kwargs = {"region_name": region} if region else {}
    return boto3.client("bedrock-runtime", **kwargs)
