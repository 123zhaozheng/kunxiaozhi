export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 64;

export function passwordPolicyError(password: string): string | null {
  if (password.length < PASSWORD_MIN_LENGTH || password.length > PASSWORD_MAX_LENGTH) {
    return "length";
  }
  if (new TextEncoder().encode(password).length > 72) {
    return "bytes";
  }
  if (password !== password.trim() || /[\p{Cc}\p{Cf}]/u.test(password)) {
    return "whitespace";
  }
  const classes = [/\p{Lu}/u, /\p{Ll}/u, /\d/, /[^\p{L}\p{N}\s]/u].filter((pattern) => pattern.test(password));
  return classes.length >= 3 ? null : "composition";
}
