import type { Locale } from '@/i18n/config';

import en from '@/messages/en.json';
import es from '@/messages/es.json';
import zh from '@/messages/zh.json';
import ja from '@/messages/ja.json';
import pt from '@/messages/pt-BR.json';
import pl from '@/messages/pl.json';

export type Messages = typeof en;

type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends Record<string, unknown> ? DeepPartial<T[K]> : T[K];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function mergeMessageRecords(
  base: Record<string, unknown>,
  overrides: Record<string, unknown>
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...base };

  for (const [key, overrideValue] of Object.entries(overrides)) {
    const baseValue = merged[key];

    if (isRecord(baseValue) && isRecord(overrideValue)) {
      merged[key] = mergeMessageRecords(baseValue, overrideValue);
      continue;
    }

    if (overrideValue !== undefined) {
      merged[key] = overrideValue;
    }
  }

  return merged;
}

function mergeMessages(base: Messages, overrides: DeepPartial<Messages>): Messages {
  return mergeMessageRecords(
    base as unknown as Record<string, unknown>,
    overrides as Record<string, unknown>
  ) as Messages;
}

const localeOverrides: Record<Locale, DeepPartial<Messages>> = {
  en,
  es,
  zh,
  ja,
  pt,
  pl,
};

const allMessages: Record<Locale, Messages> = {
  en,
  es: mergeMessages(en, localeOverrides.es),
  zh: mergeMessages(en, localeOverrides.zh),
  ja: mergeMessages(en, localeOverrides.ja),
  pt: mergeMessages(en, localeOverrides.pt),
  pl: mergeMessages(en, localeOverrides.pl),
};

export function getMessages(locale: Locale): Messages {
  return allMessages[locale] || allMessages.en;
}
