export function validFieldPath(value: unknown): value is string {
  return typeof value === 'string' && value.length === 46 && /^\/f\/[A-Za-z0-9_-]{43}$/.test(value);
}

