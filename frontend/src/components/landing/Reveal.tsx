'use client';

// Scroll reveal, Apple-style: critically damped (no overshoot), once,
// and invisible to reduced-motion users and no-JS renderings alike.
// Content is visible in SSR output; the hidden state is only armed on
// the client, before the observer starts.

import { useEffect, useRef, useState, type ReactNode } from 'react';

export default function Reveal({
  children,
  className = '',
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [armed, setArmed] = useState(false);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    setArmed(true);
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShown(true);
          io.disconnect();
        }
      },
      { threshold: 0.12, rootMargin: '0px 0px -6% 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const hidden = armed && !shown;
  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: hidden ? 0 : undefined,
        transform: hidden ? 'translateY(26px)' : undefined,
        transition:
          'opacity 0.9s cubic-bezier(0.22, 1, 0.36, 1), transform 0.9s cubic-bezier(0.22, 1, 0.36, 1)',
        transitionDelay: armed && shown ? `${delay}ms` : undefined,
      }}
    >
      {children}
    </div>
  );
}
