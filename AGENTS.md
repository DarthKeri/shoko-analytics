# Project instructions

- Develop through scoped branches and pull requests; describe checks and their limits.
- The application is not implemented yet. Do not treat proposed architecture as verified behavior.
- Never commit credentials, tokens, Telegram sessions, real exports, personal data, or local runtime settings.
- Only explicitly allowlisted test Google spreadsheet IDs may be written during development.
- Preparation and status checks must not write to Google Sheets. Publish only the exact reviewed plan after explicit user confirmation.
- Stop dependent stages when required input fails. Never silently fall back to stale input or turn parsing errors into zeroes.
- Keep the original ScriptExcel archive unchanged. Do not execute legacy scripts against live services.
- Resolve the competing legacy calculation mappings before implementing them.