'use client';

import React, { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, AlertCircle, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { CoverLetterPreview, type CoverLetterPersonalInfo } from '@/components/builder/cover-letter-preview';
import { fetchResume } from '@/lib/api/resume';
import { useTranslations } from '@/lib/i18n';

export default function CoverLetterViewerPage() {
  const { t } = useTranslations();
  const params = useParams();
  const router = useRouter();
  const resumeId = params?.id as string;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resumeTitle, setResumeTitle] = useState<string | null>(null);
  const [coverLetter, setCoverLetter] = useState('');
  const [personalInfo, setPersonalInfo] = useState<CoverLetterPersonalInfo>({});

  useEffect(() => {
    if (!resumeId) {
      return;
    }

    const loadData = async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await fetchResume(resumeId);
        setResumeTitle(data.title ?? null);
        setCoverLetter(data.cover_letter ?? '');
        const info = data.processed_resume?.personalInfo;
        setPersonalInfo({
          name: info?.name,
          title: info?.title,
          email: info?.email,
          phone: info?.phone,
          location: info?.location,
          website: info?.website ?? undefined,
          linkedin: info?.linkedin ?? undefined,
          github: info?.github ?? undefined,
        });
      } catch (err) {
        console.error('Failed to load cover letter:', err);
        setError(t('resumeViewer.errors.failedToLoad'));
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [resumeId, t]);

  if (loading) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[#F0F0E8]">
        <Loader2 className="w-10 h-10 animate-spin text-blue-700 mb-4" />
        <p className="font-mono text-sm font-bold uppercase text-blue-700">{t('common.loading')}</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[#F0F0E8] p-4">
        <div className="border p-6 text-center max-w-md bg-red-50 border-red-200 shadow-[4px_4px_0px_0px_rgba(0,0,0,0.1)]">
          <div className="flex justify-center mb-4">
            <AlertCircle className="w-8 h-8 text-red-600" />
          </div>
          <p className="font-bold mb-4 text-red-700">{error}</p>
          <div className="flex flex-col gap-2">
            <Button variant="outline" onClick={() => router.push(`/resumes/${resumeId}`)}>
              {t('builder.previewTabs.resume')}
            </Button>
            <Button variant="outline" onClick={() => router.push('/dashboard')}>
              {t('nav.backToDashboard')}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#F0F0E8] py-12 px-4 md:px-8 overflow-y-auto">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 no-print">
          <Button variant="outline" onClick={() => router.push('/dashboard')}>
            <ArrowLeft className="w-4 h-4" />
            {t('nav.backToDashboard')}
          </Button>
          <div className="flex gap-3">
            <Button variant="outline" onClick={() => router.push(`/resumes/${resumeId}`)}>
              {t('builder.previewTabs.resume')}
            </Button>
            <Button variant="outline" onClick={() => router.push(`/cold-mails/${resumeId}`)}>
              {t('builder.previewTabs.outreach')}
            </Button>
          </div>
        </div>

        <div className="border-2 border-black bg-white p-6 shadow-[4px_4px_0px_0px_#000000]">
          <p className="font-mono text-xs uppercase text-blue-700 font-bold mb-2">
            {'// '}
            {t('dashboard.tailoredResume')}
          </p>
          <h1 className="font-serif text-3xl font-bold tracking-tight">
            {t('builder.previewTabs.coverLetter')}
          </h1>
          {resumeTitle && <p className="font-mono text-xs uppercase text-gray-600 mt-2">{resumeTitle}</p>}
        </div>

        <CoverLetterPreview content={coverLetter} personalInfo={personalInfo} />
      </div>
    </div>
  );
}
