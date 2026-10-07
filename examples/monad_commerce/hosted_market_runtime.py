"""Marketplace-only process adapter; Core remains behind its private pipe."""
from agentonomy_commerce.runtime import ReviewRuntime
from examples.monad_commerce.public_runtime import PublicCommerceRuntime
from examples.monad_commerce.hosted_process import HostedCoreBridge


class HostedCommerceRuntime(PublicCommerceRuntime):
    def __init__(self, state_dir, merchant_port, bootstrap):
        ReviewRuntime.__init__(self, state_dir, merchant_port)
        self.core = HostedCoreBridge(self.state_dir, bootstrap)

    def hosted(self, method, params):
        return self.core.hosted(method, **params)

    def preview_state(self, preview_id):
        preview = self.repository.get_preview(preview_id)
        if preview is None:
            raise KeyError('preview not found')
        return preview.model_dump(mode='json')
