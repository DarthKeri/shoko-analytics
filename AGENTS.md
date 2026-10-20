# Project instructions

- Develop through scoped branches and pull requests; describe checks and their limits.
- Keep the product version at 0.1.0. Change it only when the user explicitly requests a new version; fixes and rebuilds must not bump it.
- The Windows MVP is implemented. Describe verified local behavior and unverified external integrations separately; see docs/QA.md.
- Never commit credentials, tokens, Telegram sessions, real exports, personal data, or local runtime settings.
- Only explicitly allowlisted test Google spreadsheet IDs may be written during development.
- Preparation and status checks must not write to Google Sheets. Write only the exact prepared plan after explicit user confirmation. The user removed the change-preview screen on 2026-10-19: show only a short confirmation with date/selected operations; preserve backups and post-write checks.
- Stop dependent stages when required input fails. Never silently fall back to stale input or turn parsing errors into zeroes.
- Keep the original ScriptExcel archive unchanged. Do not execute legacy scripts against live services.
- The active workflow follows the user-approved DOCX + three RTF profile, documented in docs/DOCX_RTF_PARITY.md. AM/AO/H, month-specific historical books, and preserved J:O formulas were explicitly agreed on 2026-10-19. Do not mix the conflicting TXT Google commands #2/#3 or the old primary-Python calculation into Update All.

- User-approved completion on 2026-10-19: Tenet T77 in the price order is an alias for Tenet T7 2026; create the two reviewed model rows and one Tenet Garage row, keep Tenet Garage O empty and D/BN linked to target F. Resolve fleet blocks by model/year, with strict missing/ambiguous-data errors; keep ordinary and carsharing Polo/Octavia 2019 separate for all three periods. Existing J:O are preserved, with explicit new links for these approved models only.

- User-approved BP correction on 2026-10-19: during the Garage operation bind BP to G of «Предложение цен» through a live model/year lookup, retaining ordinary/carsharing variants. Never use fixed source rows or «Простои» B. Missing/ambiguous matches block preparation; preserve every other existing formula. Verify effective BP values as well as exact formulas.

- User-approved Garage formula repair on 2026-10-19 supersedes the BP-only preservation restriction for this scoped block: resolve D/E/F/G by full model/year (legacy D Простои C -> target G, existing explicit F links and Tenet D -> F); retain BP -> G; fix BW to its own row while retaining E/G arithmetic. Preserve J:O raw formulas, manual dashes, market ranges and pricing coefficients. Use dash/no-data comments without a market, including CH/CI/CJ dependencies; CM is the agreed independent MIN(MAX(BU;CI);BV). Verify effective links, the retained pricing arithmetic, absence of row formula errors, full backups and adjacent BF copies. Do not silently change unrelated class/age/tariff rules.

- User-approved BW option 2 on 2026-10-19 supersedes the E/G estimate: BW reads actual total P by full model/year; BX reads rental idle NV W/(Q+U), independent of buyout counts. At zero rental base use a dash and no price recommendation, with an explanatory comment when market exists. Guard CB/CK dependencies without changing coefficients. Preserve Tenet's empty extra recommendation block.

- User-approved BF highlighting on 2026-10-19/20: mark exact written/cleared cells and changed formula results only in newly created BF sheets; retain old values, original/underlying formatting and comments. Do not color entire live columns. The confirmed plan includes backup_highlights policy; Google highlighting follows successful value verification, is bounded, read back and checked, and is never blindly retried. Keep copies adjacent and version 0.1.0.
