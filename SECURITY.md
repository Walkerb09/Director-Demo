# Security

## How secrets are handled

- **No secrets in the repository.** Tokens, keys and passwords are never committed. The repository is scanned for
  secrets before every release (see "Release checks" below), and GitHub's secret scanning is enabled.
- **The public demo needs no credentials.** Its Vast.ai template sets only public values (`PROVISIONING_SCRIPT`,
  `DIRECTOR_HF_REPO`, `DIRECTOR_PROFILE`). Template variables are readable by anyone, so a public template must never
  carry `HF_TOKEN` or a backup repository.
- **Private setups read secrets from environment variables only** (Vast.ai account-level variables), never from the
  template, the code or a config file. A token entered at install time is saved with owner-only permissions.
- **Private repositories stay private.** The private uploader refuses to upload to a repository that is public.

## Integrity

- Every release bundle is published with its SHA-256 in `latest.json`. The provisioning script downloads the bundle,
  checks the hash **before** unpacking, and refuses a file name it doesn't expect.
- Model weights are pinned by SHA-256 where the installer downloads them.
- The package carries an integrity manifest of every file; `verify_package.py` checks it.

## Release checks

Before a release is published:

1. A secret and personal-information scan over every file (tokens, keys, private-key blocks, credentials in URLs,
   high-entropy strings, emails, phone numbers, local user paths).
2. No personal media: only demo and synthetic test images ship.
3. Large third-party binaries (face-detection models) are not committed; the installer bundle provides them.
4. The integrity manifest verifies (and, in development, the offline test suite passes).

## Reporting a vulnerability

Please open a GitHub issue **without exploit details** and ask for a private contact, or use GitHub's
"Report a vulnerability" (private security advisory) on this repository. I'll respond as soon as I can.
