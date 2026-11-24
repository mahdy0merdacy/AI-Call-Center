"""The real tenant configs load the way the orchestrator reads them.

tenants/validate.py checks the schema; this checks the configs survive the
orchestrator's own loader, which is what actually runs in production.
"""

from __future__ import annotations

import re

from app.flow import TENANTS_DIR, load_tenants


def test_all_tenant_configs_load():
    tenants = load_tenants(TENANTS_DIR)
    assert tenants, f"no tenants found in {TENANTS_DIR}"
    for t in tenants.values():
        for flow in t.flows:
            if flow.tool:
                assert flow.tool in t.tools, f"{t.slug}: {flow.tool} not allowlisted"
            for slot in flow.slots:
                re.compile(slot.pattern)
