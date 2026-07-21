# Restore role max channels translations

## Goal

Prevent untranslated i18n placeholders from appearing in the role editor's
maximum-channel fields and guard the maintained English and Chinese locales
against the same regression.

## Background

- `RolesPanel.tsx` still renders `roles.maxChannels`,
  `roles.maxChannelsPlaceholder`, and `roles.maxChannelsHint`.
- Commit `32f02caf` removed the corresponding English and Chinese values while
  deleting the generic channel framework, although these role-limit strings
  were still in use.
- The extraction script later recreated the missing entries with visible
  placeholder values. Japanese, Korean, and Russian retained real translations.

## Requirements

- Restore the known English translations for all three keys.
- Restore the known Simplified Chinese translations for all three keys.
- Add an automated i18n validation that fails when generated placeholder
  values remain at these role-limit key paths in maintained locale files.
- Preserve unrelated locale content and existing uncommitted user changes.

## Acceptance Criteria

- [x] The role form displays readable labels, placeholder text, and help text
      in English and Simplified Chinese.
- [x] No `roles.maxChannels*` value in `en.json` or `zh.json` is a generated
      placeholder.
- [x] The relevant frontend i18n validation and JSON parsing checks pass.
- [x] The validation reports a useful key path when a placeholder is found.

## Out of Scope

- Translating unrelated pre-existing placeholder entries.
- Changing role-limit behavior or the `RolesPanel` component UI.

## Technical Notes

- This is a lightweight localization regression fix; a PRD-only task is
  sufficient.
