import type { Metadata } from 'next';
import PrivacyView from '@/components/PrivacyView';
import { dicts } from '@/i18n/dict';

// OPS-6: the GDPR Art. 13 notice in Swedish — a claim-for-claim
// translation of /privacy (src/i18n/privacy.ts).

export const metadata: Metadata = {
  title: dicts.sv.privacy.metaTitle,
  description: dicts.sv.privacy.metaDescription,
  alternates: { languages: { en: '/privacy', sv: '/sv/privacy' } },
};

export default function PrivacyPageSv() {
  return <PrivacyView locale="sv" />;
}
