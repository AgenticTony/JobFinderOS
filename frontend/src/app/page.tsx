import type { Metadata } from 'next';

import LandingView from '@/components/landing/LandingView';
import { dicts } from '@/i18n/dict';

export const metadata: Metadata = {
  title: dicts.en.meta.title,
  description: dicts.en.meta.description,
  alternates: {
    languages: {
      en: '/',
      sv: '/sv',
    },
  },
};

export default function LandingPage() {
  return <LandingView locale="en" />;
}
