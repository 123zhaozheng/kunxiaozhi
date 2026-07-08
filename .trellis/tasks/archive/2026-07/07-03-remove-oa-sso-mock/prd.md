# PRD: Remove OA SSO Mock

## Goal

Remove development-only OA SSO mock (token → workcode without bank API).

## Acceptance Criteria

- Delete oa_sso_mock module and test_oa_sso_mock
- login/oa-sso always uses OASsoService
- oauth/providers oa_sso only returns enabled
- Remove OA_SSO_MOCK_* config and UI/i18n mock strings
- test_oa_login.py passes

## Status

Implemented in working tree; pending commit.
