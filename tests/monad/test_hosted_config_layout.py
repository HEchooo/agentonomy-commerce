from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
SERVICE_PATH = REPOSITORY_ROOT / "packaging/monad/agentonomy-commerce.service"
HOSTED_OPERATIONS_PATH = REPOSITORY_ROOT / "docs/monad/hosted-operations.md"
ERC8004_DOC_PATH = REPOSITORY_ROOT / "docs/monad/erc8004.md"


def test_hosted_registry_config_uses_web_tree_and_keeps_signer_protected():
    service = SERVICE_PATH.read_text(encoding="utf-8")
    hosted_operations = HOSTED_OPERATIONS_PATH.read_text(encoding="utf-8")
    erc8004_doc = ERC8004_DOC_PATH.read_text(encoding="utf-8")

    assert "--erc8004-config /var/lib/agentonomy-web/erc8004.json" in service
    assert "--erc8004-config /etc/agentonomy-commerce/erc8004.json" not in service
    assert "ReadWritePaths=/var/lib/agentonomy-web" in service

    assert "`/var/lib/agentonomy-web/erc8004.json`" in hosted_operations
    assert "`/var/lib/agentonomy-web`" in hosted_operations
    assert "mode `0700`" in hosted_operations
    assert "mode `0600`" in hosted_operations
    assert "`/etc/agentonomy-commerce/signer.json`" in hosted_operations
    assert "agentonomy-sign" in hosted_operations

    assert "`/var/lib/agentonomy-web/erc8004.json`" in erc8004_doc
    assert "`/etc/agentonomy-commerce/signer.json`" in erc8004_doc
