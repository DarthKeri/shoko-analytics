# Project instructions

- Develop through scoped branches and pull requests; describe checks and their limits.
- Keep the product version at 0.1.0. Change it only when the user explicitly requests a new version; fixes and rebuilds must not bump it.
- The Windows MVP is implemented. Describe verified local behavior and unverified external integrations separately; see docs/QA.md.
- Never commit credentials, tokens, Telegram sessions, real exports, personal data, or local runtime settings.
- Only explicitly allowlisted test Google spreadsheet IDs may be written during development.
- Preparation and status checks must not write to Google Sheets. Publish only the exact reviewed plan after explicit user confirmation.
- Stop dependent stages when required input fails. Never silently fall back to stale input or turn parsing errors into zeroes.
- Keep the original ScriptExcel archive unchanged. Do not execute legacy scripts against live services.
- MVP calculations follow the primary update_google_sheets.py mapping documented in docs/IMPLEMENTATION.md. Do not mix alternative RTF/TXT mappings without customer agreement.
