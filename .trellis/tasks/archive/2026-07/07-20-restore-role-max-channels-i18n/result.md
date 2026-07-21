# Result: restore role max-channel translations

- Restored readable `roles.maxChannels`, `roles.maxChannelsHint`, and
  `roles.maxChannelsPlaceholder` values in maintained English and Simplified
  Chinese locales.
- Added a regression test that reports the locale and full key path when a
  generated placeholder returns.

Verification:

- `pnpm exec tsx --test src/i18n/__tests__/roleMaxChannelsKeys.test.ts` — passed
- `pnpm exec tsc --noEmit` — passed
- ESLint for the new test — passed
- English and Chinese locale JSON parsing — passed
