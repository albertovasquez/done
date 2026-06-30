"""Back-compat alias. `vibeproxy` was renamed to `proxy`; import from
`harness.proxy`. This shim keeps existing importers working for one release."""
from harness.proxy import *          # noqa: F401,F403
from harness.proxy import (          # explicit re-export of module-level names
    DEFAULT_MODEL, base_url, api_key, default_model, model_id,
    completion_kwargs, model_kwargs, model_set_in, model_value,
)
