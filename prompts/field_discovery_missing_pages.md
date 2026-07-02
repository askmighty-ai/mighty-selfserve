---
id: field_discovery_missing_pages
version: "1.0.0"
description: Suggest missing account page paths for field discovery
variables:
  - source
  - missing_str
---

Based on this {source} account page text, what specific page URLs or sections are probably missing that would contain: {missing_str}?

List only specific paths like /my-account/certificates or /loyalty/wallet. Max 5 paths. One per line. No explanation.
