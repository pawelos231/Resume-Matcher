import { describe, expect, it } from 'vitest';

import { translate } from '@/lib/i18n/server';
import { getMessages } from '@/lib/i18n/messages';

describe('i18n messages', () => {
  it('returns Polish overrides for translated keys', () => {
    const messages = getMessages('pl');

    expect(messages.settings.uiLanguage).toBe('Język interfejsu');
    expect(messages.dashboard.masterResume).toBe('CV główne');
  });

  it('falls back to English for untranslated Polish keys', () => {
    const messages = getMessages('pl');

    expect(messages.builder.formatting.panelTitle).toBe('Template & Formatting');
    expect(messages.tailor.promptOptions.full.label).toBe('Full tailor');
  });

  it('keeps new confirmation keys available for every locale via English fallback', () => {
    expect(translate('pl', 'confirmations.deleteAllTailoredResumesConfirmLabel')).toBe(
      'Usuń wszystkie'
    );
    expect(translate('es', 'confirmations.deleteAllTailoredResumesTitle')).toBe(
      'Delete Tailored Resumes'
    );
  });
});
