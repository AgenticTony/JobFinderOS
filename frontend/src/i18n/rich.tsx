import { Fragment, type ReactNode } from 'react';

import { splitRich } from './richText.ts';

// Renders the dictionaries' `**emphasis**` convention. Translators keep
// emphasis inside the sentence (word order differs between languages)
// instead of the call site splitting a sentence around a <b>.
export function rich(text: string, strongClassName = 'text-hi'): ReactNode {
  return splitRich(text).map((p, i) =>
    p.strong ? (
      <span key={i} className={strongClassName}>
        {p.text}
      </span>
    ) : (
      <Fragment key={i}>{p.text}</Fragment>
    )
  );
}
