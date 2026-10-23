# Security and public-data policy

## Reporting a security issue

Use the **Security > Report a vulnerability** option on this repository for a
confidential report. Do not place tokens, account details, unredacted receipts,
private documents, or exploit credentials in a public issue or pull request.
If confidential reporting is unavailable, do not publish sensitive details.
No response-time guarantee is implied.

## Public repository boundary

Keep provider keys, broker credentials, environment files, agent instructions,
local planning notes, migration logs, draft proposals, personal identifiers and
private account exports outside this repository. A `.gitignore` rule prevents
some future additions; it does not remove previously committed content.

Before publishing changes, inspect the staged diff and scan the complete Git
history for secrets. If a real credential is exposed, revoke or rotate it at its
issuer; deleting a file alone does not revoke access or clear cached copies.

## Research use

Repository integrity and successful offline tests do not establish financial
safety, profitability, provider independence, or readiness for live trading.
The owner has not added a new license or third-party rights grant through this
repository-hygiene update.
