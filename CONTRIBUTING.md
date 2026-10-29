# Contributing

Pull requests are welcome. Kith is maintained in spare time for one family's site, so there are no promises
about when, or whether, a change is merged.

A pull request is ready when:

- `uv run pytest` passes with 100% line and branch coverage of `app/` (CI checks this against MariaDB).
- It does one thing, and its description says why.
- Database changes come as a new numbered file in `app/migrations/` that is safe to run twice.
- Templates and CSS use the theme tokens (`var(--token)`), not literal colors or fonts.
- It adds no new dependency without a good reason.

Bugs and ideas go in GitHub issues. Report security problems privately, through GitHub's "Report a vulnerability"
on the Security tab, not in an issue.

By contributing, you agree that your contribution is licensed under the AGPL-3.0, like the rest of Kith.
