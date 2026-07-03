---
id: onboarding_checkpoint_user
version: "1.0.0"
description: User prompt for generating tailored Mighty checkpoint instructions
variables:
  - description
  - api_key
  - url
---

Agent description: {description}
API key: {api_key}
Mighty URL: {url}

API endpoints:
  Authorize: POST {url}/api/authorize
    body: {{"api_key":"{api_key}","action_type":"<type>","label":"<desc>","fields":[["Key","Val"]]}}
  Poll status: GET {url}/api/status/<request_id>  →  approved | denied | pending | timeout
  Record (no approval): POST {url}/api/record
    body: {{"api_key":"{api_key}","action_type":"<type>","label":"<desc>","outcome":"completed"}}
