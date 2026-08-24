# CohortSwitch Python SDK

```python
from cohortswitch import CohortSwitchClient

async with CohortSwitchClient(
    base_url="http://localhost:8000",
    sdk_key="cs_sdk_live_...",
) as client:
    enabled = await client.is_enabled("new_checkout", subject_key="user_42")
```

The SDK is async, typed, timeout-aware, and can keep a short in-process decision cache.

