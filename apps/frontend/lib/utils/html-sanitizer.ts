/**
 * Whitelist of allowed HTML tags for rich text content
 */
const ALLOWED_TAGS = ['strong', 'em', 'u', 'a'];

/**
 * Whitelist of allowed HTML attributes
 */
const ALLOWED_ATTR = ['href', 'target', 'rel'];

function stripTags(value: string): string {
  return value.replace(/<[^>]*>/g, '');
}

/**
 * Sanitizes HTML content using DOMPurify with a strict whitelist.
 * Only allows bold, italic, underline, and link formatting.
 * Uses isomorphic-dompurify which works in both browser and Node.js.
 *
 * @param dirty - The unsanitized HTML string
 * @returns Sanitized HTML string safe for rendering
 */
export async function sanitizeHtml(dirty: string): Promise<string> {
  if (!dirty) {
    return '';
  }

  const DOMPurifyModule = await import('isomorphic-dompurify');
  const DOMPurify = DOMPurifyModule.default;

  return DOMPurify.sanitize(dirty, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    FORCE_BODY: true,
  });
}

export function sanitizeHtmlFallback(dirty: string): string {
  return stripTags(dirty);
}

/**
 * Strips all HTML tags from content, returning plain text.
 * Uses isomorphic-dompurify which works in both browser and Node.js.
 *
 * @param html - HTML string to strip
 * @returns Plain text with all tags removed
 */
export function stripHtml(html: string): string {
  return stripTags(html);
}

/**
 * Checks if a string contains HTML tags.
 *
 * @param text - String to check
 * @returns True if the string contains HTML tags
 */
export function isHtmlContent(text: string): boolean {
  return /<[a-z][\s\S]*>/i.test(text);
}
