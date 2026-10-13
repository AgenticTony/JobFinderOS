import type { Metadata } from 'next';
import PrivacyView from '@/components/PrivacyView';
import { dicts } from '@/i18n/dict';

// OPS-6: the GDPR Art. 13 notice (English). Copy + source traces:
// src/i18n/privacy.ts. Swedish: /sv/privacy.

export const metadata: Metadata = {
  title: dicts.en.privacy.metaTitle,
  description: dicts.en.privacy.metaDescription,
  alternates: { languages: { en: '/privacy', sv: '/sv/privacy' } },
};

export default function PrivacyPage() {
  return <PrivacyView locale="en" />;
}
