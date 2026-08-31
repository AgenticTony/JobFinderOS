// Minimal ESLint wiring (HYGIENE): the repo ships eslint +
// eslint-config-next as devDependencies but had NO config file, so
// `next lint` (also removed as a command in Next 16) could never work.
// Flat config using the preset Next itself maintains — `npm run lint`
// now actually checks the app. tsc remains the type-check gate.
import nextConfig from 'eslint-config-next/core-web-vitals';

const config = [
  {
    ignores: ['.next/**', 'out/**', 'node_modules/**', 'next-env.d.ts'],
  },
  ...nextConfig,
  {
    rules: {
      // eslint-config-next v16 turns on the new React-hooks lint rules at
      // ERROR level; they flag deliberate, pre-existing patterns all over
      // this codebase (Date.now() during render for "new" badges and
      // posting ages, setState inside animation/observer effects). These
      // are downgraded to WARN so `npm run lint` passes while keeping the
      // findings visible — refactoring that render-purity debt is its own
      // change, not part of wiring the config up.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/purity': 'warn',
    },
  },
];

export default config;
