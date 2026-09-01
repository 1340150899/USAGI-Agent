"""Framework service bootstrap; business registration is intentionally separate."""
from usagi_agent.server.service_initializer import ServiceRuntimeInitializer


def bootstrap(settings, *, model_adapter):
    """Create an unconfigured ServerRuntime without compiling business graphs."""
    return ServiceRuntimeInitializer.init(settings, model_adapter=model_adapter)
