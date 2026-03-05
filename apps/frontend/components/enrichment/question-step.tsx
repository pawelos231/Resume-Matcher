'use client';

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { ToggleSwitch } from '@/components/ui/toggle-switch';
import { ChevronLeft, ChevronRight, Briefcase, FolderKanban } from 'lucide-react';
import type {
  EnrichmentQuestion,
  EnrichmentItem,
  EnrichmentSupportContextInput,
  SupportSourceInput,
} from '@/lib/api/enrichment';
import { useTranslations } from '@/lib/i18n';

interface QuestionStepProps {
  question: EnrichmentQuestion;
  item: EnrichmentItem | undefined;
  answer: string;
  questionNumber: number;
  totalQuestions: number;
  supportContext: EnrichmentSupportContextInput;
  onSupportContextChange: (next: EnrichmentSupportContextInput) => void;
  onAnswer: (answer: string) => void;
  onNext: () => void;
  onPrev: () => void;
  onFinish: () => void;
  isFirst: boolean;
  isLast: boolean;
}

export function QuestionStep({
  question,
  item,
  answer,
  questionNumber,
  totalQuestions,
  supportContext,
  onSupportContextChange,
  onAnswer,
  onNext,
  onPrev,
  onFinish,
  isFirst,
  isLast,
}: QuestionStepProps) {
  const { t } = useTranslations();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [localAnswer, setLocalAnswer] = useState(answer);

  // Sync local answer with prop
  useEffect(() => {
    setLocalAnswer(answer);
  }, [answer, question.question_id]);

  // Auto-focus textarea when question changes
  useEffect(() => {
    textareaRef.current?.focus();
  }, [question.question_id]);

  const handleChange = (value: string) => {
    setLocalAnswer(value);
    onAnswer(value);
  };

  const githubSupport: SupportSourceInput = supportContext.github ?? {
    enabled: false,
    profile: '',
    notes: '',
  };
  const linkedinSupport: SupportSourceInput = supportContext.linkedin ?? {
    enabled: false,
    profile: '',
    notes: '',
  };

  const updateGithubSupport = (patch: Partial<SupportSourceInput>) => {
    onSupportContextChange({
      ...supportContext,
      github: {
        enabled: githubSupport.enabled ?? false,
        profile: githubSupport.profile ?? '',
        notes: githubSupport.notes ?? '',
        ...patch,
      },
    });
  };

  const updateLinkedinSupport = (patch: Partial<SupportSourceInput>) => {
    onSupportContextChange({
      ...supportContext,
      linkedin: {
        enabled: linkedinSupport.enabled ?? false,
        profile: linkedinSupport.profile ?? '',
        notes: linkedinSupport.notes ?? '',
        ...patch,
      },
    });
  };

  const handleTextareaKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter') {
      e.stopPropagation();
    }
  };

  const handleContinue = useCallback(() => {
    if (isLast) {
      onFinish();
    } else {
      onNext();
    }
  }, [isLast, onFinish, onNext]);

  // Handle keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Enter without shift = next/finish (only if textarea not focused or ctrl/cmd held)
      if (e.key === 'Enter' && !e.shiftKey && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        handleContinue();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleContinue]);

  return (
    <div className="flex flex-col h-full min-h-[500px]">
      {/* Progress indicator */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-gray-500">
            {t('enrichment.questionProgress', { current: questionNumber, total: totalQuestions })}
          </span>
        </div>
        <div className="flex gap-1">
          {Array.from({ length: totalQuestions }).map((_, i) => (
            <div
              key={i}
              className={`h-1.5 w-6 transition-colors ${
                i < questionNumber
                  ? 'bg-black'
                  : i === questionNumber - 1
                    ? 'bg-black'
                    : 'bg-gray-200'
              }`}
            />
          ))}
        </div>
      </div>

      {/* Optional support sources */}
      <div className="mb-6 border-2 border-black bg-white p-4 shadow-[4px_4px_0px_0px_#000000]">
        <div className="mb-4">
          <h3 className="font-mono text-sm font-bold uppercase tracking-wider">
            Support Resume With Additional Data
          </h3>
          <p className="text-xs text-gray-600 mt-1 font-mono">
            Optionally include GitHub and LinkedIn context before generating resume updates.
          </p>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            <ToggleSwitch
              checked={githubSupport.enabled}
              onCheckedChange={(checked) => updateGithubSupport({ enabled: checked })}
              label="Use GitHub Data"
              description="Fetch public profile and repository signals."
            />

            {githubSupport.enabled && (
              <div className="space-y-3 border border-black bg-[#F8F8F4] p-3">
                <div>
                  <label className="mb-1 block font-mono text-xs uppercase tracking-wider text-black">
                    GitHub URL or Username
                  </label>
                  <Input
                    value={githubSupport.profile || ''}
                    onChange={(e) => updateGithubSupport({ profile: e.target.value })}
                    placeholder="github.com/username or username"
                  />
                </div>

                <div>
                  <label className="mb-1 block font-mono text-xs uppercase tracking-wider text-black">
                    GitHub Notes
                  </label>
                  <Textarea
                    value={githubSupport.notes || ''}
                    onChange={(e) => updateGithubSupport({ notes: e.target.value })}
                    onKeyDown={handleTextareaKeyDown}
                    placeholder="Paste achievements or project context from GitHub (optional)"
                    className="min-h-[100px] font-mono"
                  />
                </div>
              </div>
            )}
          </div>

          <div className="space-y-3">
            <ToggleSwitch
              checked={linkedinSupport.enabled}
              onCheckedChange={(checked) => updateLinkedinSupport({ enabled: checked })}
              label="Use LinkedIn Data"
              description="Use profile URL and additional details you provide."
            />

            {linkedinSupport.enabled && (
              <div className="space-y-3 border border-black bg-[#F8F8F4] p-3">
                <div>
                  <label className="mb-1 block font-mono text-xs uppercase tracking-wider text-black">
                    LinkedIn Profile URL
                  </label>
                  <Input
                    value={linkedinSupport.profile || ''}
                    onChange={(e) => updateLinkedinSupport({ profile: e.target.value })}
                    placeholder="linkedin.com/in/username"
                  />
                </div>

                <div>
                  <label className="mb-1 block font-mono text-xs uppercase tracking-wider text-black">
                    LinkedIn Notes
                  </label>
                  <Textarea
                    value={linkedinSupport.notes || ''}
                    onChange={(e) => updateLinkedinSupport({ notes: e.target.value })}
                    onKeyDown={handleTextareaKeyDown}
                    placeholder="Paste role highlights, promotions, or measurable outcomes from LinkedIn"
                    className="min-h-[100px] font-mono"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Item context badge */}
      {item && (
        <div className="mb-6">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-gray-100 border border-gray-200 text-sm font-mono">
            {item.item_type === 'experience' ? (
              <Briefcase className="w-4 h-4 text-gray-600" />
            ) : (
              <FolderKanban className="w-4 h-4 text-gray-600" />
            )}
            <span className="text-gray-600">
              {item.item_type === 'experience'
                ? t('enrichment.itemType.experience')
                : t('enrichment.itemType.project')}
              :
            </span>
            <span className="font-semibold text-gray-900">{item.title}</span>
            {item.subtitle && <span className="text-gray-500">@ {item.subtitle}</span>}
          </div>
        </div>
      )}

      {/* Question */}
      <div className="flex-1">
        <h2 className="text-2xl font-bold mb-6 leading-tight">{question.question}</h2>

        <Textarea
          ref={textareaRef}
          value={localAnswer}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleTextareaKeyDown}
          placeholder={question.placeholder}
          className="min-h-[180px] text-base resize-none font-mono"
        />

        <p className="text-xs text-gray-400 mt-2 font-mono">{t('enrichment.shortcutHint')}</p>
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between pt-6 border-t border-gray-200 mt-6">
        <Button variant="outline" onClick={onPrev} disabled={isFirst} className="gap-2">
          <ChevronLeft className="w-4 h-4" />
          {t('common.back')}
        </Button>

        <Button onClick={handleContinue} className="gap-2">
          {isLast ? (
            <>
              {t('common.finish')}
              <ChevronRight className="w-4 h-4" />
            </>
          ) : (
            <>
              {t('common.continue')}
              <ChevronRight className="w-4 h-4" />
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
