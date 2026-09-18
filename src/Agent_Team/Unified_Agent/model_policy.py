"""Preserve the domain reasoning policy when ABLATION uses GPT-5.6 Luna."""


def apply_request_policy(request):
    if str(request.get("model", "")).startswith("gpt-5.6-luna"):
        if "input" in request:
            request.setdefault("reasoning", {"effort": "none"})
        elif "messages" in request:
            request.setdefault("reasoning_effort", "none")
    return request


def patch_domain_module(module):
    original = module.domain_request
    def domain_request(request, *, domain):
        return apply_request_policy(original(request, domain=domain))
    module.domain_request = domain_request
