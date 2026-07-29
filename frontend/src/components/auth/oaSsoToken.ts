export const OA_SSO_TOKEN_QUERY_KEYS = [
  "Accesstoken",
  "token",
  "oa_token",
] as const;

export function readOaSsoToken(params: URLSearchParams): string | null {
  for (const key of OA_SSO_TOKEN_QUERY_KEYS) {
    const value = params.get(key);
    if (value?.trim()) return value;
  }
  return null;
}

export function removeOaSsoTokenParams(params: URLSearchParams): void {
  for (const key of OA_SSO_TOKEN_QUERY_KEYS) {
    params.delete(key);
  }
}
