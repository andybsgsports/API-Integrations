"""S&S Activewear → NetSuite integration.

Pulls product/inventory/pricing data from the S&S Activewear REST API
(``api.ssactivewear.com``) and upserts it into NetSuite as matrix inventory
items via SuiteTalk REST with Token-Based Auth.

The supplier-facing side is REST/JSON (different from SanMar's SFTP feed),
but the destination side reuses the same NetSuite client and the same
sandbox-first safety pattern.
"""
