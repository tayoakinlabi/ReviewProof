# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: open the repository's **Security** tab and
choose **Report a vulnerability**. Do not open a public issue.

Include the version, steps to reproduce and the impact. Use synthetic data only.

## Treated as highest severity

- Any way for a planted error to reach an executor, target system or record.
- Any way to change, delete or reorder events without `rp verify` detecting it, given an
  externally stored head hash.
- Any way for the host-facing API to reveal which items are planted errors before a decision.

## Supported versions

ReviewProof is pre-release. Only the latest version receives fixes.
