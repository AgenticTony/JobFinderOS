import type { Metadata } from 'next';

import LandingView from '@/components/landing/LandingView';
import { dicts } from '@/i18n/dict';

// The Swedish marketing surface: same landing, Swedish dictionary.
// /sv never auto-redirects — the URL is an explicit language choice.

export const metadata: Metadata = {
  title: dicts.sv.meta.title,
  description: dicts.sv.meta.description,
  alternates: {
    languages: {
      en: '/',
      sv: '/sv',
    },
  },
};

export default function LandingPageSv() {
  return <LandingView locale="sv" />;
}
