# Security policy

## Supported versions

Security fixes are made on the latest release and the current `main` branch.
Older revisions may not receive patches.

## Reporting a vulnerability

Please do not disclose a suspected vulnerability in a public issue, discussion,
pull request, screenshot, or full Codex transcript.

Use GitHub's **Security** tab and choose **Report a vulnerability** when that
option is available. If private vulnerability reporting is not enabled, contact
the maintainer privately using the contact details on their GitHub profile. If
no private channel is listed, open a minimal issue asking for a private contact
method without including vulnerability details.

Include the affected revision, impact, reproduction steps, and any suggested
mitigation. Remove API keys, tokens, prompts, local file contents, usernames,
and unrelated terminal history. You should receive an acknowledgement within
seven days.

Useful areas to scrutinize include executable discovery, shell configuration,
temporary files and Unix sockets, lifecycle hook construction, tmux process
boundaries, and uninstall path handling.
