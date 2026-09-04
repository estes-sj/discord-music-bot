# Contributing

Thanks for contributing to Discord Music Bot. By participating, keep reports and discussion focused, respectful, and safe for users and operators.

## Before Opening an Issue

- Search existing issues for the same problem.
- Check the current README and troubleshooting guidance.
- Do not include bot tokens, Spotify Client Secrets, OAuth authorization codes, complete redirect URLs, database files, logs containing private information, or a server invite you do not control.
- Report security vulnerabilities through the process in [SECURITY.md](SECURITY.md), not a public issue.

## Bug Reports

Use the bug report template and include reproducible steps, the bot version or image tag, the command used, and a redacted log excerpt when available. Describe expected and actual behavior. For playback problems, include whether the issue affects one source or every source, but do not share private playlist URLs or credentials.

## Pull Requests

1. Create a focused branch from the current default branch.
2. Keep changes small and scoped to one behavior.
3. Add or update focused tests for behavior changes.
4. Run the relevant tests and a Docker image build before opening the pull request.
5. Update the README when a command, environment variable, configuration option, or user-visible behavior changes.

Avoid committing `.env` files, secrets, databases, generated logs, or production-specific deployment material. Do not reformat unrelated code.

## License

By contributing, you agree that your contribution is licensed under the repository's [GPL-3.0 license](LICENSE).